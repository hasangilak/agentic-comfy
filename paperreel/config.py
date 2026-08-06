"""Every tunable in one place, with the measurement behind each number."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path | None = None) -> list[str]:
    """Read KEY=value lines from .env into the environment. Returns the keys it set.

    Everything here reads os.environ, and nothing was reading the file, so a .env holding
    the Modal proxy tokens left the studio starting with no credentials at all. That failure
    is expensive in the worst way: it surfaces as a 401 from ComfyUI *after* the container is
    deployed and already billing, rather than before anything is spent.

    setdefault, not assignment: a variable exported in the shell wins over the file, which
    is what anyone overriding one for a single run expects. Called at import so the CLIs and
    the studio all pick it up without each one remembering to.
    """
    path = path or ROOT / ".env"
    try:
        text = path.read_text()
    except OSError:  # absent or unreadable is the normal case, not an error
        return []
    loaded = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


ENV_FILE_KEYS = load_env_file()

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

# The studio offers exactly these two lengths and nothing else. They are not arbitrary:
# 5s is the model's 124-frame floor, and 10s snaps to 243 frames -- exactly
# PROVEN_MAX_FRAMES. So the longest beat the UI can build is the longest one that has ever
# completed on this card, and the 362-frame render that failed is unreachable by
# construction rather than by warning.
BEAT_LENGTHS = (5.0, 10.0)

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

# ## Predicting render time
#
# Fitted through the two proven measurements of steady-state per-beat render time on
# RTX PRO 6000 at 8 steps:
#
#   124 frames -> 89s     (382 container-seconds for 4 beats, minus overhead)
#   243 frames -> 259s    (1036 container-seconds for 4 beats, minus overhead)
#
# The intercept is NEGATIVE, which is the whole story: time grows faster than linearly
# with frame count, so a longer clip costs more per second of finished video. Do not
# "optimise" by making clips longer.
SECONDS_PER_FRAME = 1.4286
RENDER_INTERCEPT = -88.1
# Boot from a warm image plus the one-time model load off the Volume. Paid once per
# container no matter how many beats ride along, which is the argument for batching.
CONTAINER_OVERHEAD_SECONDS = 42.0

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
#
# Two diffusion checkpoints, because H3 splits the tasks across them and a graph loads one:
#
#   fl2va  -- text / first frame / last frame. Every join built on keyframes.
#   ref2va -- reference conditioning: up to 9 images, and no keyframe inputs at all.
#
# They are 19.5 GiB each, so keeping both on the Volume costs disk, not VRAM: ComfyUI loads
# whichever the graph asks for and evicts the other. A batch that mixes joins therefore pays
# one model swap per switch, which is why the studio orders nothing specially -- a reel that
# is all keyframes never touches ref2va at all.
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
UNET_REF = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

# ## Reference conditioning
#
# MiniMaxH3ReferenceToVideo grows one input socket per reference and stops at nine images
# (`ref_images.ref_image_0` .. `ref_image_8` in the API graph). The other three sockets --
# videos, their soundtracks, standalone audio -- cap at three each and are not wired here.
#
# The prompt refers to them as <Picture 1>..<Picture 9>, 1-based and in connection order,
# which is the tag the text encoder is trained on. Off-by-one matters: image N in the graph
# is <Picture N+1> in the prompt.
MAX_REF_IMAGES = 9
# "match" scales each reference down to the generation's pixel area; "max" uses the reference
# pipeline's 2048px short edge for better identity fidelity. Reference tokens ride through
# every sampling step, so "max" can be several times slower -- and slower here is money.
REF_IMAGE_SIZE = "match"

# ## Antigravity
PLANNER_MODEL = os.environ.get("PAPERREEL_PLANNER_MODEL", "gemini-3.6-flash-high")
IMAGE_MODEL = os.environ.get("PAPERREEL_IMAGE_MODEL", "gemini-3.6-flash-low")
AGY_HOME = Path.home() / ".gemini" / "antigravity-cli"

# ## Prompt scaffold
#
# H3 holds a paper-cutout look far better when the instruction pins down what must
# NOT change. Callers supply the beat's scene and action, the board's identity paragraph,
# and which kind of join this beat opens on; this assembles the rest.
#
# The join is the part that matters most, because the same first frame means two opposite
# things. On a cut it is the deliberate opening composition of a new shot. On a
# continuation it is a freeze lifted out of the middle of a take that is already moving.
# Told the wrong one -- as every beat was, when both got "start exactly from the provided
# first frame" -- the model reads a mid-stride pose as a starting pose, settles the subject
# back to rest and begins the action again. That restart is the jolt at a seam, and no
# amount of frame-handoff accuracy fixes it.
MEDIUM = (
    "Single continuous locked-off shot in handcrafted layered paper-cutout stop-motion "
    "style, shot straight-on. "
)
OPEN_CUT = (
    "The provided first frame is the opening frame of a new shot: begin from it exactly, "
    "and hold its framing, subject scale and lighting for the whole clip. "
)
OPEN_CONTINUATION = (
    "The provided first frame is a freeze grabbed from the middle of a take that is "
    "already in motion -- it is not a new setup and not a rest pose. Carry that motion on "
    "from exactly this pose, at the same speed and in the same direction, as one unbroken "
    "take. Do not restart the shot, do not re-pose or re-centre the subject, do not let it "
    "settle to rest and start again, and do not re-establish the scene: same set, same "
    "camera, same lighting, same moment continuing. "
)
# The reference join has no keyframe at all: ref2va conditions on pictures of the cast and the
# set, and the opening composition is the model's to build. That has to be said explicitly or
# the model reads nine supplied images as nine shots and cuts between them -- which is the one
# thing this production never does. Formatted with the <Picture i> tag list at build time.
OPEN_REFERENCE = (
    "No first frame is provided. Instead {tags} are supplied as design references: they fix "
    "what the characters, the set and the materials look like, and nothing else. Reproduce "
    "every subject that appears in them exactly -- same shapes, markings, colours, "
    "proportions, paper texture, cut edges and palette -- and compose the opening frame "
    "yourself from the scene line below. The references are not shots: do not show them, do "
    "not cut between them, do not pan across them, and do not put more than one version of a "
    "character on screen. "
)
# H3 takes a last frame as well as a first, and this is what has to be said when both are
# supplied. Without it the model treats the second image as another shot to cut to, and the
# clip arrives there early and then sits still -- or worse, jumps. Said this way, the two
# stills become the two ends of one move and the beat is the move between them.
ARRIVE_ON_LAST = (
    "A final frame is provided as well: this take must arrive at exactly that composition, "
    "and reach it only on its very last frame. Treat it as the pose, position and framing "
    "this same continuous move settles into at the end -- not a different shot, not somewhere "
    "to jump to, and not somewhere to arrive early and then wait. Everything between the two "
    "provided frames is one unbroken take at an even, unhurried pace, and the set, camera and "
    "lighting are identical in both, so nothing but the moving subject may differ between "
    "them. "
)
# The style bible, verbatim. Over 5-10 seconds of sampling a generic "keep the character
# identical" has nothing to hold on to, so the model drifts towards its own idea of a
# paper fox rather than the one this board designed.
IDENTITY_PREFIX = (
    "The characters and the set are already designed and must not be reinterpreted: "
)
# The beat's own scene line. The style bible says what the production looks like everywhere;
# this says where THIS shot is and at what scale, which is the part that differs between
# beats and the part the model otherwise has to guess at from one still. Labelled rather
# than woven into a sentence, because a scene line is sometimes a place ("a cobblestone
# street at twilight") and sometimes a framing ("macro close-up of the lantern housing"),
# and no single connecting phrase reads correctly for both.
SCENE_PREFIX = "Scene: "
CRAFT = (
    " Animate it as real paper puppetry: crisp cut edges, visible paper grain, layered "
    "cardstock depth with soft contact shadows, joints pivoting like split-pin cutouts. "
    "Keep every character's face, markings, proportions, colours, paper texture, "
    "decorative cut-paper details, outline weight, and scale identical in every frame. "
    # Deliberately generic. This used to name the flowers, sun and clouds of the board it
    # was written for, which on a board without them is an instruction to invent them.
    "Keep the set, the background layers, the lighting, and the camera completely static. "
    "Nothing transforms, duplicates, slides, rotates, or changes design. No camera "
    "movement, no cuts, no new objects, no text, no watermarks. Smooth temporal "
    "consistency and natural foot contact."
)
AUDIO_SUFFIX = (
    " Audio: soft paper rustling and quiet birdsong in a sunny forest, no music, no speech."
)


def frame_count(seconds: float) -> int:
    """Snap a duration up onto the model's 17k+5 frame grid."""
    frames = max(MIN_FRAMES, round(seconds * FPS))
    return frames + (FRAME_GRID_OFFSET - frames % FRAME_GRID) % FRAME_GRID


