"""Every tunable in one place, with the measurement behind each number."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The agent skills, as `<name>/SKILL.md`. Package-relative rather than under ROOT because a
# SKILL.md is one agent's system prompt and the code cannot run without it -- the same
# relationship `agent.SYSTEM` has to `agent.py`. `prompts/40s-stop-motion-script.md` is
# top-level for the opposite reason: it is a document a human pastes into an outside AI.
SKILLS_DIR = Path(os.environ.get("PAPERREEL_SKILLS")
                  or Path(__file__).resolve().parent / "skills")


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
# The node snaps `length` onto a 17k+5 grid. Measured on RTX PRO 6000 at 8 steps
# with quantized weights (the card this stack no longer uses):
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

# Camera angle for one locked-off take. The panel used to own this in free text, and that
# text reaches no renderer -- so stills and H3 independently invented a camera, usually
# fighting Medium.shot's hardcoded "straight-on". One enum, one clause, both prompts.
#
# Absent means the default, stored by being absent, like medium: a board that never named
# an angle and a board set back to eye level are the same board, and every reel written
# before this keeps the fingerprint it had.
CAMERA_EYE = "eye"
CAMERA_ANGLES = ("eye", "low", "high", "overhead", "dutch")
# Completes Medium.shot. The default concatenation MUST stay byte-identical to the old
# "style, shot straight-on. " opening, or every existing prompt changes.
CAMERA_CLAUSE = {
    "eye": "shot straight-on. ",
    "low": "shot from a low angle, camera below the subject looking up so it looms. ",
    "high": "shot from a high angle, camera above the subject looking down. ",
    "overhead": "shot from directly overhead, camera looking straight down. ",
    "dutch": "shot at a dutch tilt, the horizon off-level. ",
}
CAMERA_LABEL = {
    "eye": "eye level",
    "low": "low angle",
    "high": "high angle",
    "overhead": "overhead",
    "dutch": "dutch tilt",
}
# Short chip on the canvas. "Top" rather than "Overhead" because five chips share a 240 px card.
CAMERA_CHIP = {
    "eye": "Eye",
    "low": "Low",
    "high": "High",
    "overhead": "Top",
    "dutch": "Dutch",
}
# What a still is told. The video clause is a fragment that completes Medium.shot; Gemini
# stills never see that sentence, so they need a full instruction of their own.
CAMERA_STILL = {
    "eye": "Camera: eye level, looking straight at the subject. ",
    "low": "Camera: low angle, below the subject looking up so it looms. ",
    "high": "Camera: high angle, above the subject looking down. ",
    "overhead": "Camera: overhead, looking straight down on the scene. ",
    "dutch": "Camera: dutch tilt, the horizon off-level. ",
}


def snap_camera(value) -> str:
    """Force an angle onto one of the five. Unknown or empty is the default (eye)."""
    key = str(value or "").strip().lower()
    return key if key in CAMERA_ANGLES else CAMERA_EYE


def parse_camera(value) -> str:
    """Raise ValueError on a name we do not ship. Empty is the default."""
    if value is None or str(value).strip() == "":
        return CAMERA_EYE
    key = str(value).strip().lower()
    if key not in CAMERA_ANGLES:
        raise ValueError(f"camera must be one of {', '.join(CAMERA_ANGLES)}")
    return key


def write_camera(beat: dict, value) -> None:
    """Persist a camera the way fingerprints expect: absent means eye."""
    key = snap_camera(value)
    if key == CAMERA_EYE:
        beat.pop("camera", None)
    else:
        beat["camera"] = key


def camera_clause(key: str | None = None) -> str:
    return CAMERA_CLAUSE[snap_camera(key)]


def camera_still(key: str | None = None) -> str:
    return CAMERA_STILL[snap_camera(key)]


def camera_label(key: str | None = None) -> str:
    return CAMERA_LABEL[snap_camera(key)]


DEFAULT_STEPS = 8       # 20 steps costs ~70% more; 8 was judged good on paper art
DRAFT_STEPS = 8
DRAFT_SECONDS = 5.0

# H3 has no native temperature socket -- MiniMax's own sampler is steps, seed, and a
# baked-in flow shift of 12/3. What a director asking for "temperature" means is sampling
# diversity, and ComfyUI's TemporalScoreRescaling is that knob: it rescales the model's
# score during denoising. k=1 is a no-op (the node is omitted from the graph), which is
# why the default is stored by being ABSENT -- same representation as the medium, so every
# board written before this keeps the fingerprint it already had.
#
# Polarity is the node's, not an LLM's: lower k is sharper and more detailed, higher k is
# smoother. Unmeasured on H3; do not quote a quality claim. The floor is above the node's
# 0.01 so a slider cannot park on a degenerate rescale.
DEFAULT_TEMPERATURE = 1.0
MIN_TEMPERATURE = 0.1
MAX_TEMPERATURE = 2.0


def clamp_temperature(value) -> float:
    return round(max(MIN_TEMPERATURE, min(MAX_TEMPERATURE, float(value))), 2)


def write_temperature(data: dict, value) -> None:
    """Persist H3 sampling temperature the way `Board.temperature_digest` expects.

    The default is stored by being absent, so a board that never named one and a board set
    back to 1.0 are the same document -- which is what keeps every existing reel out of
    `stale` until somebody actually moves the slider.
    """
    if clamp_temperature(value) == DEFAULT_TEMPERATURE:
        data.pop("temperature", None)
    else:
        data["temperature"] = clamp_temperature(value)

# ## Billing
#
# Modal list rates. A container is billed for GPU + requested cores + requested
# memory for its whole lifetime, so counting GPU alone understates.
# Keep in sync with the @app.server decorator in comfyui_minimax_h3.py.
#
# B200 is required: unpruned BF16 is ~115 GB resident and does not fit on 96 GB.
# Wall-clock on BF16/B200 is unmeasured; SECONDS_PER_FRAME still uses the old
# 8-step quantized RTX PRO 6000 fit, so the canvas quotes B200 rates against old
# durations and will read low. Do not invent new timings. Do not run a render to
# verify.
GPU_RATE_PER_SEC = 0.001736
CPU_RATE_PER_CORE_SEC = 0.0000131
MEM_RATE_PER_GIB_SEC = 0.00000222
CONTAINER_CORES = 8
CONTAINER_GIB = 128
RATE_PER_SEC = (
    GPU_RATE_PER_SEC
    + CPU_RATE_PER_CORE_SEC * CONTAINER_CORES
    + MEM_RATE_PER_GIB_SEC * CONTAINER_GIB
)
# Published as /api/status.rate_per_second so the studio's live ticker cannot drift.

# ## Predicting render time
#
# Fitted through the two proven measurements of steady-state per-beat render time on
# RTX PRO 6000 at 8 steps, quantized weights. BF16 on B200 has not been timed;
# these numbers are kept so the canvas still has a curve rather than a guess.
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
# ref2va is now the DEFAULT for a beat that opens a new shot, which is what `reference` on the
# canvas means. A cut used to hand its still to fl2va's first-frame slot and get one anchor;
# on ref2va the same still goes in as <Picture 1> and the reel's locked cast reference rides
# along as <Picture 2>, with room for seven more. Consistency is the whole reason: one
# keyframe fixes the opening composition exactly and says nothing about anything else, where
# several references keep pulling the cast back towards its design for the whole clip.
#
# What it costs is frame-exactness and time. A keyframe latent is re-injected at every step
# and never denoised, so the opening frame is the still; a reference is conditioning, so the
# opening is close rather than identical. And reference tokens ride through every sampling
# step, where a keyframe is one VAE encode -- so a two-picture cut is a slower clip than the
# keyframe cut it replaced, and SECONDS_PER_FRAME below is fitted on the keyframe path.
# `asset` is still there for the beat that needs the frame exactly.
#
# The keyframe continuations do NOT move: chain and bridge hand over the previous clip's true
# last frame, which ref2va has no socket for at all. So a normal reel now alternates
# checkpoints, and they are 61.7 GiB each -- kept on the Volume, which costs disk, not VRAM.
# ComfyUI loads whichever the graph asks for and evicts the other, so a batch pays one model
# swap per switch. Rendering in beat order keeps that to one swap per shot boundary, which is
# what the batch already does for chaining's sake.
UNET = "minimax_h3_fl2va_bf16.safetensors"
UNET_REF = "minimax_h3_ref2va_bf16.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_bf16.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

# ## Reference conditioning
#
# MiniMaxH3ReferenceToVideo grows one input socket per reference and stops at nine images
# (`ref_images.ref_image_0` .. `ref_images.ref_image_8` in the API graph), three videos
# (`ref_videos.ref_video_0` .. `ref_video_2`), three paired soundtracks, and three standalone
# audio clips. Diffusers and the MiniMax API also cap the MIX at 12 files, so nine pictures
# plus one previous-clip video is 10 and fits; nine plus three videos is the ceiling.
#
# The prompt refers to them as <Picture 1>..<Picture 9> and <Video 1>..<Video 3>, 1-based
# and in connection order, which is the tag the text encoder is trained on. Off-by-one
# matters: image N in the graph is <Picture N+1> in the prompt. Presentation order inside
# the node is fixed: images, then videos (soundtrack label before the clip when one is
# paired), then standalone audio.
MAX_REF_IMAGES = 9
MAX_REF_VIDEOS = 3
# Mixed image+video+audio files in one ref2va request. The node has 9+3+3 sockets; the
# checkpoint will not take more than this many of them at once.
MAX_REF_FILES = 12
# On a beat with only its opening still, that still fills <Picture 1>. <Picture 2> is the
# reel's locked cast reference only when this beat binds no character or prop sheet -- a
# turnaround is the puppet, beat 1's composed wide is a camera, and sending both pulled
# later clips back to that two-shot. Extra Gemini poses are keyframes H3 interpolates
# through, not a fill of the nine sockets: filling them crowded the identity sheets out
# of the pack (they became staging_text) which is the opposite of the lock they exist for.
# An asset cut that binds identity sheets is the same list on ref2va (still as Picture 1,
# then any extra poses, then sheets): fl2va has no socket for a turnaround.
# `Board.pictures_for` is where the order is decided; the roles below are the words each
# auto-wired slot is described to the model with.
#
# One still plus the sheets is the usual pack: H3 interpolates the action. A 10s take
# adds a landing pose because 243 frames is the window a puppet can drop mid-clip; a
# lateral walk adds a mid-slide because one still plus "walk left" produced a treadmill.
# The previous clip as <Video 1> sits next to that, not instead of it.
REF_ROLE_OPENING = (
    "the composition this shot opens on: its set, its framing, its subject scale and its "
    "lighting are the ones this whole clip holds"
)
REF_ROLE_CAST = (
    "this reel's locked cast reference -- it fixes what the characters and the materials look "
    "like everywhere in the film, and it is NOT this shot's setting or framing"
)
# Still-only. Panels are composition references for Gemini, never H3 pictures -- a graphite
# sketch in a video slot is how the clip becomes a drawing. `Board.still_pictures` takes every
# panel PNG that fits after identity; `Board.pictures_for` does not mention them.
REF_ROLE_PANEL = (
    "this beat's storyboard panel -- a graphite sketch of this shot's framing, angle, and "
    "who stands where. Match that composition. Do not copy the pencil medium; the film is "
    "made of the materials in the style"
)
REF_ROLE_PANEL_FRAME = (
    "storyboard panel {i} of {k} ({phase}): a graphite sketch of this shot at that moment "
    "of the action -- framing, angle, who stands where. Match this composition in sequence. "
    "Do not copy the pencil medium; the film is made of the materials in the style"
)
# Poses 2..k of a stop-motion sequence. Pose 1 keeps REF_ROLE_OPENING, because that is still
# where the clip begins; these are the in-betweens the video model interpolates through so a
# ten-second transform cannot drop the puppet and invent a new one mid-clip.
#
# `{phase}` is `pose_phase` -- the same sentence the pose was DRAWN from. ref2va has no
# per-image text socket, so the role sentence in the prompt IS the picture's caption, and
# "at this moment of the action" with no moment named left the model to guess which picture
# was which increment.
REF_ROLE_POSE = (
    "stop-motion pose {i} of {k} of this shot: the same locked-off take, the same puppets "
    "and set, the subject at this moment -- {phase} -- not a different camera, not "
    "a different character"
)
# Lateral travel: the set is the same pieces in a different place in the frame. Without this
# H3 reads background shift as a new camera and either cuts or freezes the garden.
REF_ROLE_POSE_TRAVEL = (
    "stop-motion pose {i} of {k} of this shot: the same locked-off take and the same puppets, "
    "the set pulled this far through the travel -- {phase} -- not a different camera, not a "
    "different character, not a walk-cycle on a frozen set"
)
# "match" scales each reference down to the generation's pixel area; "max" uses the reference
# pipeline's 2048px short edge for better identity fidelity. Reference tokens ride through
# every sampling step, so "max" can be several times slower -- and slower here is money.
REF_IMAGE_SIZE = "match"

# ## Carrying motion into a reference beat
#
# ref2va has no keyframe, so a reference beat cannot take the previous clip's last frame the
# way a chained beat does. It CAN take the clip itself: the node accepts up to
# MAX_REF_VIDEOS reference videos of 2-15 s each, 15 s in total, at 24 fps.
#
# Only the tail is sent. A reference video's tokens ride through every sampling step exactly
# as an image's do, so a whole 10 s clip would be ~9x the reference cost of this for motion
# that stopped being relevant seconds ago. Three seconds is the recent past -- where the
# puppet is, how fast it is going, which way it faces.
#
# The node trims what it gets down onto the 17k+5 frame grid itself and needs at least 5
# frames, so 3 s (72 frames at 24 fps) lands at 56 frames after its own trim.
REF_VIDEO_SECONDS = 3.0
# How many Gemini keyframes asset generation draws per beat that wires pictures. Zero
# (the default) is auto from `pose_need`: H3 interpolates, so Gemini only supplies the
# poses the model cannot invent -- one for a quiet 5s beat, two for a 10s take, three
# for a lateral walk. A positive pin is that count. Nine restores the old fill of
# remaining sockets, which crowds identity sheets out of the pack. Uploads and identity
# sheets on disk are reserved either way; a sheet that does not fit is told in words.
STILL_SEQUENCE = int(os.environ.get("PAPERREEL_STILL_SEQUENCE", "0"))
# Graphite sketches per beat. They condition the still (so Nano Banana can lock the
# opening composition), never H3 -- a sketch in a video slot is how the clip becomes a
# drawing. One is enough once H3 interpolates the action; three was a stop-motion board
# for a nine-pose fill. Cap at the node's nine.
PANEL_SEQUENCE = max(1, min(
    int(os.environ.get("PAPERREEL_PANEL_SEQUENCE", "1")),
    MAX_REF_IMAGES,
))
# The clip's own soundtrack, paired to the video as `ref_video_audio_N`. Off by default:
# H3 generates each beat's audio anyway, and an audio reference is one more thing for the
# model to reproduce literally. Turn it on if ambience drifts between beats.
REF_VIDEO_WITH_AUDIO = False

# ## The language model
#
# One model does every job that is words: writing the script, carrying out the board edits a
# conversation asks for, writing the storyboard panels, writing the caption, and -- because
# it has vision -- looking at each still the image server produced and saying whether it
# belongs in this reel.
#
# It is Gemini, over the same Google API and the same `X-GOOG-API-KEY` the stills go through,
# which is the reason for the move: one credential, one provider, one bill, and nothing to
# install or keep resident. It replaced `qwen3.6` on Ollama, which replaced the Antigravity
# CLI before that.
#
# The consequence to keep in mind is that words are metered again. Not the way agy was --
# there is no five-images-per-five-hours window and no quota to ration -- but a turn has a
# price, so a call added here has to be worth one. The two self-review passes below still
# are: a flash review turn is a fraction of one Gemini image, and this pipeline spends those
# without hesitating.
GEMINI_API_URL = os.environ.get(
    "PAPERREEL_GEMINI_API_URL", "https://generativelanguage.googleapis.com/v1beta")
# The image server reads the key as `X-GOOG-API-KEY` from the same .env, so that spelling is
# first; the other two are what a shell that already has a Google key tends to call it.
GOOGLE_API_KEY = (os.environ.get("X-GOOG-API-KEY")
                  or os.environ.get("GEMINI_API_KEY")
                  or os.environ.get("GOOGLE_API_KEY") or "")
# Flash rather than Pro: every call here is either a short board edit or a look at one
# picture, and the one call whose quality is worth more (the script) buys it with reasoning
# instead (PLAN_THINK). 3.7-flash has vision, tool calling and a thinking level, which is
# what lets a single model drive the whole pipeline.
TEXT_MODEL = os.environ.get("PAPERREEL_TEXT_MODEL", "gemini-3.7-flash")
# Vision is a separate name only so the picture calls can be pointed at a different model --
# a bigger one when cast drift is being chased, a cheaper one when it is not.
VISION_MODEL = os.environ.get("PAPERREEL_VISION_MODEL", TEXT_MODEL)
# Board edits want a near-deterministic decode; this is the default for everything except the
# creative pass, which asks for more (see PLAN_TEMPERATURE).
LLM_TEMPERATURE = float(os.environ.get("PAPERREEL_LLM_TEMPERATURE", "0.3"))
PLAN_TEMPERATURE = float(os.environ.get("PAPERREEL_PLAN_TEMPERATURE", "0.8"))
# Reasoning costs output tokens and wall clock, and an unambiguous board edit needs none of
# it. So `gemini.chat` sends thinkingLevel `low` everywhere except writing the script,
# which is the one call whose quality is worth both. (`minimal` is cheaper on 3.5/3.6-flash
# but gemini-3.7-flash 400s on it.)
PLAN_THINK = os.environ.get("PAPERREEL_PLAN_THINK", "1") == "1"
# Long because the script review generates a whole corrected script with reasoning on; short
# calls come back in seconds and never approach it.
LLM_TIMEOUT = float(os.environ.get("PAPERREEL_LLM_TIMEOUT", "600"))
LLM_PROBE_TIMEOUT = 4.0
# The long edge of a picture on its way to the model, in pixels. A still is a 1.5 MB PNG and
# the review sends two; Gemini resamples an inline image onto its own tile grid anyway, so the
# full-size bytes were never reaching the model as detail. It does not measurably change the
# round trip (see `gemini.encode`) -- it is about request size. 0 sends the file untouched.
LLM_IMAGE_EDGE = int(os.environ.get("PAPERREEL_LLM_IMAGE_EDGE", "1024"))
# /api/status is polled after every settled job, so the probe behind it is cached. Short
# enough that a key just fixed in .env shows up while the user is still looking at the rail.
LLM_PROBE_CACHE = 60.0
# A tool loop has to be able to end. Every round is one model turn plus whatever the tools
# did, and a model that has started calling `read_board` in circles is not going to stop on
# its own. Eight is far more than any observed turn needs.
AGENT_MAX_ROUNDS = int(os.environ.get("PAPERREEL_AGENT_MAX_ROUNDS", "8"))
# Which transport `llm.provider()` hands back. One is registered, and the point of the name
# being a setting rather than an import is that adding a second is a file rather than a
# search-and-replace through every module that writes a prompt.
LLM_PROVIDER = os.environ.get("PAPERREEL_LLM_PROVIDER", "gemini")
# How many stills one crew run may render before the tool starts refusing. Counted in pose
# frames, not beats: a travel beat draws three keyframes, a 10s take two, a quiet 5s one.
# AGENT_MAX_ROUNDS bounds turns, not money, and `generate_stills` is the one tool in the
# crew's toolbox that spends any. Seventy-two is a guess sized when a reference cut filled
# nine sockets -- not a measurement, and the first real run is what should replace this
# number.
CREW_STILL_BUDGET = int(os.environ.get("PAPERREEL_CREW_STILL_BUDGET", "72"))
# Model rounds across one crew run, counted in runtime.run. AGENT_MAX_ROUNDS is per agent;
# a storyboard cast is nine members and each can take its own cap, so the still budget is
# not a bound on words. Zero means no cap. Two hundred is a guess sized to one ungated
# storyboard-then-assets run, not a measurement.
CREW_GEMINI_BUDGET = int(os.environ.get("PAPERREEL_CREW_GEMINI_BUDGET", "200"))
# Estimated Modal dollars a render may quote before the API 409s. Zero means no cap --
# the studio's confirm dialog is still the human gate, and a default here would 409 every
# existing reel the first time someone pressed render. A board may set its own cap in
# `render_budget`; this env is the floor only when that key is present and this is > 0.
RENDER_BUDGET_USD = float(os.environ.get("PAPERREEL_RENDER_BUDGET", "0") or 0)
# How many beats `board_digest` spells out in full. A 40-beat film in the format the
# 6-beat reel used is how the writer blew its window on history it could not use.
# Under this count the digest is byte-identical to what it was.
DIGEST_BEAT_DETAIL = int(os.environ.get("PAPERREEL_DIGEST_BEAT_DETAIL", "12"))
# Queued GPU jobs, so a studio restart can resume a render. Not a second database --
# the board is still the source of truth for what is finished; this file is only the
# queue the in-process worker used to forget.
JOBS_PATH = Path(os.environ.get("PAPERREEL_JOBS") or (ROOT / ".jobs.json"))

# ## Authoring envelope
#
# `reel` is the product this brief was written for: 20–60s, one Instagram deliverable.
# `film` keeps the same 5s/10s beats and the same 20s shot ceiling, and changes only the
# length envelope and the act grouping so a multi-minute board has somewhere to live.
# Absent means reel, stored by being absent -- the medium rule, applied to duration.
ENVELOPE_REEL = "reel"
ENVELOPE_FILM = "film"
ENVELOPES = (ENVELOPE_REEL, ENVELOPE_FILM)
DEFAULT_ENVELOPE = ENVELOPE_REEL

LENGTH_REEL = (
    "Length is chosen by the director (commonly 20–60 seconds); every beat is still "
    "either 5s or 10s."
)
LENGTH_FILM = (
    "Length is chosen by the director (commonly 2–10 minutes). Every beat is still "
    "either 5s or 10s; no shot longer than 20 seconds. Group beats into named acts so a "
    "fresh context window can orient from the act list rather than from forty scene lines. "
    "The film is N clips stitched, not one generation."
)
DURATION_REEL = """   - `4 × 5s` — 20s. Short test reel; four quick beats.
   - `2 × 10s` — 20s. Two held beats; useful for trying a long take cheaply.
   - `6 × 5s` — 30s. Brisk, montage-leaning.
   - `8 × 5s` — 40s. Eight quick beats. Busiest, most cutting energy, hardest to keep
     from feeling like a montage.
   - `4 × 10s` — 40s. Four long held beats. Slow, contemplative, most film-like, least
     room for plot.
   - `2 × 10s + 4 × 5s` — 40s, six beats. A slow open and a slow close around a quick
     middle. The default when the director says "you choose".
   - `1 × 10s + 6 × 5s` — 40s, seven beats. One held moment, otherwise brisk.
   - `3 × 10s + 2 × 5s` — 40s, five beats. Very slow, with two accents.
   - `6 × 10s` — 60s. Six held beats; room for a longer arc, more expensive to render.
   - Or any other combination of 5s and 10s — or "you choose", in which case you pick
     `2 × 10s + 4 × 5s` and say why in one line."""
DURATION_FILM = """   - `12 × 10s` — 2 min. A short film; about two or three acts.
   - `18 × 10s` — 3 min. Room for a turn.
   - `24 × 10s` — 4 min. A chaptered short; name the acts.
   - `36 × 10s` — 6 min. Mixed 5s beats are fine; keep any one setup at or under 20s.
   - `6 × (4 × 10s)` — 4 min in six acts of four held beats. The default when the
     director says "you choose" on a film.
   - Or any other combination of 5s and 10s that sums to the runtime they named.
     Group the beats into named acts on the board (`acts`), not as prose in the bible."""


def envelope(key: str | None = None) -> str:
    """One authoring envelope by key, falling back rather than raising.

    Same judgement as `medium`: the key arrives from a hand-editable board and can predate
    a rename. An unknown value renders as the reel envelope rather than refusing to write.
    """
    wanted = (key or "").strip() or DEFAULT_ENVELOPE
    return wanted if wanted in ENVELOPES else DEFAULT_ENVELOPE


def write_envelope(data: dict, key: str | None) -> None:
    """Persist an envelope the way the digest expects: absent means the reel.

    A reel's document stays byte-identical to one that never named an envelope, which is
    what keeps every board written before this out of a new code path it did not ask for.
    `None` is a no-op so a create path that did not send one leaves the document alone.
    """
    if key is None:
        return
    resolved = envelope(key)
    if resolved == DEFAULT_ENVELOPE:
        data.pop("envelope", None)
    else:
        data["envelope"] = resolved


def length_copy(key: str | None = None) -> tuple[str, str]:
    """The two brief seams that change with envelope: opening length, duration menu."""
    if envelope(key) == ENVELOPE_FILM:
        return LENGTH_FILM, DURATION_FILM
    return LENGTH_REEL, DURATION_REEL

# ## Reviewing its own work
#
# Both passes exist because the review catches failures that are hard to see in prose. Their
# additional Gemini requests are controlled by the existing review switches.
#
# The script pass re-reads the draft against the rules of the medium -- one continuous
# locked-off shot per beat, one thing moving, the style bible quoted verbatim in every asset
# prompt -- and returns a corrected script plus what it changed. Those are the failures that
# are invisible on the page and obvious in the render.
PLAN_REVIEW = os.environ.get("PAPERREEL_PLAN_REVIEW", "1") == "1"
# The stills pass looks at each finished still next to the character sheets (or the locked
# cast still when there are none) and says whether the same characters, palette and paper
# stock came back. A still that missed gets its asset prompt rewritten and is rendered again.
# The same switch and attempt bound cover a sheet held to its own note after `staging.draw`.
#
# One retry, not five: Gemini is metered and not instant, and a still the
# model rejects twice is usually telling you the style bible is the problem.
STILL_REVIEW = os.environ.get("PAPERREEL_STILL_REVIEW", "1") == "1"
STILL_ATTEMPTS = int(os.environ.get("PAPERREEL_STILL_ATTEMPTS", "2"))

# ## Talking to one still
#
# The automatic review answers "does this belong in the reel". It cannot answer "the pig should
# be looking the other way", because that is not a mismatch with anything -- it is the
# director's taste, and only the director has it. So every generated still carries a
# conversation of its own: the model is shown the picture, the reel's cast reference and what
# has already been said about it, rewrites the beat's `asset_prompt`, and renders it again.
#
# Two numbers, different on purpose. The first is how much of that conversation goes back into
# the prompt -- a window, because a still that has been through ten rounds is being judged on
# what it looks like NOW. The second is how much the board keeps, which is the record the user
# reads on the node and should outlive the window.
ASSET_CHAT_HISTORY = int(os.environ.get("PAPERREEL_ASSET_CHAT_HISTORY", "12"))
ASSET_CHAT_MEMORY = int(os.environ.get("PAPERREEL_ASSET_CHAT_MEMORY", "60"))

# ## Where opening stills come from
#
# `beat<n>_asset.png`, rendered by Papercut Studio in image/ over HTTP on this machine:
# Gemini Nano Banana through Papercut Studio, with the output straight onto the H3 grid so
# nothing is cropped on the way to the video model.
#
# It is the only generator. The old fallback was agy, whose five-per-five-hours window is
# gone along with it -- so on a machine where the image server is not up, stills are uploads.
# That is what `manual_stills` on a board is for.
PAPERCUT_URL = os.environ.get("PAPERREEL_PAPERCUT_URL", "http://127.0.0.1:8791")
# The aspect preset in image/shared/types.ts that matches GEN_WIDTH x GEN_HEIGHT exactly.
# Anything else gets cover-cropped by media.fit_frame at render time, which is how a still
# loses its framing between being approved and being rendered.
PAPERCUT_ASPECT = "9:16-reel"
# Kept in the transport contract for older scene documents; Gemini controls its own inference
# steps and does not use this value.
PAPERCUT_STEPS = int(os.environ.get("PAPERREEL_PAPERCUT_STEPS", "4"))
# How many pictures a still may be drawn FROM. Identity sheets first, then this beat's
# storyboard panels, then the previous shot's last pose, then a set that fits, then uploads.
# Nine matches H3's image sockets and the image server's MAX_REFERENCES, so Nano Banana can
# see the same stack the clip interpolates through rather than a truncated four.
#
# The image server reports its own cap in `limits.maxReferences` and the smaller of the two wins,
# exactly as the frame cap already works.
MAX_STILL_REFS = int(os.environ.get("PAPERREEL_MAX_STILL_REFS", "9"))

# Local compositor: stacked RGBA cutouts instead of asking H3 to invent puppetry from a
# photograph of a puppet. Width and baseline are what `media.compose` has always used for
# a single character on a set. Hold-on-twos and the seeded wobble are pasteup's cadence;
# they are free, and they are how paper actually moves.
COMPOSE_WIDTH_FRACTION = 0.62
COMPOSE_BASELINE = 0.88
COMPOSE_GROUND = (232, 220, 198)
ASSEMBLE_HOLD = int(os.environ.get("PAPERREEL_ASSEMBLE_HOLD", "2"))
ASSEMBLE_JITTER_PX = float(os.environ.get("PAPERREEL_ASSEMBLE_JITTER_PX", "1.5"))
ASSEMBLE_JITTER_DEG = float(os.environ.get("PAPERREEL_ASSEMBLE_JITTER_DEG", "0.4"))

# ## The medium
#
# Everything below this comment used to be a global string that said "paper". Nine of them
# reached a render and one of them -- the vision review's `judge` -- REJECTED a still for not
# being paper, so a board whose style bible said clay would have been fighting its own
# reviewer. The bundle exists so a second medium is a table entry rather than a rewrite.
#
# What is NOT in here is as important as what is. `agent.MEDIUM` is misnamed and stays where
# it is: read it and it is entirely pipeline -- the four joins, 5 s or 10 s, one thing moves,
# the camera never moves -- and every line of it is true of clay. Same for ~88% of
# `prompts/40s-stop-motion-script.md`; only its section 4 (the physics) and section 6(a) (the
# construction) are medium-bound, and those two are `physics` and `construction` below.
#
# The three entries are not translations of one thing. Cutout is stacked flats: a shape is
# SWAPPED for another on a pin. Papercraft is folded volume: the same rigid paper, assembled
# into faceted 3D forms, still swapped rather than deformed. Clay is the opposite on the
# deformation axis: a shape BECOMES another. Writing any of them under another's physics
# produces the failure that reads as a cheap 3D render. Each entry is written from the
# material outward -- and papercraft and clay are reasoned, not measured. Nothing has been
# rendered in either yet.
@dataclass(frozen=True)
class Medium:
    """One medium's words, in the fifteen places a render or a review asks for them."""

    key: str
    # What the prompts call it, e.g. "paper-cutout stop-motion". Spliced into six system
    # prompts, which is why it is a phrase rather than a sentence.
    name: str
    # The first clause of every video prompt, before anything about the join. Ends at the
    # comma after "style" -- `build_prompt` appends CAMERA_CLAUSE so the angle is one field
    # rather than a hardcoded "straight-on" fighting a panel that never reached the renderer.
    shot: str
    # The material words inside OPEN_REFERENCE's list of what a design reference fixes.
    surface: str
    # The craft clause, unconditional on every video prompt.
    craft: str
    # The audio direction, unless the board is muted.
    audio: str
    # The still's style suffix -- and, because the review judges against the same medium, the
    # thing `judge` has to agree with word for word.
    still: str
    # A prop design sheet: the subject whole, centred, on nothing.
    sheet: str
    # A character model sheet: the same puppet packed as numbered views, not a centred portrait.
    model: str
    # A set sheet: nothing but scenery, the subject deliberately absent.
    set: str
    # What the vision review holds a finished still to.
    judge: str
    # The same claim in a parenthetical, for the four chat prompts that tell a director which
    # part of their note the pipeline will not let them overrule. Shorter than `judge` because it
    # sits inside a sentence rather than being a criterion of its own.
    essence: str
    # What a storyboard panel must NOT look like, so a sketch is never mistaken for the shot.
    negate: str
    # What both renderers must not produce. Gemini image models have no negative-prompt
    # field (Imagen on Vertex does; Nano Banana does not -- a `negativePrompt` parameter
    # 400s), so Papercut appends `Avoid: {avoid}` via the scene's `negativePrompt`. H3 has
    # no negative socket either: MiniMax's own papercraft skill and the fal H3 guide both
    # say to write the exclusions as a closing block in the prompt, and they are unusually
    # effective there when they name a specific failure (plastic 3D, liquid morphs, extra
    # people) rather than a vibe ("ugly", "low quality"). One string, two transports,
    # because two copies of the same list drift. Comma-separated and specific; "no paper
    # fibers" is a double negative Gemini reads as "omit the grain", so the absences stay
    # on the positive suffixes and only the neighbouring genres go here.
    avoid: str
    # The brief's opening sentence about what the films are made of, after the em dash.
    opening: str
    # Section 4 of the authoring brief: the physics a beat has to obey to look real.
    physics: str
    # Section 6(a) of the brief: what the style bible must lock down about construction.
    construction: str


