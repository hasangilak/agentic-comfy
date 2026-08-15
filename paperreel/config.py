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
# On a beat with only its opening still, two of those nine slots fill themselves --
# <Picture 1> is that still, <Picture 2> is the reel's locked cast reference -- which is why
# an upload budget exists rather than a flat nine. On a beat whose asset pass drew a
# stop-motion sequence, the poses themselves take those slots (they ARE the cast, in motion)
# and fill whatever is left after staging sheets and uploads, so a quiet cut uses all nine.
# `Board.pictures_for` is where the order is decided; the roles below are the words each
# auto-wired slot is described to the model with.
#
# The still plus the cast (or the still plus its in-betweens) are the reason this join is the
# default at all: one opening composition drifts towards its own reading of the style bible
# over ten seconds, and a transform with only that one picture drops the puppet mid-clip.
# The sequence is what a keyframe cut never had -- the action, held, through every sampling
# step. The previous clip as <Video 1> sits next to that, not instead of it.
REF_ROLE_OPENING = (
    "the composition this shot opens on: its set, its framing, its subject scale and its "
    "lighting are the ones this whole clip holds"
)
REF_ROLE_CAST = (
    "this reel's locked cast reference -- it fixes what the characters and the materials look "
    "like everywhere in the film, and it is NOT this shot's setting or framing"
)
# Poses 2..k of a stop-motion sequence. Pose 1 keeps REF_ROLE_OPENING, because that is still
# where the clip begins; these are the in-betweens the video model interpolates through so a
# ten-second transform cannot drop the puppet and invent a new one mid-clip.
REF_ROLE_POSE = (
    "stop-motion pose {i} of {k} of this shot: the same locked-off take, the same puppets "
    "and set, the subject here at this moment of the action -- not a different camera, not "
    "a different character"
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
# How many stop-motion poses asset generation draws per reference beat. Zero means fill
# whatever of the nine image sockets staging sheets and uploads have not already claimed,
# so a quiet beat gets nine poses and a beat that already binds three sheets gets six.
# Pin a number to explore; nine is the node's own cap (Papercut's too).
STILL_SEQUENCE = int(os.environ.get("PAPERREEL_STILL_SEQUENCE", "0"))
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
# instead (PLAN_THINK). 3.6-flash has vision, tool calling and a thinking level, which is
# what lets a single model drive the whole pipeline.
TEXT_MODEL = os.environ.get("PAPERREEL_TEXT_MODEL", "gemini-3.6-flash")
# Vision is a separate name only so the picture calls can be pointed at a different model --
# a bigger one when cast drift is being chased, a cheaper one when it is not.
VISION_MODEL = os.environ.get("PAPERREEL_VISION_MODEL", TEXT_MODEL)
# Board edits want a near-deterministic decode; this is the default for everything except the
# creative pass, which asks for more (see PLAN_TEMPERATURE).
LLM_TEMPERATURE = float(os.environ.get("PAPERREEL_LLM_TEMPERATURE", "0.3"))
PLAN_TEMPERATURE = float(os.environ.get("PAPERREEL_PLAN_TEMPERATURE", "0.8"))
# Reasoning costs output tokens and wall clock, and an unambiguous board edit needs none of
# it. So `gemini.chat` sends thinkingLevel `minimal` everywhere except writing the script,
# which is the one call whose quality is worth both.
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
# frames, not beats: a reference cut draws up to nine stop-motion poses, so a per-beat cap of
# 24 would run out on the third scene. AGENT_MAX_ROUNDS bounds turns, not money, and
# `generate_stills` is the one tool in the crew's toolbox that spends any. Seventy-two is a
# guess sized to an eight-beat reel drawn once -- not a measurement, and the first real run
# is what should replace this number.
CREW_STILL_BUDGET = int(os.environ.get("PAPERREEL_CREW_STILL_BUDGET", "72"))

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
# How many pictures a still may be drawn FROM. The first is the reel's locked cast reference,
# which is what a still has always been conditioned on; the rest are the director's own uploads
# on that beat -- the same pictures the video model is shown, so the puppet in the clip and the
# puppet in the frame it opens on are being held to one set of images rather than two.
#
# Four is a conservative request-size and consistency cap across the supported Gemini models.
#
# The image server reports its own cap in `limits.maxReferences` and the smaller of the two wins,
# exactly as the frame cap already works.
MAX_STILL_REFS = int(os.environ.get("PAPERREEL_MAX_STILL_REFS", "4"))

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
# The two entries are not symmetrical descriptions of one thing. Paper is rigid, hinged and
# flat, and its whole grammar is that shapes are SWAPPED rather than deformed. Clay is the
# opposite on exactly that axis: squash and stretch is what the medium is FOR, and writing a
# clay film under paper's physics produces stiff clay, which reads as a bad 3D render. So the
# claymation entry is written from the material outward rather than by find-and-replacing
# "paper" -- and it is reasoned, not measured. Nothing has been rendered in it yet.
@dataclass(frozen=True)
class Medium:
    """One medium's words, in the fourteen places a render or a review asks for them."""

    key: str
    # What the prompts call it, e.g. "paper-cutout stop-motion". Spliced into six system
    # prompts, which is why it is a phrase rather than a sentence.
    name: str
    # The first clause of every video prompt, before anything about the join.
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


PAPER_CUTOUT = Medium(
    key="paper-cutout",
    name="paper-cutout stop-motion",
    shot=("Single continuous locked-off shot in handcrafted layered paper-cutout stop-motion "
          "style, shot straight-on. "),
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
         "in it, nothing cropped at the edges, no text, no watermarks, no signature."),
    judge=("layered paper-cutout with visible paper grain, crisp cut edges, soft contact "
           "shadows. Not a photograph, not a 3D render, not clay, not felt"),
    essence="layered paper cutout, visible paper grain, soft contact shadows",
    opening=("**handcrafted layered paper-cutout stop motion** — real paper on a real "
             "tabletop, lit by a real lamp, shot on a locked-off camera"),
    negate="Not paper cutout, no paper-cutout layers, no paper grain, no collage",
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


# Written from the material outward, not by substituting words into the entry above. The one
# axis where the two media are opposites is deformation: paper's grammar is that a shape is
# swapped for another shape, and clay's grammar is that a shape BECOMES another shape. A clay
# film written under paper's rules comes out stiff, which is the failure mode that reads as a
# cheap 3D render -- the exact thing both media are trying not to look like.
CLAYMATION = Medium(
    key="claymation",
    name="clay stop-motion",
    shot=("Single continuous locked-off shot in handcrafted plasticine clay stop-motion "
          "style, shot straight-on. "),
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
         "watermarks, no signature."),
    judge=("sculpted plasticine clay with a matte surface, visible thumbprints and tool "
           "marks, soft rounded forms. Not a photograph, not a 3D render, not paper, not felt"),
    essence="sculpted plasticine clay, visible thumbprints and tool marks, a matte surface",
    opening=("**handcrafted plasticine clay stop motion** — real clay on a real tabletop, "
             "lit by a real lamp, shot on a locked-off camera"),
    negate="Not clay, no plasticine, no sculpted forms, no photographic texture",
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

MEDIUMS = {entry.key: entry for entry in (PAPER_CUTOUT, CLAYMATION)}
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
# framing, the angle and, with arrows on the panel, how the subject and the camera move. It is
# drawn cheap and read fast, and it exists so the sequence is judged before money goes out.
#
# Everything else in this file describes a picture that reaches a renderer. A panel does not: it
# conditions nothing, is handed to H3 never, and is in no fingerprint. It is a planning artifact,
# which is what makes the cheapest model the right one rather than a compromise.
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
    "No first frame is provided. Instead {tags} are supplied as design references: they fix "
    "what the characters, the set and the materials look like, and nothing else. Reproduce "
    "every subject that appears in them exactly -- same shapes, markings, colours, "
    "proportions, {surface} and palette. The references are not shots: do not "
    "show them, do not cut between them, do not pan across them, do not morph the camera "
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


def sequence_length(reserved: int) -> int:
    """How many stop-motion poses a beat should draw, given slots already spoken for.

    `reserved` is staging sheets plus director uploads -- the pictures that are not the
    sequence. Zero `STILL_SEQUENCE` fills whatever of the nine is left, so the video model
    is handed a full set rather than one still and eight empty sockets.
    """
    room = max(1, MAX_REF_IMAGES - max(0, reserved))
    wanted = STILL_SEQUENCE or MAX_REF_IMAGES
    return max(1, min(wanted, room, MAX_REF_IMAGES))


def pose_phase(index: int, total: int, action: str) -> str:
    """Where in the beat's action this pose sits, in words the still model can draw.

    Papercut's own `beatHint` is a left-to-right walk, which is the wrong action for most
    shots. This one names the beat's actual action and only the phase changes, so pose 4 of
    7 of "she raises the lantern" is the lantern partway up, not a step to the right.
    """
    said = " ".join(str(action or "").split()) or "the action"
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
# model gets `pictures_for` (own still, cast, uploads) tagged `<Picture N>`, the still model
# gets `still_pictures` (identity sheets or the cast still, then uploads on a reference join,
# capped at four) with no tags at all -- so one literal
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


def build_prompt(action: str, *, scene: str = "", mute: bool = False, identity: str = "",
                 continues: bool = False, lands: bool = False, refs: int = 0,
                 ref_notes: list[str] | None = None, ref_videos: int = 0,
                 opens_on: bool = False, staging: str = "", blocking: str = "",
                 medium_key: str | None = None,
                 mentions: dict[str, tuple[int | None, str]] | None = None,
                 poses: int = 0, hold_video: bool = False) -> str:
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

    `mentions` resolves the @-tokens a director may have typed into any of the three texts.
    One keyword here rather than three expanded call sites, so every path into a render --
    studio, CLI, and whatever comes next -- gets it by construction rather than by remembering.
    None means no expansion, which is what `reel.py` (no board, so nothing to resolve against)
    passes and why its prompts are byte-identical to what they always were.
    """
    look = medium(medium_key)
    action = expand_mentions(action, mentions)
    scene = expand_mentions(scene, mentions)
    ref_notes = [expand_mentions(note, mentions) for note in (ref_notes or [])] or None
    poses = max(0, int(poses or 0))
    if refs > 0 or ref_videos > 0:
        parts = [look.shot]
        if refs > 0:
            if poses > 1:
                # The sequence is this shot, in order. Remaining pictures (sheets, uploads)
                # still get the design-reference paragraph, which is what they always were.
                parts.append(OPEN_REFERENCE_SEQUENCE.format(
                    tags=reference_tags(min(poses, refs)), first="<Picture 1>"))
                rest = refs - min(poses, refs)
                if rest > 0:
                    parts.append(OPEN_REFERENCE.format(
                        tags=reference_tags(rest, start=poses + 1), surface=look.surface))
            else:
                parts.append(OPEN_REFERENCE.format(tags=reference_tags(refs),
                                                   surface=look.surface))
            # Straight after the paragraph that says what a reference IS, because these are
            # the exceptions to it: which picture is the cast, which is only the set, which
            # prop, which pose.
            roles = reference_roles(list(ref_notes or []))
            if roles:
                parts.append(REFERENCE_ROLES.format(roles=roles))
        # A carried clip and an opening still used to be mutually exclusive -- two answers
        # to where the shot opens. HOLD_VIDEO is a third job for the same socket: identity
        # from the previous take, while the still (or the pose sequence) still says where
        # THIS shot begins. CARRY_VIDEO remains exclusive with COMPOSE_OPENING; it is not
        # exclusive with a sequence whose first pose is that continuation.
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
    else:
        parts = [look.shot, OPEN_CONTINUATION if continues else OPEN_CUT]
        if lands:
            parts.append(ARRIVE_ON_LAST)
    identity = " ".join(identity.split())
    if identity:
        parts.append(IDENTITY_PREFIX + identity.rstrip(".") + ". ")
    # Straight after the style bible, because it is the same claim about more specific things:
    # the bible says what the production looks like, these say what two named things in it look
    # like. Before the scene line, so "Scene: the clearing at dusk" is read against a clearing
    # that has already been described rather than one the model has just invented.
    staging = " ".join(expand_mentions(staging, mentions).split()).strip().rstrip(".")
    if staging:
        parts.append(STAGING_PREFIX + staging + ". ")
    # Between the designs and the scene line, because it is the answer to a question those two
    # leave open. The bible says what things look like and the scene line says where the shot is
    # and at what scale; neither says where in THIS frame anything stands, and left unsaid the
    # model re-blocks the set every beat. Before the scene line rather than after, so a reader
    # has the set in mind before being told what is standing in it.
    blocking = " ".join(expand_mentions(blocking, mentions).split()).strip().rstrip(".")
    if blocking:
        parts.append(BLOCKING_PREFIX + blocking + ". ")
    scene = " ".join(scene.split()).strip().rstrip(".")
    if scene:
        parts.append(SCENE_PREFIX + scene + ". ")
    action = action.strip().rstrip(".")
    if action:
        parts.append(action + ".")
    parts.append(look.craft)
    if not mute:
        parts.append(look.audio)
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
