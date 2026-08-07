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
# checkpoints, and they are 19.5 GiB each -- kept on the Volume, which costs disk, not VRAM.
# ComfyUI loads whichever the graph asks for and evicts the other, so a batch pays one model
# swap per switch. Rendering in beat order keeps that to one swap per shot boundary, which is
# what the batch already does for chaining's sake.
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
MAX_REF_VIDEOS = 3
# Two of those nine slots fill themselves on a beat that opens a shot, which is why an upload
# budget exists rather than a flat nine: <Picture 1> is the beat's own generated still -- the
# composition this shot opens on -- and <Picture 2> is the reel's locked cast reference. The
# remaining seven are the director's. `Board.pictures_for` is where the order is decided; the
# roles below are the words each auto-wired slot is described to the model with.
#
# The two are the reason this join is the default at all: the still alone is one shot's
# composition and drifts towards its own reading of the style bible over ten seconds, and the
# cast reference alone fixes the characters but not where the shot opens. Together they are
# what a keyframe cut had (an exact opening) plus what it never had (the cast, re-asserted
# through every sampling step).
REF_ROLE_OPENING = (
    "the composition this shot opens on: its set, its framing, its subject scale and its "
    "lighting are the ones this whole clip holds"
)
REF_ROLE_CAST = (
    "this reel's locked cast reference -- it fixes what the characters and the materials look "
    "like everywhere in the film, and it is NOT this shot's setting or framing"
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
# The clip's own soundtrack, paired to the video as `ref_video_audio_N`. Off by default:
# H3 generates each beat's audio anyway, and an audio reference is one more thing for the
# model to reproduce literally. Turn it on if ambience drifts between beats.
REF_VIDEO_WITH_AUDIO = False

# ## The language model
#
# One local model does every job that is words: writing the script, carrying out the board
# edits a conversation asks for, writing the caption, and -- because it has vision -- looking
# at each still the image server produced and saying whether it belongs in this reel.
#
# It replaced the Antigravity CLI, and one number is why: agy's image tool allowed roughly
# five generations per five-hour window and its agent turns billed against the same plan
# quota, so every turn had a price. Ollama on this machine has none, which is what makes the
# self-review passes below affordable -- they were never worth a quota slot.
OLLAMA_URL = os.environ.get("PAPERREEL_OLLAMA_URL", "http://127.0.0.1:11434")
QWEN_MODEL = os.environ.get("PAPERREEL_QWEN_MODEL", "qwen3.6")
# Vision is a separate name only so a machine whose main model is text-only can still point
# this at one that sees. Same model by default: qwen3.6 reports `vision` and `tools` and
# `thinking` together, which is the whole reason it can drive this pipeline alone.
QWEN_VISION_MODEL = os.environ.get("PAPERREEL_QWEN_VISION_MODEL", QWEN_MODEL)
# Ollama defaults the context window to a few thousand tokens and TRUNCATES silently past it,
# which does not fail -- it answers confidently from a prompt whose end is missing.
#
# The largest call decides this, and it is the script review: the whole authoring prompt
# (prompts/40s-paper-cutout-script.md, ~6.7k tokens) plus the draft it is checking plus the
# corrected script it returns. 32k leaves room for an eight-beat script whose asset prompts
# are the 150-250 words the brief asks for. qwen3.6 itself tops out at 262144.
QWEN_NUM_CTX = int(os.environ.get("PAPERREEL_QWEN_NUM_CTX", "32768"))
# The model ships with temperature 1.0 and presence_penalty 1.5, which is tuned for open
# chat. Board edits want the opposite, and this is the default for everything except the
# creative pass, which asks for more (see PLAN_TEMPERATURE).
QWEN_TEMPERATURE = float(os.environ.get("PAPERREEL_QWEN_TEMPERATURE", "0.3"))
PLAN_TEMPERATURE = float(os.environ.get("PAPERREEL_PLAN_TEMPERATURE", "0.8"))
# Reasoning is on by default in a thinking model and it is not free: the same unambiguous
# board edit measured 0.9s with thinking off and 14s with it on. So it is off everywhere
# except writing the script, which is the one call whose quality is worth the wall clock.
PLAN_THINK = os.environ.get("PAPERREEL_PLAN_THINK", "1") == "1"
# ~23 GiB of weights, about four seconds to load. A session is dozens of short turns, so
# keeping the model resident is the difference between an edit landing at once and every
# single one of them paying the load again.
QWEN_KEEP_ALIVE = os.environ.get("PAPERREEL_QWEN_KEEP_ALIVE", "10m")
QWEN_TIMEOUT = float(os.environ.get("PAPERREEL_QWEN_TIMEOUT", "900"))
QWEN_PROBE_TIMEOUT = 2.0
# A tool loop has to be able to end. Every round is one model turn plus whatever the tools
# did, and a model that has started calling `read_board` in circles is not going to stop on
# its own. Eight is far more than any observed turn needs.
AGENT_MAX_ROUNDS = int(os.environ.get("PAPERREEL_AGENT_MAX_ROUNDS", "8"))

# ## Reviewing its own work
#
# Both passes exist because the model is local and unmetered. Neither would have been worth
# a quota slot; both are worth ten seconds.
#
# The script pass re-reads the draft against the rules of the medium -- one continuous
# locked-off shot per beat, one thing moving, the style bible quoted verbatim in every asset
# prompt -- and returns a corrected script plus what it changed. Those are the failures that
# are invisible on the page and obvious in the render.
PLAN_REVIEW = os.environ.get("PAPERREEL_PLAN_REVIEW", "1") == "1"
# The stills pass looks at each finished still next to the reel's locked cast reference and
# says whether the same characters, palette and paper stock came back. A still that missed
# gets its asset prompt rewritten and is rendered again.
#
# One retry, not five: mflux is free but not instant at ~10-18 s a frame, and a still the
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
# flux2-klein-4b through mflux, free, unmetered, ~10-18 s per still, and straight onto the H3
# grid so nothing is cropped on the way to the video model.
#
# It is the only generator. The old fallback was agy, whose five-per-five-hours window is
# gone along with it -- so on a machine where the image server is not up (or where mflux
# cannot run at all), stills are uploads. That is what `manual_stills` on a board is for.
PAPERCUT_URL = os.environ.get("PAPERREEL_PAPERCUT_URL", "http://127.0.0.1:8791")
# The aspect preset in image/shared/types.ts that matches GEN_WIDTH x GEN_HEIGHT exactly.
# Anything else gets cover-cropped by media.fit_frame at render time, which is how a still
# loses its framing between being approved and being rendered.
PAPERCUT_ASPECT = "9:16-reel"
# flux2-klein-4b is a distilled few-step model; 4 is what the studio next door defaults to
# and what its measured timings are quoted at.
PAPERCUT_STEPS = int(os.environ.get("PAPERREEL_PAPERCUT_STEPS", "4"))
# How many pictures a still may be drawn FROM. The first is the reel's locked cast reference,
# which is what a still has always been conditioned on; the rest are the director's own uploads
# on that beat -- the same pictures the video model is shown, so the puppet in the clip and the
# puppet in the frame it opens on are being held to one set of images rather than two.
#
# Four, which is a time cap rather than a model limit: mflux takes any number of `--image-paths`
# and encodes every one of them through every sampling step. Measured on this card at 768x1344
# and 4 steps, same prompt and seed: 18.6 s with one picture, 31.4 s with two -- so each extra
# picture costs about as much again as the first, and four is a still of roughly a minute. Still
# free, still unmetered; what more pictures buy in fidelity has not been compared.
#
# The image server reports its own cap in `limits.maxReferences` and the smaller of the two wins,
# exactly as the frame cap already works.
MAX_STILL_REFS = int(os.environ.get("PAPERREEL_MAX_STILL_REFS", "4"))

# What every still is asked for on top of the board's style bible and the beat's own
# asset_prompt. One place, because it is also what the vision review judges a still against:
# a still is rejected for missing the medium described here, so the words that ask for it and
# the words that check for it must not be able to drift apart.
ASSET_STYLE_SUFFIX = (
    "Vertical 9:16 portrait composition, handcrafted layered paper-cutout art, visible "
    "paper grain, soft contact shadows, no text, no watermarks, no signature."
)

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
    "proportions, paper texture, cut edges and palette. The references are not shots: do not "
    "show them, do not cut between them, do not pan across them, and do not put more than one "
    "version of a character on screen. A character shown in a reference is the SAME single "
    "character that performs the action below, not an additional one, and the pose it is in "
    "there is only how it looks -- not where this shot starts and not something that must "
    "also appear. "
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
    "placement and scale, its set dressing and its light are where this take starts -- and "
    "hold that framing, that scale and that lighting for the whole clip. Everything the "
    "action below describes happens from there, forward. "
)
# What a carried reference video is, and it has to be said in the same breath as "compose the
# opening frame yourself" -- otherwise the two instructions fight and the model either ignores
# the clip or treats it as footage to replay. This is the reference join's answer to
# OPEN_CONTINUATION: not a frame handoff, but the same take, carried on from where it ended.
CARRY_VIDEO = (
    "{tag} is the last few seconds of the shot immediately before this one, and this shot is "
    "that same take carrying on. Open on the moment {tag} ends -- same set, same camera, same "
    "lighting, the subject in the pose, position and scale it is in on that final moment -- "
    "and continue its movement onward at the same speed and in the same direction. Do not "
    "replay {tag}, do not cut to it, do not re-establish the scene, and do not let the "
    "subject settle to rest and start again. "
)
# What each picture is FOR, when the user has said. Without this the model has to guess from
# the picture alone, and it guesses "this is the scene" -- which is how a reference showing the
# cast in the finished set ends up rendered as-is AND acted out a second time by the same
# puppet. Only pictures with a note appear here; the rest are covered by the paragraph above.
REFERENCE_ROLES = "What each reference is for: {roles} "
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