# The layout every character sheet is asked for, medium-agnostic. Construction stays on
# `Medium.model` so paper and clay do not share a suffix; this paragraph is the one copy of
# the sections themselves, concatenated into both. Four sections, not nine: micro-expressions,
# action poses, hand macros, fabric crops and a lore blurb shrink the puppet below what H3
# can lock to. Small labels are required so Gemini packs the cells; a personality paragraph
# is forbidden because lettering on a sheet leaks into the clip.
CHAR_SHEET_LAYOUT = (
    "plain light-grey ground, no scenery. One character model sheet of a SINGLE puppet packed "
    "as four numbered labeled sections: "
    "(1) FULL BODY TURNAROUND -- FRONT, 3/4, SIDE, BACK, standing at rest, faint height guides, "
    "figures complete and not cropped; "
    "(2) EXPRESSION SHEET -- six head-and-shoulders of the SAME face: NEUTRAL, HAPPY, ANGRY, "
    "SAD, SURPRISED, DETERMINED; "
    "(3) HEAD DETAIL -- FRONT, 3/4 and SIDE close-ups of that same face; "
    "(4) COLOR PALETTE -- labeled swatches sampled from this puppet, not invented hex. "
    "Every cell is the same character: same silhouette, markings, palette and construction. "
    "Small printed section labels only (FRONT, NEUTRAL, and so on). No personality paragraph, "
    "no lore, no watermarks, no signature."
)


