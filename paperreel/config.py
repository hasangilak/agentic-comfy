"""Every tunable in one place, with the measurement behind each number."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ## Frame geometry
#
# H3's quality profile is ~1 megapixel and both dimensions must be multiples of 32,
# so exact 9:16 (1080x1920) is not directly renderable. 768x1344 is the closest
# multiple-of-32 vertical at ~1.03 MP; delivery scales to height 1920 and shaves
# 17 px of width to hit Instagram's frame exactly.
GEN_WIDTH, GEN_HEIGHT = 768, 1344
REEL_WIDTH, REEL_HEIGHT = 1080, 1920
FPS = 24

# ## Clip length
#
# The node snaps `length` onto a 17k+5 grid. Measured on RTX PRO 6000 at 8 steps:
#
#   124 frames (5.2s) -> 115s render, $0.024 per second of video   [proven]
#   243 frames (10.1s) -> 252s render, $0.028 per second of video  [proven]
#   362 frames (15.1s) -> never completed on this card             [UNPROVEN]
#
# Render time grows faster than linearly with frame count (video attention is
# quadratic in sequence length), so longer clips cost more per second of output.
# Pick length for how many seams you want, not to save money.
FRAME_GRID = 17
FRAME_GRID_OFFSET = 5
MIN_FRAMES = 124
PROVEN_MAX_FRAMES = 243

DEFAULT_STEPS = 8       # 20 steps costs ~70% more; 8 was judged good on paper art
DRAFT_STEPS = 8
DRAFT_SECONDS = 5.0

# ## Billing
#
# Modal list rates. A container is billed for GPU + requested cores + requested
# memory for its whole lifetime, so counting GPU alone understates by ~29%.
# Keep in sync with the @app.server decorator in comfyui_minimax_h3.py.
#
# Measured on an identical 4x124-frame, 8-step batch:
#   RTX PRO 6000  382 container-seconds  $0.42
#   B200          357 container-seconds  $0.80   (1.19x faster, 2.06x the rate)
# B200 is only worth it if a clip needs more than 96 GB.
GPU_RATE_PER_SEC = 0.000842
CPU_RATE_PER_CORE_SEC = 0.0000131
MEM_RATE_PER_GIB_SEC = 0.00000222
CONTAINER_CORES = 8
CONTAINER_GIB = 64
RATE_PER_SEC = (
    GPU_RATE_PER_SEC
    + CPU_RATE_PER_CORE_SEC * CONTAINER_CORES
    + MEM_RATE_PER_GIB_SEC * CONTAINER_GIB
)

# ## Deployment
APP_NAME = "comfyui-minimax-h3"
APP_FILE = ROOT / "comfyui_minimax_h3.py"
BACKEND_URL = os.environ.get(
    "PAPERREEL_BACKEND_URL",
    "https://gilak--comfyui-minimax-h3-comfyui.us-east.modal.direct",
)

# Set PAPERREEL_PUBLIC=1 before `modal deploy` to expose ComfyUI's browser UI without
# authentication. Off by default: the endpoint serves the full ComfyUI API, the model
# weights, and every render you have ever produced.
PUBLIC_ENDPOINT = os.environ.get("PAPERREEL_PUBLIC") == "1"

# ## Models
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

# ## Antigravity
PLANNER_MODEL = os.environ.get("PAPERREEL_PLANNER_MODEL", "gemini-3.6-flash-high")
IMAGE_MODEL = os.environ.get("PAPERREEL_IMAGE_MODEL", "gemini-3.6-flash-low")
AGY_HOME = Path.home() / ".gemini" / "antigravity-cli"

# ## Prompt scaffold
#
# H3 holds a paper-cutout look far better when the instruction pins down what must
# NOT change. Callers supply only the action; this wraps it.
STYLE_PREFIX = (
    "Single continuous locked-off shot in handcrafted layered paper-cutout stop-motion "
    "style, shot straight-on. Start exactly from the provided first frame. "
)
STYLE_SUFFIX = (
    " Animate it as real paper puppetry: crisp cut edges, visible paper grain, layered "
    "cardstock depth with soft contact shadows, joints pivoting like split-pin cutouts. "
    "Keep the character's face, proportions, colours, paper texture, decorative cut-paper "
    "details, outline weight, and scale identical in every frame. Keep the background, "
    "flowers, sun, clouds, lighting, and camera completely static. Nothing transforms, "
    "duplicates, slides, rotates, or changes design. No camera movement, no cuts, no new "
    "objects, no text, no watermarks. Smooth temporal consistency and natural foot contact."
)
AUDIO_SUFFIX = (
    " Audio: soft paper rustling and quiet birdsong in a sunny forest, no music, no speech."
)


def frame_count(seconds: float) -> int:
    """Snap a duration up onto the model's 17k+5 frame grid."""
    frames = max(MIN_FRAMES, round(seconds * FPS))
    return frames + (FRAME_GRID_OFFSET - frames % FRAME_GRID) % FRAME_GRID


def build_prompt(action: str, *, mute: bool = False) -> str:
    prompt = STYLE_PREFIX + action.strip().rstrip(".") + "." + STYLE_SUFFIX
    return prompt if mute else prompt + AUDIO_SUFFIX


def estimate_cost(container_seconds: float) -> float:
    return container_seconds * RATE_PER_SEC
