# # MiniMax-H3 on ComfyUI, single GPU
#
# The cheap path. ComfyUI's native H3 support (0.30.0+) prunes the modulation
# weights into lookup tables and quantizes the rest to int8 / NVFP4, taking the
# pipeline from 123.6 GB of VRAM down to ~42.5 GB. That fits on ONE GPU, so this
# runs at roughly a sixth of the 4x H200 hourly rate -- at the cost of speed and
# some quality, since the weights are lossily quantized.
#
#   uvx modal setup
#   uvx modal run   comfyui_minimax_h3.py::download_models   # once, ~40 GiB
#   uvx modal serve comfyui_minimax_h3.py                    # interactive UI
#   uvx modal deploy comfyui_minimax_h3.py                   # persistent URL
#
# Compare with minimax_h3.py, which serves the full BF16/FP32 pipeline on 4x H200
# via SGLang. That one is faster and lossless; this one is cheap.

import os
import subprocess
from pathlib import Path

import modal

# Deploy-time switch, read on the machine running `modal deploy`. See the
# `unauthenticated=` argument below for why this defaults to off.
PUBLIC_ENDPOINT = os.environ.get("PAPERREEL_PUBLIC") == "1"

# ## GPU choice
#
# RTX PRO 6000 Blackwell: 96 GB VRAM at $0.000842/GPU-sec, the cheapest card on
# Modal with enough memory *and* the right architecture. It matters that it's
# Blackwell -- the NVFP4 text encoder below needs sm_120 to run natively.
#
# Modal does not offer the RTX 5090. This is the closest equivalent and has 3x
# the VRAM, which is why we can skip the aggressive layerwise-offload recipe the
# 5090 needs.
GPU = "RTX-PRO-6000"
# Measured against B200 on an identical 4x124-frame, 8-step batch: B200 rendered a
# steady-state beat in 75s vs 89s here (1.19x faster) but costs 2.06x per second,
# so the same batch came to $0.80 on B200 against $0.42 here. The quantization is
# tuned for sm_120 and a single-stream diffusion job never touches B200's extra
# bandwidth or NVLink. Only revisit B200 if clip length needs its 180 GB.

# ## Model files
#
# The ComfyUI-repacked, quantized weights. `pruned_int8_convrot` is the
# diffusion model with modulation-weight pruning plus int8 convolutions;
# `nvfp4_awq` is the 32B Qwen3-VL text encoder in 4-bit.
#
# Both diffusion checkpoints ship, because H3 splits the tasks between them:
#
#   fl2va  -- text / first frame / last frame, i.e. every keyframe join
#   ref2va -- reference conditioning: up to 9 images (MiniMaxH3ReferenceToVideo), and
#             no keyframe inputs at all
#
# Only one is resident at a time -- ComfyUI loads whatever the queued graph names and evicts
# the other -- so the second checkpoint costs Volume space and one model swap per switch,
# not VRAM. A reel that never uses references never loads it.
MODEL_REPO = "Comfy-Org/MiniMax-H3"
MODEL_FILES = [
    # (repo path, ComfyUI models/ subdirectory)
    ("diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors", "diffusion_models"),
    ("diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors", "diffusion_models"),
    ("text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", "text_encoders"),
    ("vae/minimax_h3_video_vae_fp16.safetensors", "vae"),
    ("vae/minimax_h3_audio_vae_fp32.safetensors", "vae"),
]
# ~59.1 GiB total (19.5 GiB of it the second checkpoint). On a non-Blackwell card
# (L40S, A100) NVFP4 has no hardware support -- swap the text encoder for
# qwen3vl_32b_minimax_h3_int8_convrot (25.3 GiB), which pushes the total to ~70 GiB and
# needs an 80 GB card.

COMFYUI_VERSION = "0.30.0"  # first stable release with native H3 nodes
PYTORCH_VERSION = "2.11.0+cu130"
TORCHVISION_VERSION = "0.26.0+cu130"
TORCHAUDIO_VERSION = "2.11.0+cu130"
CACHE_PATH = "/cache"
COMFY_MODELS_PATH = "/root/comfy/ComfyUI/models"
COMFY_INPUT_PATH = "/root/comfy/ComfyUI/input"
COMFY_WORKFLOW_PATH = "/root/comfy/ComfyUI/user/default/workflows"
COMFY_OUTPUT_PATH = "/outputs"
PORT = 8000
MINUTES = 60