# Every set sheet is ONE view, and this is the one copy of that rule, spliced into each
# medium's `set` suffix the way CHAR_SHEET_LAYOUT is into `model`. Measured failure: asked
# for a 9:16 set with nothing forbidding a layout, Gemini returned three stacked views of
# the same clearing in one PNG -- a triptych that then conditioned every still bound to the
# set, which is a confusing anchor and reads on the canvas as a broken picture.
SET_SHEET_VIEW = (
    "One single continuous view of the set -- not a grid, not stacked frames, not a "
    "multi-panel sheet, no split views, no borders between areas of the picture."
)


PAPER_CUTOUT = Medium(
    key="paper-cutout",
    name="paper-cutout stop-motion",
    shot=("Single continuous locked-off shot in handcrafted layered paper-cutout stop-motion "
          "style, "),
    surface="paper texture, cut edges",
    craft=(
        " Animate it as real paper puppetry: crisp cut edges, visible paper grain, layered "
        "cardstock depth with soft contact shadows, joints pivoting like split-pin cutouts. "
        "Keep every character's face, markings, proportions, colours, paper texture, "
        "decorative cut-paper details, outline weight, and scale identical in every frame. "
        # Deliberately generic. This used to name the flowers, sun and clouds of the board it
        # was written for, which on a board without them is an instruction to invent them.
        "Keep the set, the background layers, the lighting, and the camera completely static. "
        "One locked-off framing for the whole clip -- no push-in, pull-back, pan, tilt, zoom, "
        "reframe, or cut to a second angle inside the beat. Hold every subject's on-screen "
        "size constant: a puppet that opens at one height in the frame stays that height "
        "unless the action explicitly walks them toward or away from the camera -- never "
        "grow, shrink, or rescale mid-clip. Nothing transforms, duplicates, slides, rotates, "
        "or changes design. No camera movement, no cuts, no new objects, no text, no "
        "watermarks. Smooth temporal consistency and natural foot contact."
    ),
    audio=(" Audio: soft paper rustling and quiet birdsong in a sunny forest, no music, "
           "no speech."),
    still=("Vertical 9:16 portrait composition, handcrafted layered paper-cutout art, visible "
           "paper grain, soft contact shadows, no text, no watermarks, no signature."),
    sheet=("Handcrafted layered paper-cutout construction, visible paper grain, soft contact "
           "shadows, plain neutral background, the subject complete and centred with nothing "
           "cropped, even frontal lighting, no scenery, no text, no watermarks, no signature."),
    model=("Handcrafted layered paper-cutout construction, visible paper grain, soft contact "
           "shadows, even frontal lighting, " + CHAR_SHEET_LAYOUT),
    set=("Handcrafted layered paper-cutout construction, visible paper grain, soft contact "
         "shadows, layered depth from foreground to sky, even daylight unless the description "
         "says otherwise, an empty set with no characters, no people and no animals anywhere "
         "in it, nothing cropped at the edges, no text, no watermarks, no signature. "
         + SET_SHEET_VIEW),
    judge=("layered paper-cutout with visible paper grain, crisp cut edges, soft contact "
           "shadows. Not a photograph, not a 3D render, not clay, not felt"),
    essence="layered paper cutout, visible paper grain, soft contact shadows",
    opening=("**handcrafted layered paper-cutout stop motion** — real paper on a real "
             "tabletop, lit by a real lamp, shot on a locked-off camera"),
    negate="Not paper cutout, no paper-cutout layers, no paper grain, no collage",
    # MiniMax's papercraft-stop-motion-explainer skill, STEP 18, minus the items that
    # fight this pipeline (high-speed camera orbit is already banned by `craft`; "no
    # paper fibers" is the double-negative trap above) and minus the explainer-only
    # ones (educational labels). Identity extras (duplicate characters, extra limbs)
    # are the H3 same-face guide: a reference anchors the subject, the text has to
    # forbid a second copy or the model invents one.
    avoid=("smooth plastic 3D, glossy CG render, live-action photograph, photoreal "
           "skin or hair, flat vector illustration, generic cartoon without paper "
           "texture, melting or liquid morphing, extra limbs, extra faces, duplicate "
           "characters, text overlays, watermarks, signatures"),
    physics="""The film is paper. Paper is rigid, flat, and hinged. Everything you write must be
physically buildable on a tabletop by a person with a craft knife.

- **Paper does not morph, melt, stretch, or squash.** Shapes never smoothly transform into
  other shapes. A character changes expression by *swapping a cut piece*, not by their face
  flowing into a new one.
- **Limbs pivot at visible joints** (brass split pins). No rubber-hose bending, no
  boneless curves.
- **Water, fire, smoke, rain, cloth and hair are cut shapes that slide, rotate, swap, or
  are replaced** — never fluid simulation. Waves are nested crescents that slide past each
  other. Fire is three flame shapes cycling. Rain is straight paper slivers all leaning the
  same way, translating downward. Say this explicitly in the action lines.
- **Motion is on twos or threes** — small visible steps between poses, a slight stutter,
  not glassy interpolation. Name this in the style bible.
- **Layers are physically separated in depth** and each casts a soft contact shadow onto
  the one behind. Depth comes from stacked planes, never from a blurred gradient.""",
    construction="""That it is layered paper-cutout stop motion photographed
on a tabletop diorama rig. Which papers: cold-press cardstock with visible tooth, kraft
paper, shredded crepe, translucent vellum, gold foil — name the actual materials used for
the actual elements in *this* film. That every layer stands a few millimetres in front of
the next and casts a soft contact shadow. That edges are hand-cut with a craft knife, crisp
but slightly irregular, showing a pale paper core where coloured stock is cut through. That
sheets curl and warp a little and registration is a hair imperfect. That motion is animated
on twos. That all tone comes from stacked cut shapes — **no digital gradients, no 3D
render, no plastic sheen, no airbrushing**.""",
)


# Folded volume, not stacked flats. Cutout's depth is air between sheets; papercraft's depth
# is the paper itself, scored and assembled into faceted 3D forms. It is still paper -- rigid,
# no squash -- so a papercraft film written under cutout's physics comes out as theater flats,
# which is the one look this medium must not be. MiniMax's own skill is named for this, not
# for cutout, and the two reads of "paper" are why they are separate table entries.
PAPER_CRAFT = Medium(
    key="paper-craft",
    name="papercraft stop-motion",
    shot=("Single continuous locked-off shot in handcrafted folded papercraft stop-motion "
          "style, "),
    surface="scored creases, folded edges, paper thickness",
    craft=(
        " Animate it as real folded papercraft: scored crease lines catching the light, "
        "geometric faceted forms assembled from folded planes, visible tabs and slots, "
        "paper thickness at every folded edge. Joints are folded hinges and interlocking "
        "tabs, not split pins. Keep every character's face, markings, proportions, colours, "
        "paper surface, crease pattern, faceted silhouette, and scale identical in every "
        "frame. Keep the set, the background, the lighting, and the camera completely "
        "static. One locked-off framing for the whole clip -- no push-in, pull-back, pan, "
        "tilt, zoom, reframe, or cut to a second angle inside the beat. Hold every subject's "
        "on-screen size constant: a figure that opens at one height in the frame stays that "
        "height unless the action explicitly walks them toward or away from the camera -- "
        "never grow, shrink, or rescale mid-clip. Nothing transforms, duplicates, changes "
        "design, or unfolds into a different form. No camera movement, no cuts, no new "
        "objects, no text, no watermarks. Smooth temporal consistency and natural foot "
        "contact."
    ),
    audio=(" Audio: soft paper rustling and quiet room tone, no music, no speech."),
    still=("Vertical 9:16 portrait composition, handcrafted folded papercraft, visible scored "
           "creases and faceted 3D paper forms, soft contact shadows, no text, no watermarks, "
           "no signature."),
    sheet=("Handcrafted folded papercraft construction, visible scored creases, faceted 3D "
           "paper forms, soft contact shadows, plain neutral background, the subject complete "
           "and centred with nothing cropped, even frontal lighting, no scenery, no text, no "
           "watermarks, no signature."),
    model=("Handcrafted folded papercraft construction, visible scored creases, faceted 3D "
           "paper forms, soft contact shadows, even frontal lighting, " + CHAR_SHEET_LAYOUT),
    set=("Handcrafted folded papercraft construction, visible scored creases, faceted 3D "
         "paper architecture receding into depth, even daylight unless the description says "
         "otherwise, an empty set with no characters, no people and no animals anywhere in "
         "it, nothing cropped at the edges, no text, no watermarks, no signature. "
         + SET_SHEET_VIEW),
    judge=("folded papercraft with visible scored creases, faceted 3D paper forms, assembled "
           "constructions sitting in real volume. Not a photograph, not a 3D render, not "
           "clay, not flat paper-cutout collage"),
    essence="folded papercraft, visible scored creases, faceted 3D paper forms",
    opening=("**handcrafted folded papercraft stop motion** — real scored and folded paper "
             "on a real tabletop, lit by a real lamp, shot on a locked-off camera"),
    negate="Not papercraft, no folded paper, no scored creases, no faceted paper models",
    # Neighbouring-genre list: stacked cutout flats are the thing this medium must not
    # come back as (cutout's avoid names clay; this one names cutout). Melting stays,
    # because papercraft is still rigid paper.
    avoid=("smooth plastic 3D, glossy CG render, live-action photograph, photoreal "
           "skin or hair, flat paper-cutout collage, stacked theater flats, generic "
           "cartoon without paper creases, melting or liquid morphing, extra limbs, "
           "extra faces, duplicate characters, text overlays, watermarks, signatures"),
    physics="""The film is folded paper. Paper is rigid, and here it has volume because it has
been scored, folded and assembled. Everything you write must be physically buildable on a
tabletop by a person with a craft knife, a bone folder and glue.

- **Paper does not morph, melt, stretch, or squash.** Shapes never smoothly transform into
  other shapes. A character changes expression by *swapping a cut face panel* on a folded
  head, not by the paper flowing.
- **Forms are faceted 3D constructions**, not stacked flats. A body is folded planes meeting
  at scored edges; a building has a ridge-fold roof; a limb is a folded tube or a hinged
  strip. Depth comes from the paper's own volume, never from air between theater flats.
- **Joints are folded hinges, glued tabs and interlocking slots**, not brass split pins.
  Name the join. A raised arm is a folded hinge opening, not a flat piece rotating on a pin.
- **Water, fire, smoke, rain, cloth and hair are folded or accordion-pleated paper** —
  never fluid simulation, never nested flat crescents sliding past each other (that is
  cutout). Waves are concertina folds that expand. Fire is three folded flame forms cycling.
  Rain is folded paper slivers. Say this explicitly in the action lines.
- **Motion is on twos or threes** — small visible steps between poses, a slight stutter,
  not glassy interpolation. Name this in the style bible.
- **Crease lines catch the light.** Every fold is a ridge or a valley you can see. A form
  with no crease in it reads as a 3D render of paper rather than a photograph of it.""",
    construction="""That it is folded papercraft stop motion photographed on a
tabletop. Which papers and which folds: cold-press cardstock scored with a bone folder,
kraft for structural walls, vellum for windows, gold foil on a folded trim — name the
actual materials and the actual crease pattern used for the actual elements in *this* film.
That every form is a faceted 3D construction assembled from folded planes, with visible
tabs, slots and paper thickness at every edge. That crease lines catch the key light.
That motion is animated on twos. That all volume comes from folded paper — **no stacked
theater flats, no digital gradients, no 3D render, no plastic sheen, no airbrushing**.""",
)