def snap_seconds(value: float | int | str) -> float:
    """Force a duration onto one of the two offered lengths.

    Applied on the way in AND on the way out, so a hand-edited storyboard or an older
    board with some other number still presents as one of the two the UI can show.
    """
    try:
        wanted = float(value)
    except (TypeError, ValueError):
        return BEAT_LENGTHS[-1]
    return min(BEAT_LENGTHS, key=lambda option: abs(option - wanted))


def reference_tags(count: int) -> str:
    """The prompt's name for the supplied references: "<Picture 1>, <Picture 2> and <Picture 3>".

    1-based and in connection order, which is what the text encoder was trained on. The graph
    sockets are 0-based, so this deliberately does not match the key names in comfy.build_graph.
    """
    tags = [f"<Picture {i}>" for i in range(1, max(0, count) + 1)]
    if len(tags) <= 1:
        return "".join(tags)
    return ", ".join(tags[:-1]) + " and " + tags[-1]


def build_prompt(action: str, *, scene: str = "", mute: bool = False, identity: str = "",
                 continues: bool = False, lands: bool = False, refs: int = 0) -> str:
    """Assemble the instruction for one beat.

    `identity` is the board's style bible -- what the characters and the set look like,
    never how they move. `scene` is the beat's own line: where it happens and at what
    scale. `action` is what moves. All three go in: the action alone leaves the model to
    infer the setting from a single still, which is where a background quietly turns into
    a different place halfway through a clip. `continues` says this beat opens on the
    previous clip's final frame rather than on a still of its own, which changes how the
    first frame must be read; see the scaffold above. `lands` says a final frame was given
    too, so the clip has a destination it must reach and not overshoot.

    `refs` is how many reference pictures this beat is conditioned on instead of a keyframe.
    Non-zero puts the beat on the ref2va checkpoint, which has no first or last frame inputs
    at all -- so `continues` and `lands` cannot apply and are ignored rather than silently
    describing frames the model was never given.
    """
    if refs > 0:
        parts = [MEDIUM, OPEN_REFERENCE.format(tags=reference_tags(refs))]
    else:
        parts = [MEDIUM, OPEN_CONTINUATION if continues else OPEN_CUT]
        if lands:
            parts.append(ARRIVE_ON_LAST)
    identity = " ".join(identity.split())
    if identity:
        parts.append(IDENTITY_PREFIX + identity.rstrip(".") + ". ")
    scene = " ".join(scene.split()).strip().rstrip(".")
    if scene:
        parts.append(SCENE_PREFIX + scene + ". ")
    action = action.strip().rstrip(".")
    if action:
        parts.append(action + ".")
    parts.append(CRAFT)
    if not mute:
        parts.append(AUDIO_SUFFIX)
    return "".join(parts)


def estimate_cost(container_seconds: float) -> float:
    return container_seconds * RATE_PER_SEC


def predict_render_seconds(frames: int, *, steps: int = DEFAULT_STEPS) -> float:
    """How long one beat should take, before it starts.

    Scaled linearly by step count off the 8-step measurements. Rough for other step
    counts -- only 8 and 20 were ever measured -- but the UI recalibrates from the first
    completed beat, so the initial guess only has to be in the right neighbourhood.
    """
    predicted = SECONDS_PER_FRAME * frames + RENDER_INTERCEPT
    return max(20.0, predicted) * (steps / DEFAULT_STEPS)


def predict_batch_seconds(frame_counts: list[int], *, steps: int = DEFAULT_STEPS) -> float:
    """Wall-clock for a batch, including the once-per-container overhead."""
    if not frame_counts:
        return 0.0
    return CONTAINER_OVERHEAD_SECONDS + sum(
        predict_render_seconds(f, steps=steps) for f in frame_counts
    )
