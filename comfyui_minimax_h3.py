# # MiniMax-H3 on ComfyUI, single GPU
#
# Unpruned BF16 DiT + BF16 Qwen3-VL-32B encoder. ~123.6 GB of weights, one DiT
# resident at a time (~115 GB with encoder and VAEs). That is the same checkpoint
# MiniMax released, repacked as ComfyUI single-files -- no pruning, no int8, no
# NVFP4. It needs a 180 GB card.
#
#   uvx modal setup
#   uvx modal run   comfyui_minimax_h3.py::download_models   # once, ~177 GiB
#   uvx modal serve comfyui_minimax_h3.py                    # interactive UI
#   uvx modal deploy comfyui_minimax_h3.py                   # persistent URL
#
# minimax_h3.py is the faster 4x H200 SGLang path. It speaks /v1/videos;
# paperreel's graph client cannot, so this file is the one the studio uses.

import os
import subprocess
from pathlib import Path

import modal

# Deploy-time switch, read on the machine running `modal deploy`. See the
# `unauthenticated=` argument below for why this defaults to off.
PUBLIC_ENDPOINT = os.environ.get("PAPERREEL_PUBLIC") == "1"

# ## GPU choice
#
# B200: 180 GB at $0.001736/GPU-sec, the cheapest Modal card that holds unpruned
# BF16. 96 GB cannot. A fl2va/ref2va swap that keeps both DiTs even briefly peaks
# near 177 GB -- that is the OOM risk, and why this is not an RTX PRO 6000 job.
# Escape hatch is B300 (288 GB, CUDA 13.1+), not 4x H200. Wall-clock on this
# stack is unmeasured; do not quote the old quantized RTX PRO 6000 timings.
GPU = "B200"

# ## Model files
#
# ComfyUI-repacked unpruned BF16 of MiniMaxAI/MiniMax-H3. Same weights as the
# official checkpoint, one file per piece.
#
# Both diffusion checkpoints ship, because H3 splits the tasks between them:
#
#   fl2va  -- text / first frame / last frame, i.e. every keyframe join
#   ref2va -- reference conditioning: up to 9 images and 3 videos
#             (MiniMaxH3ReferenceToVideo), and no keyframe inputs at all
#
# Only one is resident at a time -- ComfyUI loads whatever the queued graph names and evicts
# the other -- so the second checkpoint costs Volume space and one model swap per switch,
# not VRAM. A reel that never uses references never loads it. Resident is one DiT
# plus the encoder and VAEs (~115 GB).
MODEL_REPO = "Comfy-Org/MiniMax-H3"
MODEL_FILES = [
    # (repo path, ComfyUI models/ subdirectory)
    ("diffusion_models/minimax_h3_fl2va_bf16.safetensors", "diffusion_models"),
    ("diffusion_models/minimax_h3_ref2va_bf16.safetensors", "diffusion_models"),
    ("text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors", "text_encoders"),
    ("vae/minimax_h3_video_vae_fp16.safetensors", "vae"),
    ("vae/minimax_h3_audio_vae_fp32.safetensors", "vae"),
]
# ~177 GiB total (61.7 GiB of it the second checkpoint).

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


def _load_dotenv(path: Path) -> None:
    """Same setdefault rule as paperreel.config: a shell export wins."""
    try:
        text = path.read_text()
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(LOCAL_ROOT / ".env")


def _hf_secrets() -> list:
    # huggingface_hub runs in the remote container, which does not inherit the
    # laptop's env. A token is rate-limit headroom for a 177 GiB public pull,
    # not gated access -- Comfy-Org/MiniMax-H3 is public either way.
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if not token:
        return []
    return [modal.Secret.from_dict({"HF_TOKEN": token})]


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
# starts don't re-pay for 177 GiB of transfer.


@app.function(
    image=image,
    volumes={CACHE_PATH: cache_vol},
    timeout=6 * 60 * MINUTES,
    cpu=16,
    secrets=_hf_secrets(),
)
def download_models():
    from huggingface_hub import hf_hub_download

    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"):
        print("huggingface: authenticated")
    else:
        print("huggingface: anonymous -- put HF_TOKEN=hf_... in .env for higher rate limits")
    for repo_path, _ in MODEL_FILES:
        path = hf_hub_download(MODEL_REPO, repo_path)
        print(f"cached {repo_path} -> {path}")
        # Commit per file so a laptop sleep does not throw away a 62 GiB pull
        # that already finished. huggingface_hub resumes the rest.
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
    memory=131072,
    secrets=_hf_secrets(),
    volumes={
        CACHE_PATH: cache_vol,
        COMFY_OUTPUT_PATH: output_vol,
    },
    port=PORT,
    startup_timeout=20 * MINUTES,
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