# Written from the material outward, not by substituting words into the entries above. The
# axis where clay opposes both paper media is deformation: paper's grammar is that a shape
# is swapped for another shape, and clay's grammar is that a shape BECOMES another shape. A
# clay film written under paper's rules comes out stiff, which is the failure mode that
# reads as a cheap 3D render -- the exact thing all three media are trying not to look like.
CLAYMATION = Medium(
    key="claymation",
    name="clay stop-motion",
    shot=("Single continuous locked-off shot in handcrafted plasticine clay stop-motion "
          "style, "),
    surface="clay surface, thumbprints and tool marks",
    craft=(
        " Animate it as real clay puppetry: matte plasticine over a wire armature, visible "
        "thumbprints and sculpting-tool marks, soft rounded forms, seams where parts were "
        "pressed together, weight in every pose. Bodies squash and stretch as they move and "
        "settle back; nothing is rigid. Keep every character's face, markings, proportions, "
        "colours, clay surface, sculpted details, silhouette weight, and scale identical in "
        "every frame. Keep the set, the background, the lighting, and the camera completely "
        "static. One locked-off framing for the whole clip -- no push-in, pull-back, pan, "
        "tilt, zoom, reframe, or cut to a second angle inside the beat. Hold every subject's "
        "on-screen size constant: a puppet that opens at one height in the frame stays that "
        "height unless the action explicitly walks them toward or away from the camera -- "
        "never grow, shrink, or rescale mid-clip. Nothing duplicates, changes design, or "
        "turns into a different character. No camera movement, no cuts, no new objects, no "
        "text, no watermarks. Smooth temporal consistency and natural foot contact."
    ),
    audio=(" Audio: soft clay squeaks and quiet room tone, no music, no speech."),
    still=("Vertical 9:16 portrait composition, handcrafted plasticine clay stop-motion art, "
           "matte clay surface with visible thumbprints and tool marks, soft practical "
           "shadows, no text, no watermarks, no signature."),
    sheet=("Handcrafted plasticine clay construction, matte clay surface with visible "
           "thumbprints and tool marks, soft practical shadows, plain neutral background, the "
           "subject complete and centred with nothing cropped, even frontal lighting, no "
           "scenery, no text, no watermarks, no signature."),
    model=("Handcrafted plasticine clay construction, matte clay surface with visible "
           "thumbprints and tool marks, soft practical shadows, even frontal lighting, "
           + CHAR_SHEET_LAYOUT),
    set=("Handcrafted plasticine clay construction, matte clay surface with visible "
         "thumbprints and tool marks, sculpted terrain receding into depth, even daylight "
         "unless the description says otherwise, an empty set with no characters, no people "
         "and no animals anywhere in it, nothing cropped at the edges, no text, no "
         "watermarks, no signature. " + SET_SHEET_VIEW),
    judge=("sculpted plasticine clay with a matte surface, visible thumbprints and tool "
           "marks, soft rounded forms. Not a photograph, not a 3D render, not paper, not felt"),
    essence="sculpted plasticine clay, visible thumbprints and tool marks, a matte surface",
    opening=("**handcrafted plasticine clay stop motion** — real clay on a real tabletop, "
             "lit by a real lamp, shot on a locked-off camera"),
    negate="Not clay, no plasticine, no sculpted forms, no photographic texture",
    # Same neighbouring-genre list as paper, with the two swaps the material needs:
    # paper-cutout collage is the thing this medium must not come back as, and
    # "melting or liquid morphing" is omitted because squash-and-stretch IS clay.
    # Specular plastic is the cheap-CGI failure `construction` already names.
    avoid=("smooth plastic 3D CGI, glossy specular highlights, live-action photograph, "
           "paper-cutout collage, flat vector illustration, generic cartoon without "
           "clay texture, extra limbs, extra faces, duplicate characters, text "
           "overlays, watermarks, signatures"),
    physics="""The film is clay. Clay is soft, heavy, and continuous. Everything you write must be
physically buildable on a tabletop by a person with their hands and a set of sculpting tools.

- **Clay squashes and stretches, and that is the point.** A body compresses as it lands and
  extends as it leaps. Write the anticipation and the settle, not just the move — a pose that
  arrives and stops dead reads as a 3D render, which is the one thing this medium must not
  look like.
- **Limbs bend along their length**, over a wire armature. There are no visible joints and no
  hinges; a raised arm curves.
- **A face changes by being re-sculpted**, not by swapping a piece. Mouths are pressed and
  reshaped; brows are pushed. Expressions arrive over two or three frames rather than cutting.
- **Water, fire, smoke, rain and hair are sculpted clay forms that deform, roll and are
  replaced** — never fluid simulation. Waves are rolled clay ridges that bulge and flatten.
  Fire is three sculpted flame forms cycling. Rain is short clay slivers falling. Say this
  explicitly in the action lines.
- **Motion is on twos** — small visible steps between poses, a slight stutter, not glassy
  interpolation. Name this in the style bible.
- **Depth comes from real sculpted volume** lit by one rig, never from a blurred gradient.
  Every surface is matte: clay has no specular sheen and a shiny highlight reads as plastic.""",
    construction="""That it is handcrafted plasticine clay stop motion photographed
on a tabletop set. Which clays and which armatures: matte plasticine over twisted aluminium
wire, harder sculpey for props that must hold an edge, a painted foam or sculpted clay
groundplane — name the actual materials used for the actual elements in *this* film. That
the surface holds visible thumbprints, fingernail creases and the drag marks of a sculpting
tool, and that these move slightly frame to frame. That seams show where two pieces were
pressed together. That everything is matte and slightly dusty, with **no plastic sheen, no
specular highlights, no digital gradients, no 3D render, no airbrushing**. That motion is
animated on twos, with squash on impact and a settle after every stop.""",
)

MEDIUMS = {entry.key: entry for entry in (PAPER_CUTOUT, PAPER_CRAFT, CLAYMATION)}
# The medium a board is in when it does not say. It has to be paper cutout and it has to stay
# that way: every reel written before the bundle existed has no `medium` key, and `Board.medium`
# reads a missing key as this. A board that never says is byte-identical to what it was.
DEFAULT_MEDIUM = PAPER_CUTOUT.key


def medium(key: str | None = None) -> Medium:
    """One medium by key, falling back rather than raising.

    Falls back because the key arrives from a board document, which is hand-editable and can
    predate a rename. A reel that names a medium nobody ships should render as the default
    rather than not render at all -- the same judgement `Board.source_for` makes about a join
    it does not recognise.
    """
    return MEDIUMS.get((key or "").strip() or DEFAULT_MEDIUM, PAPER_CUTOUT)


def write_medium(data: dict, key: str | None) -> None:
    """Persist a medium the way `Board.medium_digest` expects: absent means the default.

    A paper-cutout reel's document stays byte-identical to one that never named a medium,
    which is what keeps every board written before this bundle out of `stale`. Callers that
    take a key from a request must validate it against `MEDIUMS` first -- this function
    falls back the same way `medium` does, and a typo here would silently become paper.
    `None` is a no-op so a create path that did not send a medium leaves the document alone.
    """
    if key is None:
        return
    resolved = medium(key).key
    if resolved == DEFAULT_MEDIUM:
        data.pop("medium", None)
    else:
        data["medium"] = resolved


# What every still is asked for on top of the board's style bible and the beat's own
# asset_prompt. One place, because it is also what the vision review judges a still against:
# a still is rejected for missing the medium described here, so the words that ask for it and
# the words that check for it must not be able to drift apart.
#
# The module-level name is the default medium's, kept so a caller that has no board in its hand
# still reads correctly. Anything that HAS a board goes through `config.medium(board.medium)`.
ASSET_STYLE_SUFFIX = PAPER_CUTOUT.still

# ## Reference pictures the studio draws rather than receives
#
# A beat's reference pictures used to be uploads only. Gemini supports inline references, so they are
# now drawable too: a prop sheet, a set with nobody in it, a costume detail -- anything the
# director would otherwise have had to find a photograph of.
#
# `1:1`, not PAPERCUT_ASPECT. `9:16-reel` (768x1344) exists for exactly one reason -- a STILL is
# handed to H3 as a frame and anything off that grid is cover-cropped by media.fit_frame. A
# reference picture is never a frame; it is conditioning, and the graph rescales it. So this is
# free to be the neutral shape for a design sheet, and at 1024x1024 it is ~0.8x the pixels, which
# is ~0.8x the wall clock.
PAPERCUT_REF_ASPECT = "1:1"

# Per-beat controls exposed by the canvas. The image server validates them again; keeping the
# allow-list here lets the canvas API reject a typo before a paid request is queued.
GEMINI_IMAGE_MODELS = (
    "gemini-3-pro-image",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image",
)
GEMINI_IMAGE_SIZES = ("1K", "2K", "4K")

# The still's suffix asks for a vertical 9:16 SHOT. A design reference is the opposite of a shot:
# no framing to speak of, nothing implied off the edges, the subject whole and centred so it can
# be read rather than staged. Sharing ASSET_STYLE_SUFFIX would ask every prop sheet to be a
# composition, which is how a picture of a club comes back as a scene with a club in it.
REF_DRAW_STYLE_SUFFIX = PAPER_CUTOUT.sheet

# The window and the memory for one picture's conversation, mirroring the still's pair above.
#
# 12 against the still's 60, and the difference is not timidity. `to_json` serialises the whole
# board on every SSE-announced refetch, and a beat has one still against up to nine pictures --
# so this grows in two dimensions where ASSET_CHAT_MEMORY grows in one. It is also only ever the
# director's own turns: no automatic reviewer posts here, because a reference picture is SUPPOSED
# to differ from the cast.
REF_CHAT_HISTORY = int(os.environ.get("PAPERREEL_REF_CHAT_HISTORY", "8"))
REF_CHAT_MEMORY = int(os.environ.get("PAPERREEL_REF_CHAT_MEMORY", "12"))

# ## Staging: the reel's cast and sets, designed before anything is rendered
#
# Everything above is beat-scoped. A picture uploaded to beat 3 conditions beat 3, and the one
# image that crossed the whole reel was the cast reference -- which is not a design sheet at all
# but beat 1's own still, a composed shot whose framing, staging and light every later still was
# then anchored to. That is why a second character had nowhere to live and why the same forest
# was redesigned from the same paragraph in every beat that used it: the only reel-wide anchor
# was one picture, and it was a picture of one moment.
#
# A staging entry is the missing layer. It is named, it is written down, it is drawn once as a
# design sheet, and beats BIND it -- so the same wolf and the same clearing reach every shot they
# appear in as the same image and the same sentence, rather than as two readings of the bible.
#
# Three kinds, and the kind is not decoration -- it decides three separate things: what the sheet
# is drawn as (the suffixes below), what shape it is drawn at, and how `Board.staging_pictures`
# orders it for the still (environments last, so the four-slot cap drops the set first).
STAGE_CHARACTER = "character"
STAGE_ENVIRONMENT = "environment"
STAGE_PROP = "prop"
STAGE_KINDS = (STAGE_CHARACTER, STAGE_ENVIRONMENT, STAGE_PROP)

# What each picture in `pictures_for` is, for the unhashed MiniMax reference scaffold.
# Parallel to the notes, never instead of them -- `FrameIds.refs` hashes (file, note) pairs,
# so rewriting STAGE_ROLE or REF_ROLE_* would mark every picture-wired beat stale. These
# labels let `build_prompt` emit subject / retention lines without touching those strings.
REF_KIND_OPENING = "opening"
REF_KIND_POSE = "pose"
REF_KIND_CAST = "cast"
REF_KIND_CHARACTER = STAGE_CHARACTER
REF_KIND_PROP = STAGE_PROP
REF_KIND_SET = "set"
REF_KIND_UPLOAD = "upload"
REF_KIND_FROM_STAGE = {
    STAGE_CHARACTER: REF_KIND_CHARACTER,
    STAGE_PROP: REF_KIND_PROP,
    STAGE_ENVIRONMENT: REF_KIND_SET,
}

# A soft ceiling on the whole bible, not on what one beat binds -- that is bounded by
# MAX_REF_IMAGES already. It exists so a runaway loop cannot fill the reel directory with sheets,
# and it is generous: a 40-second reel with more than a dozen designed things is not a reel.
MAX_STAGE_SHEETS = int(os.environ.get("PAPERREEL_MAX_STAGE_SHEETS", "16"))

# A set sheet is the one kind REF_DRAW_STYLE_SUFFIX is wrong for, and wrong in both halves: it
# asks for "the subject complete and centred" on a "plain neutral background" with "no scenery",
# and an environment sheet is nothing but scenery with the subject deliberately absent. Handed
# the prop-sheet suffix, "a moonlit clearing ringed with birches" came back as a single birch on
# grey -- which is a faithful reading of the instruction it was given.
#
# It also asks for the reel's own vertical shape rather than the square below, because a set is
# the one design sheet whose framing is load-bearing: what the still needs to know is how much of
# this clearing is above the puppet's head, and a square sheet answers a different question.
SET_DRAW_STYLE_SUFFIX = PAPER_CUTOUT.set
# The set sheet's shape. Deliberately the still's grid rather than PAPERCUT_REF_ASPECT: a set is
# read for its framing, so it is drawn in the frame it will be seen in. Unlike PAPERCUT_ASPECT
# this carries no hard constraint -- a sheet is conditioning and is never handed to H3 as a frame
# -- so it is the same string for a reason rather than by obligation.
PAPERCUT_SET_ASPECT = PAPERCUT_ASPECT

# A character model sheet is the other kind the square is wrong for: four labeled sections
# side by side, and a 1:1 pack crushes the turnaround. `16:9` is already a Papercut preset
# (1152x640, Gemini 2K scales it). Unlike PAPERCUT_ASPECT this carries no hard constraint --
# a sheet is conditioning and is never handed to H3 as a frame. Prop sheets stay on the
# square above; they are one object, not a pack.
PAPERCUT_CHAR_ASPECT = "16:9"
CHAR_DRAW_STYLE_SUFFIX = PAPER_CUTOUT.model