def build_prompt(action: str, *, scene: str = "", mute: bool = False, identity: str = "",
                 continues: bool = False, lands: bool = False, refs: int = 0,
                 ref_notes: list[str] | None = None, ref_videos: int = 0,
                 opens_on: bool = False) -> str:
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
    """
    if refs > 0 or ref_videos > 0:
        parts = [MEDIUM]
        if refs > 0:
            parts.append(OPEN_REFERENCE.format(tags=reference_tags(refs)))
            # Straight after the paragraph that says what a reference IS, because these are
            # the exceptions to it: which picture is the cast, which is only the set, which
            # prop.
            roles = reference_roles(list(ref_notes or []))
            if roles:
                parts.append(REFERENCE_ROLES.format(roles=roles))
        # Exactly one of these three, and they are mutually exclusive because they are three
        # different answers to the one question the model has to have settled before it starts:
        # where does this shot open? A carried clip says "where the last one ended", an opening
        # still says "on this picture", and neither leaves anything for "compose it yourself".
        # Two of them present at once is two instructions fighting, which reads in the render
        # as a clip that starts, settles, and starts again.
        if ref_videos > 0:
            parts.append(CARRY_VIDEO.format(tag="<Video 1>"))
        elif opens_on and refs > 0:
            parts.append(OPEN_REFERENCE_STILL.format(tag="<Picture 1>"))
        elif refs > 0:
            parts.append(COMPOSE_OPENING)
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