LOCAL_ROOT = Path(__file__).parent

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "ffmpeg")
    .uv_pip_install("comfy-cli", "huggingface_hub>=0.34,<2")
    .run_commands(
        f"comfy --skip-prompt install --nvidia --version {COMFYUI_VERSION}"
    )
    # comfy-cli currently resolves a CUDA 12.6 nightly on this base image.
    # Blackwell (sm_120) needs CUDA 13 kernels; without them even a basic
    # embedding lookup fails with cudaErrorNoKernelImageForDevice.
    .uv_pip_install(
        f"torch=={PYTORCH_VERSION}",
        f"torchvision=={TORCHVISION_VERSION}",
        f"torchaudio=={TORCHAUDIO_VERSION}",
        index_url="https://download.pytorch.org/whl/cu130",
    )
    .uv_pip_install("comfy-kitchen[cublas]==0.2.26")
    .env({"HF_XET_HIGH_PERFORMANCE": "1", "HF_HOME": CACHE_PATH})
    # Make the supplied art and a customized official H3 workflow immediately
    # available in the remote UI. These are runtime mounts, so changing a prompt
    # or image does not rebuild the large ComfyUI image layer.
    .add_local_dir(LOCAL_ROOT / "characters", COMFY_INPUT_PATH)
    .add_local_file(
        LOCAL_ROOT / "workflows" / "pig_walk_minimax_h3_i2v.json",
        f"{COMFY_WORKFLOW_PATH}/pig_walk_minimax_h3_i2v.json",
    )
)

cache_vol = modal.Volume.from_name("comfyui-minimax-h3-cache", create_if_missing=True)
output_vol = modal.Volume.from_name("minimax-h3-outputs", create_if_missing=True)

app = modal.App("comfyui-minimax-h3")


# ## Download the weights
#
# Into a Volume, once. Keeping this out of the server's startup path means cold
# starts don't re-pay for 40 GiB of transfer.


@app.function(
    image=image,
    volumes={CACHE_PATH: cache_vol},
    timeout=2 * 60 * MINUTES,
    cpu=8,
)
def download_models():
    from huggingface_hub import hf_hub_download

    for repo_path, _ in MODEL_FILES:
        path = hf_hub_download(MODEL_REPO, repo_path)
        print(f"cached {repo_path} -> {path}")
    cache_vol.commit()


def link_models():
    """Symlink cached weights into the tree ComfyUI scans."""
    from pathlib import Path

    from huggingface_hub import hf_hub_download

    for repo_path, subdir in MODEL_FILES:
        src = Path(hf_hub_download(MODEL_REPO, repo_path))
        dst_dir = Path(COMFY_MODELS_PATH) / subdir
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        if not dst.exists():
            dst.symlink_to(src)
            print(f"linked {dst}")


# ## The server
#
# `uvx modal serve` gives you the ComfyUI web UI on a temporary URL -- load one
# of the six bundled H3 workflow templates from the sidebar and generate
# interactively. `uvx modal deploy` gives the same thing on a stable URL, which
# also accepts ComfyUI's HTTP API at POST /prompt if you'd rather drive it
# programmatically.


@app.server(
    image=image,
    gpu=GPU,
    cpu=8,
    memory=65536,
    volumes={
        CACHE_PATH: cache_vol,
        COMFY_OUTPUT_PATH: output_vol,
    },
    port=PORT,
    startup_timeout=10 * MINUTES,
    scaledown_window=5 * MINUTES,
    # ComfyUI holds one workflow's models in VRAM; serialize requests per replica
    # and add replicas for concurrent users.
    target_concurrency=1,
    max_containers=1,
    # Authenticated by default. The endpoint serves the full ComfyUI API, the model
    # weights, and every render in the output Volume, so a public URL is a real
    # exposure. Programmatic clients send Modal-Key/Modal-Secret headers
    # (paperreel.comfy.modal_auth_headers reads them from ~/.modal.toml).
    #
    # A browser cannot attach those headers to the UI's WebSocket requests, so to
    # poke at ComfyUI interactively, deploy a throwaway public instance:
    #     PAPERREEL_PUBLIC=1 uvx modal deploy comfyui_minimax_h3.py
    # and re-deploy without it when finished.
    unauthenticated=PUBLIC_ENDPOINT,
)
class ComfyUI:
    @modal.enter()
    def start(self):
        link_models()
        cmd = [
            "comfy",
            "launch",
            "--",
            "--listen",
            "0.0.0.0",
            "--port",
            str(PORT),
            "--output-directory",
            COMFY_OUTPUT_PATH,
            "--disable-auto-launch",
        ]
        print("launching:", " ".join(cmd))
        self.process = subprocess.Popen(cmd)