# What a bound sheet is called in the prompt when the director has written nothing about it. The
# name alone, in a sentence, because `reference_roles` splices this after "<Picture 3> is " and a
# bare noun reads as a fragment there. A sheet WITH a note uses the note instead: the director's
# own words about their own design always win.
STAGE_ROLE = {
    STAGE_CHARACTER: "{name}, one of this reel's characters -- this sheet is that character's "
                     "appearance reference only: it may show several views of the same puppet "
                     "(turnaround, expressions, head, palette) and is still one {name}. It "
                     "fixes what {name} looks like (shapes, markings, colours, materials), not "
                     "this shot's pose or framing, and the same single {name} performs the "
                     "action below",
    STAGE_ENVIRONMENT: "{name}, the set this shot takes place in -- this sheet is that set's "
                       "locked design, empty of characters",
    STAGE_PROP: "{name}, a prop in this film -- this sheet is its locked design",
}

# A bound entry that is NOT one of the pictures this render was given travels as words instead,
# and every renderer applies the same rule (`Board.staging_text`). Two things end up here: a set,
# which is text by design on the still side, and anything past a cap. Prefixed rather than woven
# in, for the reason SCENE_PREFIX is: these are descriptions of separate things and no connecting
# phrase reads correctly across a character, a clearing and a club at once.
STAGING_PREFIX = "Also already designed and not to be reinterpreted: "

# The window and the memory for one staging sheet's conversation. Longer than a picture's,
# because the reason that one is short does not apply: REF_CHAT_MEMORY is 12 against the still's
# 60 because a beat has up to nine pictures and `to_json` serialises every transcript on every
# refetch, so it grows in two dimensions. The bible is one list for the whole reel and is the
# thing most worth having a record of -- it is what every image in the film is held to.
STAGE_CHAT_HISTORY = int(os.environ.get("PAPERREEL_STAGE_CHAT_HISTORY", "10"))
STAGE_CHAT_MEMORY = int(os.environ.get("PAPERREEL_STAGE_CHAT_MEMORY", "40"))

# ## Storyboard panels: the reel read as pictures before anything is paid for
#
# A storyboard in the film sense is a sheet of rough panels -- one drawing per shot, showing the
# framing, the angle and, with arrows on the panel, how the subject moves. It is
# drawn cheap and read fast, and it exists so the sequence is judged before money goes out.
#
# Everything else in this file describes a picture that reaches a renderer. A panel reaches the
# still renderer as a composition sketch (`Board.still_pictures`, `config.REF_ROLE_PANEL`) and
# is handed to H3 never. It is in no fingerprint: the still file is what the clip hashes. That
# split is what makes the cheapest model the right one rather than a compromise.
PANEL_MODEL = os.environ.get("PAPERREEL_PANEL_MODEL", "gemini-3.1-flash-lite-image")
# Lite is 1K-only, which the image server enforces itself and `api.gemini_options` rejects early.
# Stated here so a change of PANEL_MODEL has the pair to change in one place.
PANEL_IMAGE_SIZE = os.environ.get("PAPERREEL_PANEL_IMAGE_SIZE", "1K")
# `9:16` (640x1152), NOT PAPERCUT_ASPECT. The `9:16-reel` grid exists for exactly one reason -- a
# STILL is handed to H3 as a frame, and anything off that grid is cover-cropped by
# `media.fit_frame`. A panel is never a frame, so the cheaper vertical preset is free to use: ~0.63x
# the pixels of the reel grid, which is ~0.63x the wall clock.
PANEL_ASPECT = "9:16"

# What a panel is asked for, in place of the board's style bible -- `papercut.draw` overrides the
# scene style, which is the whole reason a panel can be a different kind of picture at all.
#
# The paper cutout is negated ON PURPOSE, and it is the one instruction here that looks like a
# mistake. Two reasons. A Lite 1K version of the real medium is a bad preview OF that medium and
# reads on the canvas as a finished still, which is the confusion this feature must not create. And
# a storyboard is about framing rather than texture: the whole value of the pass is that a panel
# cannot be mistaken for the shot.
#
# Nothing conditions a panel (`pictures=[]`, so `_scene_body` composes "none"), which is
# `pictures.py`'s measured lesson applied one level further out: a model shown the cast reference
# draws the cast, in the cast's medium. Handed the cutout still, a sketch panel comes back a
# cutout. The subject travels as words instead -- `panels.write` puts it in the panel text.
# What it negates is the FILM's medium, whichever that is, which is why the clause comes out of
# the bundle. A clay reel whose panels negated paper cutout would be negating something nobody
# was going to draw anyway, and leaving the real risk -- a sketch that comes back as a finished
# clay frame -- unaddressed.
PANEL_STYLE_TEMPLATE = (
    "Rough black-and-white storyboard panel: soft graphite pencil and grey marker on off-white "
    "paper, loose confident construction lines, flat tonal blocking, no colour. Arrows drawn "
    "directly on the frame for camera and subject movement. {negate}, no photographic texture "
    "-- this is a sketch of a shot, not the shot. No lettering, no captions, no watermarks, no "
    "signature."
)


def panel_style(key: str | None = None) -> str:
    return PANEL_STYLE_TEMPLATE.format(negate=medium(key).negate)


PANEL_STYLE_SUFFIX = panel_style()

# ## Prompt scaffold
#
# H3 holds a paper-cutout look far better when the instruction pins down what must
# NOT change. Callers supply the beat's scene and action, the board's identity paragraph,
# and which kind of join this beat opens on; this assembles the rest.
#
# Two shapes, because MiniMax trains two. The keyframe path (chain / bridge / a plain
# asset cut) is still a concatenation: OPEN_CUT or OPEN_CONTINUATION, then bible, staging,
# blocking, scene, action, craft, audio, Avoid. That path is byte-identical to what it was.
#
# The reference path (ref2va: refs or a reference video) is MiniMax's six-part reference
# format: subject_definitions, summary, retention_analysis, detailed_description,
# overall_soundscape, non_diegetic_music. Combined identity+storyboard sheets on
# RunDiffusion open ON THE SHEET for half a second unless every picture is given one
# role and told it is not a start frame. Fingerprints do not hash the scaffold, so this
# does not mark existing clips stale; STAGE_ROLE / REF_ROLE_* stay put for that reason.
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
    "style, " + CAMERA_CLAUSE[CAMERA_EYE]
)
OPEN_CUT = (
    "The provided first frame is the opening frame of a new shot: begin from it exactly, "
    "and hold its framing, every subject's on-screen size, and lighting for the whole clip. "
)
OPEN_CONTINUATION = (
    "The provided first frame is a freeze grabbed from the middle of a take that is "
    "already in motion -- it is not a new setup and not a rest pose. Carry that motion on "
    "from exactly this pose, at the same speed and in the same direction, as one unbroken "
    "take. Do not restart the shot, do not re-pose or re-centre the subject, do not let it "
    "settle to rest and start again, do not grow or shrink anyone, and do not re-establish "
    "the scene: same set, same camera, same lighting, same subject sizes, same moment "
    "continuing. "
)
# The reference join has no keyframe at all: ref2va conditions on pictures of the cast and the
# set, and the opening composition is the model's to build. That has to be said explicitly or
# the model reads nine supplied images as nine shots and cuts between them -- which is the one
# thing this production never does. Formatted with the <Picture i> tag list at build time.
OPEN_REFERENCE = (
    "No first frame is provided. Instead {tags} {verb} supplied as design references: they fix "
    "what the characters, the set and the materials look like, and nothing else. Reproduce "
    "every subject that appears in them exactly -- same shapes, markings, colours, "
    "proportions, {surface} and palette. The references are not shots and not a start "
    "frame: do not show them, do not display them as the opening frame of the clip, do not "
    "cut between them, do not pan across them, do not morph the camera "
    "toward any of them, do not grow or shrink a character to match another picture's size, "
    "and do not put more than one version of a character on screen. A "
    "character shown in a reference is the SAME single character that performs the action "
    "below, not an additional one. The pose, framing, shot scale and on-screen size in a "
    "reference are only how that picture was drawn -- not where this shot starts, not a "
    "second angle to visit, and not something that must also appear. "
)
# Where the shot begins, when nothing says otherwise. Separate from the paragraph above
# because a carried reference video answers the same question differently -- it says open on
# the moment that clip ends -- and the two instructions must never both be present.
COMPOSE_OPENING = "Compose the opening frame yourself from the scene line below. "
# The third answer to "where does this shot begin", and the one a cut now gives: <Picture 1>
# is this beat's own still, so the opening composition was designed rather than left to the
# model. It has to say so in these words because the paragraph above deliberately says the
# opposite about references in general -- that the pose in one is only how a thing looks and
# not where the shot starts. That is right for the cast reference and wrong for this picture,
# so the exception is named rather than left to be inferred from the roles list.
#
# "Begin from it" and not "begin from it exactly", which is what OPEN_CUT says of a keyframe:
# a keyframe latent IS the first frame, a reference only conditions towards it, and promising
# exactness the checkpoint cannot deliver is how the model ends up holding still at the start
# waiting to match something.
OPEN_REFERENCE_STILL = (
    "{tag} is the exception to that: it is not a design reference but this shot's own opening "
    "composition, drawn for this beat. Begin the clip on it -- its framing, its subject "
    "placement and on-screen sizes, its set dressing and its light are where this take "
    "starts -- and hold that ONE framing, those ONE subject sizes and that ONE lighting for "
    "the whole clip. Other references may show the same cast larger, smaller, or tighter: "
    "ignore those sizes and framings completely; do not push in, pull back, reframe, cut, "
    "grow, shrink, or drift the camera toward them. Everything the action below describes "
    "happens from the opening composition, forward, under a locked-off camera, with "
    "on-screen sizes changing only if the action explicitly moves a subject toward or away "
    "from the camera. "
)
# When asset generation drew a stop-motion sequence, those pictures ARE the shot, in order.
# OPEN_REFERENCE says a reference's pose is not where the shot starts, which is right for a
# cast sheet and exactly wrong for pose 3 of 7 of this beat's action. Named as a sequence so
# the model interpolates through them instead of treating nine stills as nine cuts.
OPEN_REFERENCE_SEQUENCE = (
    "{tags} are successive stop-motion poses of THIS shot, in order. {first} is where the "
    "clip begins -- its framing, subject sizes, set and light are the ones this whole take "
    "holds -- and each next picture is the next pose of the same locked-off take. "
    "Interpolate the action through those poses, evenly, without cutting, without skipping "
    "a pose, and without treating any of them as a different camera or a second puppet. "
    "The puppets, the set, the lighting and the framing are the same in every one; only the "
    "moving subject changes. "
)
# Lateral travel on 9:16 cannot be a puppet crossing a locked wide -- they exit in a few
# steps, and poses that only change gait against a glued-down set make H3 fake a walk.
# Same sequence, but the set is allowed to translate. The rig still does not pan.
OPEN_REFERENCE_SEQUENCE_TRAVEL = (
    "{tags} are successive stop-motion poses of THIS shot, in order. {first} is where the "
    "clip begins -- its framing, subject sizes and light are the ones this whole take "
    "holds -- and each next picture is the next pose of the same locked-off take. "
    "Interpolate the action through those poses, evenly, without cutting, without skipping "
    "a pose, and without treating any of them as a different camera or a second puppet. "
    "The puppets hold their on-screen size and roughly the same screen third in every one; "
    "their gait advances AND the set layers translate opposite the walk, same pieces "
    "shifting in the frame. That set shift is locomotion, not a new camera. Do not freeze "
    "the background; do not animate a walk-cycle in place. "
)
# What a carried reference video is, and it has to be said in the same breath as "compose the
# opening frame yourself" -- otherwise the two instructions fight and the model either ignores
# the clip or treats it as footage to replay. This is the reference join's answer to
# OPEN_CONTINUATION: not a frame handoff, but the same take, carried on from where it ended.
CARRY_VIDEO = (
    "{tag} is the last few seconds of the shot immediately before this one, and this shot is "
    "that same take carrying on. Open on the moment {tag} ends -- same set, same camera, same "
    "lighting, the subject in the pose, position and on-screen size it is in on that final "
    "moment -- and continue its movement onward at the same speed and in the same direction. "
    "Do not replay {tag}, do not cut to it, do not re-establish the scene, do not grow or "
    "shrink anyone, and do not let the subject settle to rest and start again. "
)
# The other job a previous clip can do: identity, not opening. A hard cut still begins on
# its own still (or its pose sequence); the video is there so a transform cannot drop the
# puppet the last shot already established. Must never be paired with CARRY_VIDEO -- those
# are two answers to where the shot opens.
HOLD_VIDEO = (
    "{tag} is the clip immediately before this one in the same film. It locks how the "
    "characters, the materials, the motion and the set look. Reproduce them exactly. Do not "
    "replay {tag}, do not cut to it, and do not re-establish the scene from it. This shot "
    "begins on its own opening composition, not on the moment {tag} ends. "
)
# What each picture is FOR, when the user has said. Without this the model has to guess from
# the picture alone, and it guesses "this is the scene" -- which is how a reference showing the
# cast in the finished set ends up rendered as-is AND acted out a second time by the same
# puppet. Only pictures with a note appear here; the rest are covered by the paragraph above.
REFERENCE_ROLES = "What each reference is for: {roles} "
# MiniMax's official reference-generation labels. Used only on the ref2va path; the
# keyframe scaffold stays a concatenation so chain / bridge prompts stay byte-identical.
# The six names are what the text encoder is trained to organise a mixed image+video pack
# with. RunDiffusion's beginner `Image 1` wording is their UI, not the model -- tags stay
# `<Picture N>` / `<Video 1>`.
REF_SUMMARY_PREFIX = "[reference generation]"
# Aric Vale / architecture-team sheets on RunDiffusion open ON THE SHEET for ~0.5 s, then
# dissolve into the first cinematic frame, unless the prompt maps regions and says the
# sheet is not a start frame. Our character sheets are identity-only (no storyboard half),
# so the map names the four CHAR_SHEET_LAYOUT sections as look, not as shots. Unhashed:
# the hashed note is still STAGE_ROLE.
SHEET_REGIONS = (
    "A character model sheet is one image with four labeled sections: (1) the turnaround "
    "defines identity, silhouette and wardrobe; (2) the expressions are available faces, "
    "not shots to cut to; (3) the head details lock the face; (4) the palette locks colour. "
    "Do not display the sheet, do not cut between its cells, do not treat those cells as "
    "shots, and do not copy any printed labels into the film. "
)
# Previous-clip audio is not a voice reference -- REF_VIDEO_WITH_AUDIO is off, and this
# production has no dialogue. Named anyway: H3 will take vocal timbre from a video with
# sound unless told not to, which is how leftover speech from the last beat leaks in.
VIDEO_NO_VOICE = (
    "Do not take voice, dialogue, speech, or music from {tag}. "
)
CAMERA_IDEA_LOCK = (
    "One main camera idea: static, locked-off -- no pan, tilt, push, pull, or cut. "
)
CAMERA_IDEA_TRAVEL = (
    "One main camera idea: the rig stays locked; locomotion is a background pull, not a pan. "
)
SHOT_ENDING = (
    "End on the landing of that action, same framing, camera still locked. "
)
# Medium.audio is still "Audio: …, no music, no speech." on the keyframe path. The reference
# scaffold splits that into overall_soundscape + non_diegetic_music: N/A, so this strips
# the wrapper the labeled sections replace.
_AUDIO_BAN_RE = re.compile(r",?\s*no music,?\s*no speech\.?\s*$", re.IGNORECASE)
# H3 takes a last frame as well as a first, and this is what has to be said when both are
# supplied. Without it the model treats the second image as another shot to cut to, and the
# clip arrives there early and then sits still -- or worse, jumps. Said this way, the two
# stills become the two ends of one move and the beat is the move between them.
ARRIVE_ON_LAST = (
    "A final frame is provided as well: this take must arrive at exactly that composition, "
    "and reach it only on its very last frame. Treat it as the pose, position and framing "
    "this same continuous move settles into at the end -- not a different shot, not somewhere "
    "to jump to, and not somewhere to arrive early and then wait. Everything between the two "
    "provided frames is one unbroken take at an even, unhurried pace, and the set, camera, "
    "lighting and every subject's on-screen size are identical in both, so nothing but the "
    "moving subject's pose may differ between them -- no grow, no shrink, no reframe. "
)
# The style bible, verbatim. Over 5-10 seconds of sampling a generic "keep the character
# identical" has nothing to hold on to, so the model drifts towards its own idea of a
# paper fox rather than the one this board designed.
IDENTITY_PREFIX = (
    "The characters and the set are already designed and must not be reinterpreted: "
)
# Closes the video prompt. H3 has no negative-prompt socket -- MiniMax's papercraft
# skill and the fal H3 guide both put the exclusions last, as a block, and they land
# when they name a failure rather than a mood. Same prefix Papercut's Gemini transport
# uses (`Avoid: `), so one `Medium.avoid` string is what both models hear. Fingerprints
# do not hash the scaffold, so adding this does not mark existing clips stale.
AVOID_PREFIX = "Avoid: "
# The beat's own scene line. The style bible says what the production looks like everywhere;
# this says where THIS shot is and at what scale, which is the part that differs between
# beats and the part the model otherwise has to guess at from one still. Labelled rather
# than woven into a sentence, because a scene line is sometimes a place ("a cobblestone
# street at twilight") and sometimes a framing ("macro close-up of the lantern housing"),
# and no single connecting phrase reads correctly for both.
SCENE_PREFIX = "Scene: "
# The beat's blocking: where things stand in THIS frame and what the set holds. The gap it
# fills is real and was measurable in the panels before it existed -- `scene` is one line and is
# deliberately shared across every beat of a continuous shot, so a shot where the character
# crosses from left to right has one scene line for both halves and nothing anywhere that says
# which half is which. The style bible says what things look like, the design sheets say it
# again for named things, and none of the three says where anything IS.
#
# Labelled rather than woven in, for `SCENE_PREFIX`'s reason one notch further: a blocking line
# is a list of positions ("the lantern low in the right third, the moth entering frame left")
# and no connecting phrase makes a list read as a sentence.
BLOCKING_PREFIX = "In frame: "
CRAFT = (
    " Animate it as real paper puppetry: crisp cut edges, visible paper grain, layered "
    "cardstock depth with soft contact shadows, joints pivoting like split-pin cutouts. "
    "Keep every character's face, markings, proportions, colours, paper texture, "
    "decorative cut-paper details, outline weight, and scale identical in every frame. "
    # Deliberately generic. This used to name the flowers, sun and clouds of the board it
    # was written for, which on a board without them is an instruction to invent them.
    "Keep the set, the background layers, the lighting, and the camera completely static. "
    "One locked-off framing for the whole clip -- no push-in, pull-back, pan, tilt, zoom, "
    "reframe, or cut to a second angle inside the beat. Hold every subject's on-screen "
    "size constant: a puppet that opens at one height in the frame stays that height "
    "unless the action explicitly walks them toward or away from the camera -- never "
    "grow, shrink, or rescale mid-clip. Nothing transforms, duplicates, slides, rotates, "
    "or changes design. No camera movement, no cuts, no new objects, no text, no "
    "watermarks. Smooth temporal consistency and natural foot contact."
)
AUDIO_SUFFIX = (
    " Audio: soft paper rustling and quiet birdsong in a sunny forest, no music, no speech."
)

