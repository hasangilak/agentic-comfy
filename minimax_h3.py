# # MiniMax-H3 audio + video generation on Modal
#
# MiniMax-H3 is not a text LLM. It is a 33B dense omni-transformer that emits an
# H.264 video (24 fps, up to 2K) plus a synchronized AAC stereo track (32 kHz)
# from a single request. We serve it with SGLang Diffusion, which exposes an
# async submit / poll / download API at /v1/videos.
#
#   uvx modal setup                                  # once, to authenticate
#   uvx modal run    minimax_h3.py::download_weights # once, ~134 GiB
#   uvx modal deploy minimax_h3.py                   # bring up the server
#   uvx modal run    minimax_h3.py                   # generate a test clip

import modal

MODEL_ID = "MiniMaxAI/MiniMax-H3"

# `fl2va` weights serve the `t2va` (text-only) and `fl2va` (first / last frame)
# tasks. `ref2va` weights serve reference-conditioned requests: image, audio and
# video references, including video-to-video. Pick one -- they are separate
# checkpoints and the server loads a single partition.
#
# SGLang owns the checkpoint-to-subdirectory mapping, so we always pass the root
# repo id to --model-path and select the partition with --model-variant.
MODEL_VARIANT = "fl2va"
VARIANT_DIR = {"fl2va": "FL2VA", "ref2va": "Ref2VA"}[MODEL_VARIANT]

# ## Container image
#
# We start from the SGLang dev image, which already carries a matched
# torch / flash-attn / CUDA stack. That base ships without the optional diffusion
# extras, so we install them from the source bundled in the image -- the same
# step the upstream Docker recipe does at runtime, hoisted into a build layer so
# it costs nothing on a cold start.

image = (
    modal.Image.from_registry("lmsysorg/sglang:dev", add_python=None)
    .entrypoint([])
    .apt_install("ffmpeg")  # muxes the H.264 video and AAC audio into one MP4
    .run_commands('pip install -e "/sgl-workspace/sglang/python[diffusion]"')
    .env(
        {
            "HF_XET_HIGH_PERFORMANCE": "1",  # faster weight transfers
            "HF_HOME": "/root/.cache/huggingface",
        }
    )
)

# ## Storage
#
# Volumes are shared disks that persist between runs. We use three:
# one caches the weights so we download them once rather than on every boot, one
# holds conditioning inputs the server reads as `file:///data/minimax-h3/...`,
# and one collects finished MP4s.

hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
media_vol = modal.Volume.from_name("minimax-h3-media", create_if_missing=True)
output_vol = modal.Volume.from_name("minimax-h3-outputs", create_if_missing=True)

HF_CACHE_PATH = "/root/.cache/huggingface"
MEDIA_PATH = "/data/minimax-h3"
OUTPUT_PATH = "/outputs"

app = modal.App("minimax-h3")

MINUTES = 60
PORT = 30010

# ## Pre-download the weights
#
# The full repo is ~464 GiB because it ships three packagings of the pipeline.
# A single variant partition is self-contained at ~134 GiB, so we fetch only the
# one we serve. Run this once; it writes into the shared Volume.


@app.function(
    image=image,
    volumes={HF_CACHE_PATH: hf_cache_vol},
    timeout=6 * 60 * MINUTES,
    cpu=16,
    # Uncomment if the model card starts gating access behind accepted terms:
    # secrets=[modal.Secret.from_name("huggingface-secret")],
)
def download_weights():
    from huggingface_hub import snapshot_download

    path = snapshot_download(
        MODEL_ID,
        allow_patterns=[f"{VARIANT_DIR}/**", "*.json", "*.md", "LICENSE"],
        max_workers=16,
    )
    print(f"cached variant {MODEL_VARIANT!r} at {path}")


# ## The server
#
# The topologies below are the ones MiniMax and SGLang verified on real hardware.
# On 4x H200 (141 GB each) the whole BF16/FP32 pipeline stays resident under pure
# Ulysses-4 sequence parallelism, peaking near 94 GB/GPU -- both the fastest and
# the simplest option, and the default here.