# ## Lateral travel: a background pull, not a walk-cycle on a treadmill
#
# 9:16 is too narrow for a chase to read as "puppet crosses a locked wide". Cutout
# locomotion on a table is a background pull: the camera rig stays locked, you slide the
# set layers the other way, the puppet holds its third and plants against moving ground.
# The brief already allows a slow rail slide; the locked-hold tail of Medium.craft banned it,
# and that is how H3 was told to fake walking.
#
# Detected from the action, not a stored field. Toward/away from camera is size change and
# is not this. Climbing, dropping, raising stay on the locked-hold tail.
#
# The craft swap REPLACES the "Keep the set, …" tail rather than appending a contradiction.
# H3 given both "background static" and "slide the set" interpolates a walk-cycle in place.
# Every Medium.craft holds that mark; identity sentences before it stay.
CRAFT_HOLD_MARK = "Keep the set,"
TRAVEL_CRAFT_HOLD = (
    "The camera rig stays locked -- no push-in, pull-back, pan, tilt, zoom, reframe, or "
    "cut to a second angle. Locomotion is a background pull on the table: the set layers "
    "translate opposite the walk, same pieces and architecture shifting in the frame, "
    "new ground entering from the direction of travel. The puppets hold their on-screen "
    "size and roughly the same screen third; their gait plants against the sliding ground. "
    "This is real travel, not a walk-cycle in place -- do not freeze the background, do "
    "not animate legs while the set stays planted. Nothing transforms, duplicates, or "
    "changes design. No camera movement, no cuts, no new objects, no text, no watermarks. "
    "Smooth temporal consistency and natural foot contact."
)
# What Gemini is told on a travel pose / opening still. The image server's chain clause
# otherwise locks background position to the previous frame, which is the treadmill.
TRAVEL_POSE_NOTE = (
    "Locomotion is a background pull: keep the puppets in the same screen third and "
    "on-screen size; translate the set layers opposite the walk (same pieces, same "
    "architecture, shifted in the frame). This is real travel, not a walk-cycle on a "
    "frozen set."
)
TRAVEL_STILL_NOTE = (
    "This opening still is frame one of a background pull, not a locked-camera cross. "
    "Park the subject in the screen third they will hold for the clip -- not at the "
    "exit edge with the destination empty. The set will slide through later poses."
)
# Verb plus a lateral direction nearby, or an explicit left-to-right. "walk toward the
# camera" has no left/right/across and does not match.
_LATERAL_TRAVEL_RE = re.compile(
    r"(?:"
    r"(?:walk(?:s|ing)?|cross(?:es|ing)?|run(?:s|ning)?|chase(?:s|ing)?|"
    r"slide(?:s|ing)?|leap(?:s|ing)?|bound(?:s|ing)?|dash(?:es|ing)?|"
    r"move(?:s|ing)?|travel(?:s|ing)?|procession|keep walking)"
    r".{0,48}?"
    r"(?:left|right)"
    r"|"
    r"(?:left|right)[\s-]?to[\s-]?(?:right|left)"
    r"|"
    r"across the (?:path|frame|shot|street|road|garden)"
    r")",
    re.IGNORECASE,
)
# A real cross names both sides or names the path. "slides into frame from the upper
# left" matches the verb+left arm and is an entrance, not locomotion that would exit 9:16.
_LATERAL_SPAN_RE = re.compile(
    r"(?:left|right)[\s-]?to[\s-]?(?:right|left)|"
    r"from (?:the )?(?:far )?(?:right|left)\b.{0,24}\bto (?:the )?(?:far )?(?:left|right)|"
    r"across the (?:path|frame|shot|street|road|garden)|"
    r"\b(?:to the |towards? the )(?:left|right)\b",
    re.IGNORECASE,
)
_FROM_EDGE_RE = re.compile(
    r"\bfrom the (?:upper |lower |far )?(?:left|right)\b",
    re.IGNORECASE,
)
_TRAVEL_RIGHT_RE = re.compile(
    r"left[\s-]?to[\s-]?right|(?:to|towards?) the right|\brightward\b",
    re.IGNORECASE,
)


def is_travel(action: str) -> bool:
    """Lateral travel that would exit 9:16. Toward/away from camera is size, not a pull."""
    text = " ".join(str(action or "").split())
    if not text or not _LATERAL_TRAVEL_RE.search(text):
        return False
    if _FROM_EDGE_RE.search(text) and not _LATERAL_SPAN_RE.search(text):
        return False
    return True


def travel_way(action: str) -> str:
    """Which way the subject travels: 'left' or 'right'. The set pulls the other way."""
    text = " ".join(str(action or "").split())
    return "right" if _TRAVEL_RIGHT_RE.search(text) else "left"


def travel_digest(action: str) -> str:
    """Fingerprint part -- empty when the beat is not a pull, so locked boards keep their hash."""
    return "travel:pull" if is_travel(action) else ""


def craft_for(look: Medium, travel: bool = False) -> str:
    """Medium.craft, with the locked-hold tail swapped on a pull.

    Identity sentences stay. A missing mark (a future medium that rewords the tail) appends
    rather than silently keeping the locked hold -- better a doubled instruction than a
    travel beat that still says the garden is static.
    """
    if not travel:
        return look.craft
    mark = look.craft.find(CRAFT_HOLD_MARK)
    if mark < 0:
        return look.craft.rstrip() + " " + TRAVEL_CRAFT_HOLD
    return look.craft[:mark] + TRAVEL_CRAFT_HOLD


def pose_role(index: int, total: int, *, travel: bool = False, action: str = "") -> str:
    """What `<Picture N>` is, when that picture is a stop-motion pose of this shot.

    `action` is the beat's action line, and it fills the role's `{phase}` with the same
    `pose_phase` sentence the pose was drawn from -- so the video model is told what moment
    each picture shows, not just that it is one of a sequence. Roles are hashed off
    `pictures_for`, so this wording marks a sequence beat edited; that is correct (the
    prompt the render would get changed) rather than an accident.
    """
    if index <= 1:
        return REF_ROLE_OPENING
    template = REF_ROLE_POSE_TRAVEL if travel else REF_ROLE_POSE
    return template.format(i=index, k=total, phase=pose_phase(index, total, action))


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


def pose_need(action: str, seconds: float | int) -> int:
    """How many Gemini keyframes this beat needs before H3 interpolates the rest.

    MiniMax H3 on ref2va interpolates through supplied poses. Filling nine sockets was
    Gemini pre-drawing those in-betweens, which crowded identity sheets out of the pack.
    One still is the guide case. Two cases we have actually measured need more: a 10s
    take (243 frames, puppet drop mid-transform) gets opening + landing; lateral travel
    gets opening / mid-slide / landing because one still plus "walk left" produced a
    treadmill. Extra characters do not add poses -- those are sheets.
    """
    if is_travel(action):
        return 3
    try:
        length = float(seconds)
    except (TypeError, ValueError):
        length = BEAT_LENGTHS[-1]
    if length >= BEAT_LENGTHS[-1]:
        return 2
    return 1


def sequence_length(reserved: int, wanted: int | None = None) -> int:
    """How many stop-motion poses a beat should draw, given slots already spoken for.

    `reserved` is director uploads plus identity sheets on disk -- pictures with no
    words fallback. `wanted` is the generate target: `pose_need` when `STILL_SEQUENCE`
    is 0 (auto), a positive pin otherwise. Nine (or any pin at the node's cap) restores
    the old fill of remaining sockets.
    """
    room = max(1, MAX_REF_IMAGES - max(0, reserved))
    if wanted is None:
        wanted = STILL_SEQUENCE or MAX_REF_IMAGES
    return max(1, min(int(wanted), room, MAX_REF_IMAGES))


def panel_frame_copy() -> str:
    """What the PANEL_SEQUENCE sketches of a beat are, in words the writer is told."""
    if PANEL_SEQUENCE <= 1:
        return "one opening sketch of that beat's action"
    return "opening, then through the action, then the landing"


def pose_phase(index: int, total: int, action: str) -> str:
    """Where in the beat's action this pose sits, in words the still model can draw.

    Papercut's own `beatHint` is a left-to-right walk, which is the wrong action for most
    shots. This one names the beat's actual action and only the phase changes, so pose 4 of
    7 of "she raises the lantern" is the lantern partway up, not a step to the right.

    Lateral travel names how far the WORLD has slid, not just the gait: poses that only
    change limb silhouette against a glued-down set are how H3 fakes walking.
    """
    said = " ".join(str(action or "").split()) or "the action"
    if is_travel(action):
        return _travel_phase(index, total, said, travel_way(action))
    if total <= 1:
        return f"single opening pose, before {said}"
    p = index / (total - 1)
    if p == 0:
        return f"the opening: {said} has not started, weight settled, limbs at rest"
    if p < 0.35:
        return f"{said} has just begun, the first increment of the move"
    if p < 0.65:
        return f"the midpoint of {said}, the pose at its widest, strongest silhouette"
    if p < 1:
        return f"{said} is nearly complete, follow-through in the trailing limbs"
    return f"{said} has just completed, weight settled again"


def _travel_phase(index: int, total: int, said: str, way: str) -> str:
    """Pose phase for a background pull. `way` is which way the subject travels."""
    pull = "right" if way == "left" else "left"
    if total <= 1:
        return (
            f"single opening pose of a background pull: {said} has not started, "
            f"subject holding their screen third, set not yet slid"
        )
    p = (index - 1) / (total - 1)
    if p == 0:
        return (
            f"the opening of a background pull: {said} has not started, weight settled, "
            f"subject in the screen third they will hold, set not yet pulled"
        )
    if p < 0.35:
        return (
            f"{said} has just begun: subject holds the same screen third, gait's first "
            f"increment, set layers starting to slide {pull}"
        )
    if p < 0.65:
        return (
            f"the midpoint of {said}: subject still in the same screen third, stride at "
            f"its widest, set pulled about halfway {pull}, new ground entering from the {way}"
        )
    if p < 1:
        return (
            f"{said} is nearly complete: subject still in the same third, follow-through "
            f"in the trailing limbs, set almost fully pulled {pull}"
        )
    return (
        f"{said} has just completed: subject still in the same third, weight settled, "
        f"set pulled through the travel {pull}"
    )


def panel_phase(index: int, total: int) -> str:
    """Where in the beat this graphite frame sits: opening, midpoint, or landing.

    Default is one opening sketch — H3 interpolates the action. Three is the old
    stop-motion board that locked a nine-pose fill. In between those, only the ends
    are named and the rest are counted, so a four-panel board does not invent a
    second midpoint.
    """
    if index <= 1:
        return "opening"
    if total <= 1 or index >= total:
        return "landing"
    if total == 3:
        return "midpoint"
    return f"pose {index} of {total}"


def panel_role(index: int, total: int) -> str:
    """What `<the Nth reference image>` is, when that image is a storyboard panel."""
    if total <= 1:
        return REF_ROLE_PANEL
    return REF_ROLE_PANEL_FRAME.format(
        i=index, k=total, phase=panel_phase(index, total),
    )


def reference_tags(count: int, *, start: int = 1) -> str:
    """The prompt's name for the supplied references: "<Picture 1>, <Picture 2> and <Picture 3>".

    1-based and in connection order, which is what the text encoder was trained on. The graph
    sockets are 0-based, so this deliberately does not match the key names in comfy.build_graph.
    """
    tags = [f"<Picture {i}>" for i in range(start, start + max(0, count))]
    if len(tags) <= 1:
        return "".join(tags)
    return ", ".join(tags[:-1]) + " and " + tags[-1]