GPU_RECIPES = {
    # ~74 s for a 5 s 1344x768 50-step clip, warmed up.
    "H200:4": ["--num-gpus", "4", "--ulysses-degree", "4"],
    # On 80 GB H100s pure Ulysses-4 does not fit; shard the DiT weights with TP2.
    "H100:4": ["--num-gpus", "4", "--tp-size", "2", "--ulysses-degree", "2"],
    # Add --quantization fp8 to roughly halve peak memory. That path is
    # approximate -- check video *and* audio quality before relying on it.
    "B200:8": ["--num-gpus", "8", "--ulysses-degree", "8"],
}

GPU = "H200:4"

# Matching warmup to the resolution you actually serve removes about 10 s of
# cold-first-request cost.
WARMUP_RESOLUTION = "1344x768"


@app.server(
    image=image,
    gpu=GPU,
    volumes={
        HF_CACHE_PATH: hf_cache_vol,
        MEDIA_PATH: media_vol,
    },
    port=PORT,
    # Weight load is ~2 min once cached, plus ~30 s of warmup.
    startup_timeout=20 * MINUTES,
    scaledown_window=10 * MINUTES,
    # One clip at a time. The verified recipes all run batching_max_size 1, and a
    # single request already saturates every GPU in the replica.
    target_concurrency=1,
    max_containers=1,
    unauthenticated=True,  # drop this to require Modal auth headers
)
class Server:
    @modal.enter()
    def start(self):
        import subprocess

        cmd = [
            "sglang",
            "serve",
            "--model-path",
            MODEL_ID,
            "--model-variant",
            MODEL_VARIANT,
            *GPU_RECIPES[GPU],
            # `speed` keeps every component resident. It requires the pipeline to
            # fit; use `memory` if you move to smaller cards.
            "--performance-mode",
            "speed",
            "--warmup-resolutions",
            WARMUP_RESOLUTION,
            "--max-concurrency",
            "1",
            "--host",
            "0.0.0.0",
            "--port",
            str(PORT),
        ]

        print("launching:", " ".join(cmd))
        self.process = subprocess.Popen(cmd)


# ## Generating a clip
#
# Generation is asynchronous: POST returns an id immediately, then you poll until
# the status flips to `completed` and download the bytes. This function runs the
# whole loop inside Modal and writes the MP4 to the outputs Volume.

DEFAULT_PROMPT = (
    "At night, while their owner sleeps in a bedroom, three cats march in "
    "loudly playing tiny brass instruments, then abruptly file out."
)


@app.function(
    image=modal.Image.debian_slim(python_version="3.12").pip_install("requests"),
    volumes={OUTPUT_PATH: output_vol},
    timeout=60 * MINUTES,
)
def generate(prompt: str, seconds: float = 5.0, seed: int = 1101) -> str:
    import time

    import requests

    base = Server.get_url()
    payload = {
        "model": MODEL_ID,
        "prompt": prompt,
        "task": "t2va",
        "conditions": [],
        "target": {
            "short_edge": 768,
            "aspect_ratio": "16:9",
            "duration_seconds": seconds,  # 4 to 15 seconds, inclusive
        },
        "num_outputs_per_prompt": 1,
        "num_inference_steps": 50,
        "flow_shift": 12.0,
        "audio_flow_shift": 3.0,
        "seed": seed,
    }

    submit = requests.post(f"{base}/v1/videos", json=payload, timeout=600)
    submit.raise_for_status()
    video_id = submit.json()["id"]
    print(f"submitted {video_id}")

    while True:
        status = requests.get(f"{base}/v1/videos/{video_id}", timeout=120).json()["status"]
        print(f"  status={status}")
        if status == "completed":
            break
        if status == "failed":
            raise RuntimeError(f"generation failed for {video_id}")
        time.sleep(5)

    content = requests.get(f"{base}/v1/videos/{video_id}/content", timeout=600).content
    out = f"{OUTPUT_PATH}/{video_id}.mp4"
    with open(out, "wb") as f:
        f.write(content)
    output_vol.commit()

    print(f"wrote {out} ({len(content) / 1e6:.1f} MB)")
    return video_id


@app.local_entrypoint()
def main(prompt: str = DEFAULT_PROMPT, seconds: float = 5.0, seed: int = 1101):
    video_id = generate.remote(prompt, seconds=seconds, seed=seed)
    print(f"\ndownload it with:  uvx modal volume get minimax-h3-outputs {video_id}.mp4")