def reference_roles(notes: list[str]) -> str:
    """One sentence per described picture: "<Picture 1> is the Moth puppet itself."

    Positional: notes[0] describes <Picture 1>. Undescribed pictures are skipped rather than
    given a placeholder -- an empty note means the user had nothing to add, not that the
    picture is unimportant, and inventing a role for it would be worse than silence.
    """
    said = [
        f"{tag} is {note.strip().rstrip('.')}."
        for tag, note in ((f"<Picture {i}>", note) for i, note in enumerate(notes, start=1))
        if note and note.strip()
    ]
    return " ".join(said)


# A director naming one particular picture inside a prompt, so a field can say "@ref:a1b2c3
# swings the club" instead of describing the club again.
#
# The token carries the picture's ID, not its number, and that is the whole design. The same
# stored string is read by two prompt builders with two incompatible orderings -- the video
# model gets `pictures_for` (own still, identity sheets or the cast still, uploads) tagged
# `<Picture N>`, the still model
# gets `still_pictures` (identity sheets or the cast still, then the storyboard panels, then
# uploads on a reference join, capped at nine) with no tags at all -- so one literal
# expansion cannot be correct in both places, and a number typed into prose is persisted
# derived state, which is the thing `board.py` exists to not have. `ref_offset` alone moves
# when beat 1's still lands, when a character.png is uploaded, when carry is ticked, and when
# the join is cycled: four events that touch no text and would silently relabel every literal.
CAST_MENTION = "cast"
# `@stage:` names a reel-level staging sheet rather than a beat's own picture. The two token
# spaces are separate because the two ids are minted independently -- a beat's `ref_ids` and the
# board's staging entries are different lists with no shared counter -- so a bare hex body could
# name either. The bodies below are namespaced for the same reason: `mentions()` is one dict, and
# a staging entry that happened to mint the same six characters as a picture on beat 3 would
# otherwise resolve onto whichever was inserted last.
STAGE_MENTION_PREFIX = "stage:"
MENTION_RE = re.compile(
    r"@(?:ref:([0-9a-f]{4,12})|stage:([0-9a-f]{4,12})|(cast))(?![\w:])"
)

# What @cast degrades to when the render it lands in is not conditioned on the cast reference.
# Short on purpose: REF_ROLE_CAST is a whole paragraph, correct as the answer to "what is
# <Picture 2> for" and absurd spliced mid-sentence in place of two words.
CAST_MENTION_ROLE = "this reel's cast reference"


def _mention_body(match: "re.Match[str]") -> str:
    """Which picture a matched token names, namespaced so the two id spaces cannot collide."""
    if match.group(1):
        return match.group(1)
    if match.group(2):
        return STAGE_MENTION_PREFIX + match.group(2)
    return CAST_MENTION


def mention_token(body: str) -> str:
    """The literal a field stores to name one picture. The one place the spelling is decided."""
    if body == CAST_MENTION:
        return "@cast"
    if body.startswith(STAGE_MENTION_PREFIX):
        return f"@stage:{body[len(STAGE_MENTION_PREFIX):]}"
    return f"@ref:{body}"


def mention_bodies(text: str) -> list[str]:
    """Every picture named in this text, in order, duplicates kept.

    A multiset rather than a set: the post-check that catches a model rewriting a token away
    has to notice "said it twice, now says it once" as well as "dropped it entirely".
    """
    return [_mention_body(match) for match in MENTION_RE.finditer(text or "")]


def lost_mentions(before: str, after: str) -> list[str]:
    """Tokens a rewrite dropped, as the literals they were written as.

    A multiset difference rather than a set one: "named it twice, now names it once" is the same
    class of loss as "dropped it entirely", and both leave a picture the render is no longer
    told about.

    It cannot be repaired -- only the model knows where in its new sentence it meant them -- so
    every caller does the same thing with the answer: logs it, and puts it in the transcript the
    director already reads to find out why an image changed. That turns a silent loss into a
    visible line, which is the whole ambition.
    """
    kept = mention_bodies(after)
    lost: list[str] = []
    for body in mention_bodies(before):
        if body in kept:
            kept.remove(body)
        else:
            lost.append(mention_token(body))
    return lost


def guarded_text(before: str, after: str) -> tuple[str, list[str]]:
    """Keep `after` only when it retained every @-token `before` had.

    A rewrite that drops a token would render a shot no longer told about a picture it is
    still conditioned on. Unrepairable -- only the model knows where it meant them -- so
    the stored string stays put and the caller logs `lost`.
    """
    before = " ".join(str(before or "").split()).strip()
    after = " ".join(str(after or "").split()).strip()
    if not after or after == before:
        return before, []
    lost = lost_mentions(before, after)
    if lost:
        return before, lost
    return after, []


def expand_mentions(text: str, mentions: dict[str, tuple[int | None, str]] | None,
                    *, prose: bool = False) -> str:
    """Turn every @-token into whatever the model reading this text can act on.

    `mentions` maps a token body to `(its position in THIS consumer's picture list, what it is
    for)`. `prose=False` writes the video model's `<Picture N>`; `prose=True` writes an ordinal
    for the still model, whose prompt carries no tags at all.

    The ordinal is the weakest link in the whole feature and the role is appended as a hedge:
    "the 2nd reference image" asks a four-step distilled model to count its conditioning
    images, and `papercut._beat_text`'s existing "The reference images show: ..." clause is
    unnumbered, so the ordinal has no antecedent in the prompt it lands in. Reasoned, not
    measured -- the same register as the multi-picture note in CLAUDE.md.

    A token whose picture is not in this consumer's list -- truncated past MAX_REF_IMAGES,
    past MAX_STILL_REFS on the still side, or simply not conditioning this render -- degrades
    to the role text, and to nothing when there is no role. Never emit a position for a
    picture the model was not given: a prompt that says "<Picture 5>" over four pictures is
    worse than one that says nothing, because the model answers it anyway.

    `mentions=None` returns the text untouched, which is what keeps `reel.py` -- no board, so
    nothing to resolve against -- composing byte-identical prompts.
    """
    if mentions is None or not text:
        return text

    def swap(match: "re.Match[str]") -> str:
        position, role = mentions.get(_mention_body(match), (None, ""))
        role = " ".join((role or "").split()).rstrip(".")
        if position is None:
            return role
        if not prose:
            return f"<Picture {position}>"
        return f"the {_ordinal(position)} reference image" + (f", {role}" if role else "")

    # Collapsing whitespace afterwards, because a token that expanded to nothing leaves the
    # spaces either side of it behind and " ,  ." reads as a typo the model tries to explain.
    return " ".join(MENTION_RE.sub(swap, text).split()).replace(" ,", ",").replace(" .", ".")


def drop_mention(text: str, body: str, role: str) -> str:
    """Rewrite mentions of one picture out of a text, leaving what it was FOR behind.

    Called when the picture is deleted. `expand_mentions` would drop the token silently at
    render time, which reads on screen as a sentence that still names something -- so the
    board is rewritten instead, once, where it can be seen.
    """
    role = " ".join((role or "").split()).rstrip(".")
    swapped = MENTION_RE.sub(
        lambda match: role if _mention_body(match) == body else match.group(0), text or ""
    )
    return " ".join(swapped.split()).replace(" ,", ",").replace(" .", ".")


def _ordinal(position: int) -> str:
    """1 -> "1st". Small integers only; there are at most nine pictures."""
    if 10 <= position % 100 <= 20:
        return f"{position}th"
    return f"{position}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(position % 10, 'th') }"


# Handed to every model that rewrites a field which may contain tokens -- five of them, listed
# in CLAUDE.md. One copy for the reason ASSET_STYLE_SUFFIX is one copy: two prompts written
# from two summaries of the same rule drift, and nothing fails when they do.
#
# The automatic still review is the one this is really for. It fires without anyone asking and
# is told to correct "the problems and nothing else", which a model reads as licence to
# normalise an unfamiliar `@ref:a1b2c3` into prose -- and a lost token does not fail, it
# renders a shot conditioned on a picture nobody told the model about.
MENTION_NOTE = (
    "Some text you are given contains reference tokens that look like @ref:a1b2c3 or @cast. "
    "They are not words and not typos: each one names a specific picture attached to this "
    "shot, and it is replaced with that picture's number at render time. Copy every token you "
    "keep EXACTLY as it is written, characters and all. Never reword one, never renumber one, "
    "never turn one into a description. Delete one only if you are deliberately removing the "
    "thing it refers to."
)


def _soundscape(audio: str) -> str:
    """Medium.audio as an overall_soundscape line: drop the wrapper the labeled section replaces."""
    text = (audio or "").strip()
    if text.lower().startswith("audio:"):
        text = text[6:].strip()
    text = _AUDIO_BAN_RE.sub("", text).strip().rstrip(".")
    return (text + ".") if text else ""


def _slot_name(note: str) -> str:
    """Leading name from a hashed role string. STAGE_ROLE and named notes put it first."""
    text = " ".join((note or "").split()).strip()
    if not text:
        return ""
    if ", " in text:
        return text.split(", ", 1)[0].strip()
    if " -- " in text:
        return text.split(" -- ", 1)[0].strip()
    return text.split(".", 1)[0].strip()


def _kind_at(kinds: list[str] | None, index: int) -> str:
    """1-based picture index -> kind, or '' when the caller omitted kinds."""
    if not kinds or index < 1 or index > len(kinds):
        return ""
    return kinds[index - 1]


def _pad_notes(notes: list[str], count: int) -> list[str]:
    notes = list(notes or [])
    if len(notes) < count:
        notes.extend([""] * (count - len(notes)))
    return notes[:count]


def _normalize_kinds(kinds: list[str] | None, count: int) -> list[str] | None:
    """None means the CLI path: no Subject IDs. A list is padded to `count`."""
    if kinds is None or count <= 0:
        return None if kinds is None else []
    padded = list(kinds)
    if len(padded) < count:
        padded.extend([""] * (count - len(padded)))
    return padded[:count]


def _design_ref_paragraphs(refs: int, *, poses: int, opens_on: bool, travel: bool,
                            surface: str) -> list[str]:
    """OPEN_REFERENCE / SEQUENCE covering the pictures that are not the opening still.

    Picture 1 on a cut is the opening composition; treating it as one more design sheet is
    how RunDiffusion's combined sheets flash on screen for the first half-second.
    """
    if refs <= 0:
        return []
    lines: list[str] = []
    if poses > 1:
        sequence = OPEN_REFERENCE_SEQUENCE_TRAVEL if travel else OPEN_REFERENCE_SEQUENCE
        lines.append(sequence.format(
            tags=reference_tags(min(poses, refs)), first="<Picture 1>"))
        rest = refs - min(poses, refs)
        if rest > 0:
            count = rest
            lines.append(OPEN_REFERENCE.format(
                tags=reference_tags(rest, start=poses + 1), surface=surface,
                verb="is" if count == 1 else "are"))
        return lines
    if opens_on and refs > 1:
        count = refs - 1
        lines.append(OPEN_REFERENCE.format(
            tags=reference_tags(refs - 1, start=2), surface=surface,
            verb="is" if count == 1 else "are"))
    elif not opens_on:
        lines.append(OPEN_REFERENCE.format(
            tags=reference_tags(refs), surface=surface,
            verb="is" if refs == 1 else "are"))
    return lines


def _subject_definitions(refs: int, notes: list[str], kinds: list[str] | None, *,
                         opens_on: bool, poses: int, ref_videos: int, hold_video: bool,
                         travel: bool, surface: str) -> str:
    """Picture vs subject, every role named. MiniMax: the picture is the file, the subject
    is the reusable thing taken from it.
    """
    lines = _design_ref_paragraphs(
        refs, poses=poses, opens_on=opens_on, travel=travel, surface=surface)
    notes = _pad_notes(notes, refs)
    has_character = False
    if kinds is not None:
        subject_i = 0
        for index, (kind, note) in enumerate(zip(kinds, notes), start=1):
            tag = f"<Picture {index}>"
            said = " ".join((note or "").split()).strip().rstrip(".")
            if kind == REF_KIND_OPENING or (index == 1 and opens_on and poses <= 1
                                            and kind in ("", REF_KIND_OPENING)):
                lines.append(
                    f"{tag} is this shot's own opening composition, not a design sheet and "
                    "not a start-frame latent. Begin the clip toward it. Do not display any "
                    "other picture as a frame."
                )
                if said:
                    lines.append(f"{tag} is {said}.")
            elif kind == REF_KIND_POSE:
                if said:
                    lines.append(f"{tag} is {said}.")
            elif kind in (REF_KIND_CHARACTER, REF_KIND_PROP, REF_KIND_SET):
                subject_i += 1
                name = _slot_name(note) or f"subject {subject_i}"
                role = "location" if kind == REF_KIND_SET else "identity"
                lines.append(
                    f"<Subject {subject_i}> is {name}, whose {role} comes from {tag}."
                )
                if said:
                    lines.append(f"{tag} is {said}.")
                if kind == REF_KIND_CHARACTER:
                    has_character = True
            elif kind == REF_KIND_CAST:
                lines.append(
                    f"{tag} is this reel's locked cast reference: it fixes what the characters "
                    "and the materials look like, and it is NOT this shot's setting or "
                    "framing. Do not display it as a frame."
                )
                if said:
                    lines.append(f"{tag} is {said}.")
            elif said:
                lines.append(f"{tag} is {said}.")
            elif kind == REF_KIND_UPLOAD:
                lines.append(f"{tag} is a director-supplied reference for this shot.")
    else:
        if opens_on and refs > 0 and poses <= 1:
            lines.append(
                "<Picture 1> is this shot's own opening composition, not a design sheet and "
                "not a start-frame latent. Begin the clip toward it. Do not display any "
                "other picture as a frame."
            )
        roles = reference_roles(notes)
        if roles:
            lines.append(roles)
    if has_character:
        lines.append(SHEET_REGIONS)
    if ref_videos > 0:
        if hold_video:
            lines.append(
                "<Video 1> locks identity, materials, motion and the set from the previous "
                "clip. This shot still begins on its own opening composition. Do not replay "
                "it, do not cut to it, and do not re-establish the scene from it."
            )
        else:
            lines.append(
                "<Video 1> provides motion and pacing from the previous take. Do not replay "
                "it, do not cut to it, and do not copy its subjects as extra characters."
            )
        lines.append(VIDEO_NO_VOICE.format(tag="<Video 1>"))
    return " ".join(line.strip() for line in lines if line and line.strip())


def _retention_analysis(refs: int, notes: list[str], kinds: list[str] | None, *,
                        opens_on: bool, poses: int, ref_videos: int,
                        hold_video: bool) -> str:
    """One MiniMax retention verb per label. Unhashed: STAGE_ROLE / REF_ROLE_* stay put.

    Character and prop notes land on the fully_preserved line so H3 is told the named
    features to keep (oval face, copper curls), which is the guide's "preserve X" rule
    rather than "use the same woman". Fingerprints do not hash this section.
    """
    lines: list[str] = []
    notes = _pad_notes(notes, refs)
    for index in range(1, refs + 1):
        tag = f"<Picture {index}>"
        kind = _kind_at(kinds, index)
        said = " ".join((notes[index - 1] or "").split()).strip().rstrip(".")
        is_opening = (
            kind == REF_KIND_OPENING
            or (index == 1 and opens_on and poses <= 1)
            or (index == 1 and poses > 1)
        )
        is_pose = kind == REF_KIND_POSE or (poses > 1 and 1 < index <= poses)
        if is_opening and not is_pose:
            lines.append(
                f"{tag} (appears in [Shot 1]): fully_preserved - retain framing, subject "
                "sizes, set dressing and lighting at the opening; interpolate the action "
                "from there."
            )
        elif is_pose:
            lines.append(
                f"{tag} (appears in [Shot 1]): attribute_transfer - transfer this pose of "
                "the same locked-off take; do not treat it as a different camera or a "
                "second puppet."
            )
        elif kind == REF_KIND_CHARACTER:
            retain = (f"Preserve {said}." if said else
                      "retain identity, silhouette, markings, colours, materials and wardrobe.")
            lines.append(
                f"{tag} (appears in [Shot 1]): fully_preserved - {retain}"
            )
        elif kind == REF_KIND_PROP:
            retain = (f"Preserve {said}." if said else
                      "retain the prop's shape, materials, colours and markings.")
            lines.append(
                f"{tag} (appears in [Shot 1]): fully_preserved - {retain}"
            )
        elif kind == REF_KIND_SET:
            lines.append(
                f"{tag} (appears in [Shot 1]): fully_preserved - retain the set's layout and "
                "dressing; it is empty of characters."
            )
        elif kind == REF_KIND_CAST:
            lines.append(
                f"{tag} (appears in [Shot 1]): fully_preserved identity, weak_reference for "
                "that camera - retain what the characters and materials look like; ignore "
                "that picture's framing."
            )
        elif kind == REF_KIND_UPLOAD:
            lines.append(
                f"{tag} (appears in [Shot 1]): partially_preserved - take what the role "
                "names; do not treat the picture as a second shot."
            )
        else:
            lines.append(
                f"{tag} (appears in [Shot 1]): fully_preserved - retain the subject it shows."
            )
    if ref_videos > 0:
        if hold_video:
            lines.append(
                "<Video 1>: fully_preserved identity - retain characters, materials, motion "
                "and set; do not copy the clip or its voice."
            )
        else:
            lines.append(
                "<Video 1> (motion and pacing): attribute_transfer - transfer motion and "
                "pacing without copying its visual subjects or voice."
            )
    return "\n".join(lines)


def _reference_summary(*, look: Medium, opens_on: bool, poses: int, refs: int,
                       ref_videos: int, hold_video: bool, kinds: list[str] | None,
                       notes: list[str]) -> str:
    bits = [REF_SUMMARY_PREFIX, f"Create a {look.name} clip."]
    if ref_videos > 0 and not hold_video:
        bits.append("Continue from the moment <Video 1> ends.")
    elif opens_on and refs > 0:
        bits.append("Begin from <Picture 1>.")
    elif refs > 0:
        bits.append("Compose the opening from the scene line.")
    names: list[str] = []
    if kinds is not None:
        for kind, note in zip(kinds, _pad_notes(notes, refs)):
            if kind in (REF_KIND_CHARACTER, REF_KIND_PROP, REF_KIND_SET):
                name = _slot_name(note)
                if name:
                    names.append(name)
    if names:
        bits.append("Named subjects: " + ", ".join(names) + ".")
    bits.append("Design references fix appearance only and are not shots.")
    if ref_videos > 0 and hold_video:
        bits.append("<Video 1> locks identity from the previous clip, not voice.")
    elif ref_videos > 0:
        bits.append("<Video 1> provides motion and pacing, not voice.")
    if poses > 1:
        bits.append("Interpolate through the stop-motion poses in order.")
    bits.append("One locked-off take.")
    return " ".join(bits)


def _opening_instructions(*, refs: int, opens_on: bool, poses: int, ref_videos: int,
                          hold_video: bool) -> list[str]:
    """Where this shot begins. Same precedence `build_prompt` used before the six-part split."""
    parts: list[str] = []
    if ref_videos > 0 and not hold_video:
        parts.append(CARRY_VIDEO.format(tag="<Video 1>"))
    elif ref_videos > 0 and hold_video:
        parts.append(HOLD_VIDEO.format(tag="<Video 1>"))
        if opens_on and refs > 0 and poses <= 1:
            parts.append(OPEN_REFERENCE_STILL.format(tag="<Picture 1>"))
    elif opens_on and refs > 0 and poses <= 1:
        parts.append(OPEN_REFERENCE_STILL.format(tag="<Picture 1>"))
    elif refs > 0 and poses <= 1:
        parts.append(COMPOSE_OPENING)
    return parts


def _detailed_description(*, look: Medium, opening: str, identity: str, staging: str,
                          blocking: str, scene: str, action: str, travel: bool,
                          refs: int, opens_on: bool, poses: int, ref_videos: int,
                          hold_video: bool) -> str:
    """Guide order: visual direction, then one [Shot 1] in playback order. Not padded.

    The official 350-500 word target is for a 15 s multi-shot montage. A 5 s or 10 s
    locked-off beat that invents extra words invents extra cuts.
    """
    visual = opening
    if identity:
        visual += IDENTITY_PREFIX + identity.rstrip(".") + ". "
    shot: list[str] = _opening_instructions(
        refs=refs, opens_on=opens_on, poses=poses, ref_videos=ref_videos,
        hold_video=hold_video,
    )
    if staging:
        shot.append(STAGING_PREFIX + staging + ". ")
    if blocking:
        shot.append(BLOCKING_PREFIX + blocking + ". ")
    if scene:
        shot.append(SCENE_PREFIX + scene + ". ")
    if action:
        shot.append(action + ". ")
    shot.append(CAMERA_IDEA_TRAVEL if travel else CAMERA_IDEA_LOCK)
    shot.append(SHOT_ENDING)
    shot.append(craft_for(look, travel))
    return visual.rstrip() + "\n\n[Shot 1] " + "".join(shot)


def _reference_prompt(*, look: Medium, opening: str, action: str, scene: str,
                      identity: str, staging: str, blocking: str, mute: bool,
                      refs: int, ref_notes: list[str], ref_kinds: list[str] | None,
                      ref_videos: int, opens_on: bool, poses: int, hold_video: bool,
                      travel: bool) -> str:
    """MiniMax six-part reference format. Keyframe joins never call this."""
    notes = _pad_notes(ref_notes, refs)
    kinds = _normalize_kinds(ref_kinds, refs)
    sections = [
        ("subject_definitions", _subject_definitions(
            refs, notes, kinds, opens_on=opens_on, poses=poses,
            ref_videos=ref_videos, hold_video=hold_video, travel=travel,
            surface=look.surface)),
        ("summary", _reference_summary(
            look=look, opens_on=opens_on, poses=poses, refs=refs,
            ref_videos=ref_videos, hold_video=hold_video, kinds=kinds,
            notes=notes)),
        ("retention_analysis", _retention_analysis(
            refs, notes, kinds, opens_on=opens_on, poses=poses,
            ref_videos=ref_videos, hold_video=hold_video)),
        ("detailed_description", _detailed_description(
            look=look, opening=opening, identity=identity, staging=staging,
            blocking=blocking, scene=scene, action=action, travel=travel,
            refs=refs, opens_on=opens_on, poses=poses, ref_videos=ref_videos,
            hold_video=hold_video)),
    ]
    if not mute:
        sections.append(("overall_soundscape", _soundscape(look.audio)))
        sections.append(("non_diegetic_music", "N/A"))
    text = "\n\n".join(
        f"{name}:\n{body.strip()}" for name, body in sections if body and body.strip()
    )
    if look.avoid:
        text += "\n\n" + AVOID_PREFIX + look.avoid.rstrip(".") + "."
    return text


def build_prompt(action: str, *, scene: str = "", mute: bool = False, identity: str = "",
                 continues: bool = False, lands: bool = False, refs: int = 0,
                 ref_notes: list[str] | None = None, ref_videos: int = 0,
                 opens_on: bool = False, staging: str = "", blocking: str = "",
                 medium_key: str | None = None,
                 mentions: dict[str, tuple[int | None, str]] | None = None,
                 poses: int = 0, hold_video: bool = False,
                 camera: str | None = None, travel: bool | None = None,
                 ref_kinds: list[str] | None = None) -> str:
    """Assemble the instruction for one beat.

    `identity` is the board's style bible -- what the characters and the set look like,
    never how they move. `scene` is the beat's own line: where it happens and at what
    scale. `action` is what moves. All three go in: the action alone leaves the model to
    infer the setting from a single still, which is where a background quietly turns into
    a different place halfway through a clip. `continues` says this beat opens on the
    previous clip's final frame rather than on a still of its own, which changes how the
    first frame must be read; see the scaffold above. `lands` says a final frame was given
    too, so the clip has a destination it must reach and not overshoot.

    `refs` is how many reference pictures this beat is conditioned on instead of a keyframe,
    and `ref_notes` is what each of them is FOR, by position -- the difference between the
    model treating a picture as the design of a character and treating it as the scene it
    should reproduce. Non-zero `refs` puts the beat on the ref2va checkpoint, which has no
    first or last frame inputs at all -- so `continues` and `lands` cannot apply and are
    ignored rather than silently
    describing frames the model was never given.

    `opens_on` says <Picture 1> is this beat's own still rather than a design reference, which
    is what a cut on this checkpoint is: the opening composition was drawn for this shot, so
    the clip begins on it instead of on something the model invents from the scene line. It is
    a flag rather than being inferred from `refs`, because the same picture count means the
    opposite thing on a beat whose references are all uploads of the cast.

    `poses` is how many of those pictures are a stop-motion sequence of THIS shot, counting
    from <Picture 1>. Zero or one keeps the old "design references plus an opening still"
    wording; two or more swaps that for OPEN_REFERENCE_SEQUENCE, because nine poses of one
    take are not nine design sheets.

    `hold_video` says a reference video is identity, not the opening. CARRY_VIDEO and
    HOLD_VIDEO are two answers to where the shot begins and must not both fire; an opening
    still (or a pose sequence) can sit next to HOLD_VIDEO, which is the whole point of
    sending the previous clip on a hard cut.

    `staging` is what the reel's bound design sheets say, for the ones this render was NOT
    handed as pictures -- a set on a beat that spent its slots on characters, or anything past
    the cap. A sheet that IS one of the pictures is described in `ref_notes` instead, by
    position, and saying it twice would have the model looking for two clearings. One rule,
    `Board.staging_text`, applied by every renderer.

    `blocking` is where things stand in this frame and what the set holds -- the one question
    the bible, the sheets and the scene line all leave open. It sits between the staging and the
    scene line; see `BLOCKING_PREFIX`.

    `medium_key` picks which medium's words wrap all of that: the opening clause, the material
    words in the reference paragraph, the craft clause and the audio. None means the default,
    which is what every board written before the bundle existed resolves to and is why their
    prompts are byte-identical to what they were.

    `camera` is the locked-off angle for this take. None or `eye` appends the same
    "shot straight-on. " Medium.shot used to end with, so a board that never named an
    angle composes the byte-identical prompt it always did. Any other key replaces that
    fragment -- which is the whole point of the field, because the panel used to name an
    angle that never reached this function.

    `travel` is a background pull: lateral travel that would exit 9:16. None means detect
    from the (expanded) action, so a forgotten caller still gets the pull clause rather
    than the locked-hold tail that makes H3 fake a walk. False keeps today's scaffold
    even if the action mentions "left". True swaps OPEN_REFERENCE_SEQUENCE and the
    craft hold; see `craft_for`.

    `mentions` resolves the @-tokens a director may have typed into any of the three texts.
    One keyword here rather than three expanded call sites, so every path into a render --
    studio, CLI, and whatever comes next -- gets it by construction rather than by remembering.
    None means no expansion, which is what `reel.py` (no board, so nothing to resolve against)
    passes and why its prompts are byte-identical to what they always were.

    `ref_kinds` is parallel to `ref_notes`, same length, same order: opening / pose / cast /
    character / prop / set / upload. It is not hashed -- the notes are. None skips Subject
    IDs (the CLI path); a list lets the six-part scaffold name `<Subject N>` and pick a
    retention verb per picture. See `Board.picture_kinds`.
    """
    look = medium(medium_key)
    action = expand_mentions(action, mentions)
    scene = expand_mentions(scene, mentions)
    ref_notes = [expand_mentions(note, mentions) for note in (ref_notes or [])]
    poses = max(0, int(poses or 0))
    if travel is None:
        travel = is_travel(action)
    opening = look.shot + camera_clause(camera)
    identity = " ".join(identity.split())
    staging = " ".join(expand_mentions(staging, mentions).split()).strip().rstrip(".")
    blocking = " ".join(expand_mentions(blocking, mentions).split()).strip().rstrip(".")
    scene = " ".join(scene.split()).strip().rstrip(".")
    action = action.strip().rstrip(".")
    if refs > 0 or ref_videos > 0:
        # MiniMax six-part reference format. The keyframe concatenation below is left
        # alone so chain / bridge prompts stay byte-identical.
        return _reference_prompt(
            look=look, opening=opening, action=action, scene=scene,
            identity=identity, staging=staging, blocking=blocking, mute=mute,
            refs=refs, ref_notes=ref_notes, ref_kinds=ref_kinds,
            ref_videos=ref_videos, opens_on=opens_on, poses=poses,
            hold_video=hold_video, travel=travel,
        )
    parts = [opening, OPEN_CONTINUATION if continues else OPEN_CUT]
    if lands:
        parts.append(ARRIVE_ON_LAST)
    if identity:
        parts.append(IDENTITY_PREFIX + identity.rstrip(".") + ". ")
    # Straight after the style bible, because it is the same claim about more specific things:
    # the bible says what the production looks like, these say what two named things in it look
    # like. Before the scene line, so "Scene: the clearing at dusk" is read against a clearing
    # that has already been described rather than one the model has just invented.
    if staging:
        parts.append(STAGING_PREFIX + staging + ". ")
    # Between the designs and the scene line, because it is the answer to a question those two
    # leave open. The bible says what things look like and the scene line says where the shot is
    # and at what scale; neither says where in THIS frame anything stands, and left unsaid the
    # model re-blocks the set every beat. Before the scene line rather than after, so a reader
    # has the set in mind before being told what is standing in it.
    if blocking:
        parts.append(BLOCKING_PREFIX + blocking + ". ")
    if scene:
        parts.append(SCENE_PREFIX + scene + ". ")
    if action:
        parts.append(action + ".")
    parts.append(craft_for(look, travel))
    if not mute:
        parts.append(look.audio)
    # Last, matching the fal H3 "limits" block: what must not appear, after what must.
    # The still path sends the same string as Papercut's `negativePrompt`; see `Medium.avoid`.
    if look.avoid:
        parts.append(AVOID_PREFIX + look.avoid.rstrip(".") + ". ")
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
