"""Storyboard panels: the reel read as pictures before anything is paid for.

A storyboard in the film sense is a sheet of rough panels -- one drawing per shot, showing the
framing, the angle and, with arrows on the panel, how the subject moves. It is drawn
cheap and read fast, and it exists so the sequence is judged *before* money goes out.

    panels.write(board)                 # one text turn writes N sketches per beat
    panels.draw_all(board, [1, 2, 3])   # the cheapest Nano Banana draws them, then the sheet
    panels.sheet(board)                 # 3 across, numbered, in reels/<slug>/storyboard_sheet.png

**A panel conditions the still, never the video.** It is a composition reference for Gemini
(`Board.still_pictures`) and is handed to H3 never (`Board.pictures_for`). It is in no
fingerprint (see `Board.own_fingerprint`, where its absence is spelled out): the still file
is what the clip hashes, and putting the sketch in there would re-price a paid render over a
drawing the video model never saw. That is what makes the cheapest model the right one here
rather than a compromise, and it is the difference between this module and every other one
that puts a picture on disk:

  * `stills.py` renders the image a clip *opens on*, conditioned on these sketches for framing,
    and then reviews it against the reel's locked identity sheets, rejecting it for drift.
  * `pictures.py` and `staging.py` render design sheets that later stills are held *to*, and have
    no review pass because a design is supposed to differ from the cast.
  * this module renders sketches nobody is ever held to. So there is **no review pass and no
    conversation** -- not for `stills.py`'s reason and not for `staging.py`'s, but because there is
    nothing for a verdict to be about. A panel that is wrong is redrawn, or its shot grammar is
    edited by hand.

One sketch per beat by default (the opening composition). Extra midpoint/landing frames
existed to lock a nine-pose fill H3 no longer needs; `PANEL_SEQUENCE` still caps what is
written. Pencil still never goes to H3.

Two things about how a panel is drawn are `pictures.py`'s measured lessons, unchanged one level
out, and both live in `config.py` next to the constants that act on them: **nothing conditions a
panel** (a model shown the cast reference draws the cast, in the cast's medium -- so a sketch panel
handed the cutout still comes back a cutout), and **the board's style bible never reaches this
render** (`papercut.draw` puts `config.panel_style(...)` in the scene `style` slot instead). The
subject travels as words, which `write` puts into the panel text. Multiple frames of one beat
are chained to each other (`consistency="chain"`), still unconditioned on the film.

The consequence is worth stating rather than discovering: character consistency across panels is
nil. Two panels of the same fox are two readings of the same sentence. That is what a storyboard
is for -- the framing is the content -- and it is why the panel is a composition lock for the
still, never a picture H3 interpolates through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import board as board_mod
from . import config, gemini, papercut

# One entry per beat. `frames` last within the object because the decode follows schema-property
# order and the field written first becomes the model's scratchpad -- the lesson
# `planner.REVIEW_SCHEMA` records at greater length. `n` first means the beat number is committed
# before the sentences about it, which is also the order it is easiest to check.
_FRAME = {
    "type": "object",
    "required": ["phase", "panel"],
    "properties": {
        "phase": {
            "type": "string",
            "description": (
                "Where in this beat's action this sketch sits"
                + (": opening, midpoint, or landing. "
                   if config.PANEL_SEQUENCE > 1 else " (the opening). ")
                + f"A beat has exactly {config.PANEL_SEQUENCE} frames."
            ),
        },
        "panel": {
            "type": "string",
            "description": (
                "One or two sentences describing this sketch: the shot size, the camera angle, "
                "where the subject sits in the frame and which way it faces, what the arrows "
                "on the panel point at"
                + (", and -- for midpoint and landing -- how far the action "
                   "has moved since the previous frame. Same setup as the opening. "
                   if config.PANEL_SEQUENCE > 1 else ". ")
                + "Written to be DRAWN, not read as notes."
            ),
        },
    },
}
PANEL_SCHEMA = {
    "type": "object",
    "required": ["panels"],
    "properties": {
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "camera", "frames"],
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "The beat number these panels are of, exactly as given.",
                    },
                    "camera": {
                        "type": "string",
                        "enum": list(config.CAMERA_ANGLES),
                        "description": (
                            "The locked-off camera angle for this beat. eye = eye level / "
                            "straight-on; low = camera below looking up; high = camera above "
                            "looking down; overhead = looking straight down; dutch = horizon "
                            "off-level. A chain/bridge beat MUST copy the angle of the shot it "
                            "continues. If ANGLE IS ALREADY CHOSEN is on the beat line, return "
                            "that key unchanged."
                        ),
                    },
                    "frames": {
                        "type": "array",
                        "minItems": config.PANEL_SEQUENCE,
                        "maxItems": config.PANEL_SEQUENCE,
                        "items": _FRAME,
                        "description": (
                            f"Exactly {config.PANEL_SEQUENCE} sketches of this beat in order: "
                            f"{config.panel_frame_copy()}. Same shot "
                            "size, same angle, same camera."
                            + (" Only the pose advances." if config.PANEL_SEQUENCE > 1 else "")
                        ),
                    },
                },
            },
        },
    },
}

# The vocabulary, spelled out because the board has never held any of it. `scene`, `action` and
# `asset_prompt` describe staging and medium and never once say how far away the camera is -- so
# left to invent the words, the model wrote another paragraph of staging and the panels came back
# as nine variations of the same wide shot.
SHOT_GRAMMAR = (
    "SHOT SIZE, pick one and name it: extreme close-up, close-up, medium close-up, medium shot, "
    "wide shot, extreme wide shot.\n"
    "ANGLE, pick one and name it: eye level, low angle (camera below the subject, it looms), high "
    "angle (camera above, the subject is small), overhead, or a dutch tilt when the moment is "
    "meant to feel wrong.\n"
    "CAMERA: static, locked-off. Do not write a pan, push, pull, tilt, or orbit -- those moves "
    "never reach the clip, but they do condition the still, and a tighter still is a false "
    "preview of a locked take.\n"
    "WHERE THE SUBJECT IS: which third of the frame, facing which way, how much headroom. "
    "If the shot holds more than one of a bound design, say how many ('five cranes in the "
    "upper-right third', not 'a bird'). A close-up of one member of a group already in the "
    "film must still name the rest of the group, or that this is one of them.\n"
    "ARROWS: a storyboard panel carries arrows drawn on top of it for the SUBJECT's path "
    "through the frame. Not the camera's -- the rig does not move."
)

SYSTEM = (
    "You are the storyboard artist for a handcrafted stop-motion Instagram Reel studio. You are "
    "given a finished script and you write the panel descriptions for each beat: what the rough "
    "sketches of that shot show.\n\n"
    "A panel is not the shot and it is not a prompt for the shot. It is a cheap grey-pencil "
    "drawing whose whole job is to let a director check the sequence before anything is rendered: "
    "framing, angle, where the subject sits, which way things move. Texture, colour, material and "
    "lighting are somebody else's problem and belong in no panel description you write.\n\n"
    f"EACH BEAT GETS EXACTLY {config.PANEL_SEQUENCE} SKETCH"
    f"{'ES' if config.PANEL_SEQUENCE != 1 else ''}: {config.panel_frame_copy()}. "
    "They are the same locked-off setup -- same shot size, same angle, same camera"
    + (" -- with the subject further through the movement.\n\n"
       if config.PANEL_SEQUENCE > 1 else ".\n\n")
    + "What you add is the thing the script does not have. Every beat already says what happens and "
    "what it is made of; none of them say how far away the camera is or what it is looking up or "
    "down at. That is your entire contribution, so name it explicitly in every panel:\n\n"
    f"{SHOT_GRAMMAR}\n\n"
    "THE FILM IS VERTICAL 9:16. A wide shot is tall, not letterboxed, and there is room above a "
    "subject's head rather than beside it.\n\n"
    "A BEAT MARKED `chain` IS THE SAME SHOT CONTINUING. Its panels are the same setup at a later "
    "moment -- same shot size, same angle, same camera -- with the subject further through the "
    "movement. Do not invent a new setup for it, and do not re-establish the scene. A beat marked "
    "`reference`, `asset` or `bridge` begins a new shot and is where a new setup belongs. A long "
    "take of successive `reference` beats is still the same setup: copy the shot size and angle "
    "from the beat before, only the pose advances.\n\n"
    "Vary the shot sizes across the shots that ARE new. A reel of five identical wide shots is the "
    "failure this pass exists to catch, and you are the one who can see all of them at once.\n\n"
    "Name how many of each bound design are in the sketch. 'A single bird' on a flock roster is "
    "the same fail as a new protagonist -- a close-up of one member of the group already in the "
    "film must still say the rest of the group is there, or that this is one of them.\n\n"
    "Write plainly, present tense, no markdown, no headings, no numbered lists inside a panel. One "
    "or two sentences each. Never mention the film's materials, its texture, its colour or its "
    "lighting -- whatever this reel is made of, a panel is graphite on paper."
)


class PanelsError(RuntimeError):
    """A panel job that must not run. `status` is the HTTP code the API answers with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _digest(board: board_mod.Board, beats: list[int]) -> str:
    """The beats a panel pass is being asked about, one line each.

    The join is on the line and is load-bearing: it is the only thing that says whether a beat is a
    new setup or the same shot continuing, and the model gets that wrong in both directions when it
    has to infer it from the prose. Same lesson as `board_digest` spelling out its "waiting on"
    lists rather than leaving them to be read off the join names.

    The bound designs are on it for the same reason, and only their NAMES. A binding is the one
    piece of per-beat structured knowledge the board holds about who is in this shot, and prose
    often does not carry it -- "she turns" does not say the wolf is in frame, and a panel that
    names the wrong subject is the failure `_messages` opens by trying to prevent. Names, not
    `role`, not `note`, not the sheet: `SYSTEM` ends "never mention the film's materials, colour
    or lighting", and a name is neither a material nor a palette, so the ban holds.

    **This changes what a re-run produces and marks nothing stale**: a panel is excluded from
    `own_fingerprint` unconditionally and permanently (see the comment where that digest ends).
    That is what makes this the safest prompt change in the repo, and it is exactly why it must
    not be copied into `config.build_prompt` or `stills.py`, where the same edit WOULD be a
    fingerprint change and would re-price a paid render.
    """
    lines = []
    for beat in board.ordered_beats():
        n = beat["n"]
        if n not in beats:
            continue
        source = board.source_for(beat)
        names = [board.stage_name(entry) for entry in board.bound_staging(n)]
        chosen = str(beat.get("camera") or "").strip()
        lines.append(
            f'beat {n} -- {board.seconds_for(beat):g}s, join: {source}'
            + (" (the same shot as the beat before it, continuing)"
               if board_mod.chains(source) else " (a new shot)")
            + (f'\n  in shot: {", ".join(names)}'
               + " — say how many of each are in the sketch"
               if names else "")
            + (f'\n  ANGLE IS ALREADY CHOSEN: {config.camera_label(chosen)} '
               f'({config.snap_camera(chosen)}) -- use this key, do not pick another'
               if chosen else "")
            + f'\n  scene: {beat.get("scene") or "(nothing written)"}'
            + f'\n  action: {beat.get("action") or "(nothing written)"}'
        )
    return "\n".join(lines)


def _messages(board: board_mod.Board, beats: list[int]) -> list[dict]:
    """The prompt for one pass: who is in the film, then the beats, then what to answer with.

    The script goes LAST before the instruction, for the reason `agent.turn` orders history before
    the board: whatever sits nearest the question is what gets answered.
    """
    parts = [
        "WHAT THIS FILM IS MADE OF AND WHO IS IN IT. This is here so your panels name the right "
        "subject. Nothing about the materials or the palette belongs in a panel description: "
        f"{board.identity() or '(no style bible written yet)'}",
        f"THE SCRIPT, {len(beats)} beat{'' if len(beats) == 1 else 's'} of it:\n\n"
        + _digest(board, beats),
        "Answer with one entry per beat in `panels`, using the beat numbers exactly as they are "
        f"above. Each entry's `frames` is exactly {config.PANEL_SEQUENCE} sketches of that beat "
        f"({config.panel_frame_copy()}). Each entry's `camera` is the locked-off angle -- copy "
        "ANGLE IS ALREADY CHOSEN when it is on the beat line, and copy the previous beat's "
        "camera on a chain or bridge.",
    ]
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def write(board: board_mod.Board, beats: list[int] | None = None, *,
          log: Callable[[str], None] = print,
          announce: Callable[[], None] | None = None) -> list[int]:
    """Write the shot grammar for these beats (all of them by default). Returns the ones written.

    One call for the whole reel rather than one per beat, and that is the point rather than an
    economy: the shot sizes have to vary ACROSS the film, and a model shown one beat at a time
    cannot see that it has just written five wide shots in a row. It is also what lets a chained
    beat be told to reuse its shot's setup.

    `think` is left off (the default). This is a translation of prose that already exists into
    camera language, not a planning decision, and reasoning here would be paid for in thought
    tokens on every beat -- see `gemini._thinking`.
    """
    wanted = [b["n"] for b in board.ordered_beats()
              if beats is None or b["n"] in set(beats)]
    if not wanted:
        raise PanelsError("no beats to write panels for", status=422)

    verdict = gemini.structured(
        _messages(board, wanted), PANEL_SCHEMA,
        # Warmer than a review's 0.1 for the reason `pictures.converse` gives: this is writing
        # rather than checking, and a near-deterministic decode answers a second pass over the
        # same script with the same five shot sizes.
        temperature=0.4,
    )
    written: list[int] = []
    picked: dict[int, str] = {}
    for entry in verdict.get("panels") or []:
        try:
            n = int(entry.get("n"))
        except (TypeError, ValueError):
            continue
        if n not in wanted:
            # A beat number the model invented. Dropped rather than repaired: writing it onto the
            # nearest real beat would put one shot's framing on another shot.
            log(f"[panel] ignoring a panel for beat {n}, which is not in this pass")
            continue
        texts = _frame_texts(entry)
        if not texts:
            continue
        beat = board.beat(n)
        beat["panel"] = texts[0]
        if len(texts) > 1:
            beat["panel_frames"] = texts[1:]
        else:
            beat.pop("panel_frames", None)
        written.append(n)
        picked[n] = str(entry.get("camera") or "").strip()
        log(f"[panel] beat {n}: {len(texts)} frame{'s' if len(texts) != 1 else ''} — {texts[0]}")
    if not written:
        raise PanelsError(f"{config.TEXT_MODEL} wrote no usable panels")
    missed = [n for n in wanted if n not in written]
    if missed:
        # Not fatal: the panels that landed are worth keeping, and the board shows which beats are
        # still blank. Said out loud because a silently short pass reads as a model that refused.
        log(f"[panel] no panel written for beat{'s' if len(missed) > 1 else ''} "
            f"{', '.join(map(str, missed))} -- ask again, or write them by hand")
    # Fill empty cameras in beat order so a cut's pick lands on the take before a chain beat
    # in the same pass is considered. A director pick (a stored key) is never overwritten.
    for n in sorted(picked):
        if str(board.beat(n).get("camera") or "").strip():
            continue
        if picked[n]:
            board.set_camera(n, picked[n])
            log(f"[panel] beat {n}: camera {config.snap_camera(picked[n])}")
    board.save()
    if announce is not None:
        announce()
    return written


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _frame_texts(entry: dict) -> list[str]:
    """The sketch lines from one model entry, oldest boards included.

    Prefers `frames[].panel`. A model that still writes a single `panel` string is accepted as
    a one-frame board so a schema change does not drop a whole pass.
    """
    found = []
    for frame in entry.get("frames") or []:
        if isinstance(frame, dict):
            text = _clean_text(frame.get("panel"))
        else:
            text = _clean_text(frame)
        if text:
            found.append(text)
    if not found:
        text = _clean_text(entry.get("panel"))
        if text:
            found.append(text)
    return found[:config.PANEL_SEQUENCE]


def panel_texts(board: board_mod.Board, n: int) -> list[str]:
    """What will be drawn for this beat: `panel` then `panel_frames`, capped."""
    beat = board.beat(n)
    first = _clean_text(beat.get("panel"))
    extra = beat.get("panel_frames") or []
    texts = [first] if first else []
    for item in extra:
        text = _clean_text(item.get("panel") if isinstance(item, dict) else item)
        if text:
            texts.append(text)
    return texts[:config.PANEL_SEQUENCE]


def _panels_complete(board: board_mod.Board, n: int) -> bool:
    """True when every written frame for this beat has a PNG."""
    texts = panel_texts(board, n)
    if not texts:
        return True
    return all(board.panel_path(n, index).is_file() for index in range(1, len(texts) + 1))


def _clear_extra_panels(board: board_mod.Board, n: int, keep: int) -> None:
    """Drop panel files past `keep`, so a shorter write cannot leave a gap-free longer one."""
    for index in range(max(0, keep) + 1, config.MAX_REF_IMAGES + 1):
        board.panel_path(n, index).unlink(missing_ok=True)


def drawable(board: board_mod.Board, n: int) -> str:
    """The shot grammar beat `n`'s opening panel is drawn from.

    Deliberately short of `pictures.drawable`'s guards, and each omission is a decision. There is
    no `manual_stills` check: that flag says the director supplies the images a video render uses,
    and a panel is not one of them. There is no join check either -- `pictures.drawable` refuses
    on a chained beat because a picture there conditions the clip never, and a panel conditions
    the still on every join, so no join can make drawing one pointless.
    """
    texts = panel_texts(board, n) if any(b["n"] == n for b in board.beats) else []
    if not any(b["n"] == n for b in board.beats):
        raise PanelsError(f"no such beat: {n}", status=404)
    if not texts:
        raise PanelsError(
            f"scene {n} has no panel written yet. Write the storyboard first, or type the shot "
            "into the panel field by hand.",
            status=422,
        )
    return texts[0]


def draw(board: board_mod.Board, n: int, *,
         log: Callable[[str], None] = print,
         progress: Callable[[int, float], None] | None = None,
         announce: Callable[[], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> Path:
    """Draw beat `n`'s panels. Returns the opening panel's path.

    One frame still goes through `papercut.draw` (unconditioned `none`). Two or more are one
    Papercut scene with `consistency="chain"` so the three sketches of this shot read as one
    take -- still `pictures=[]`, because a model shown the cast draws the cast in the film's
    medium. See the module docstring.

    The model and the size are this module's rather than the beat's, and that is on purpose:
    `papercut.draw` lets an explicit argument win over `beat["gemini_model"]`, so a board whose
    stills are set to Pro at 2K still gets its panels at Lite 1K. A storyboard drawn on the
    expensive model is not a storyboard.
    """
    texts = panel_texts(board, n)
    if not texts:
        drawable(board, n)
        texts = panel_texts(board, n)
    out_paths = [board.panel_path(n, index) for index in range(1, len(texts) + 1)]
    style = config.panel_style(board.medium())
    seed = _draw_seed(board, n, "\n".join(texts))
    common = dict(
        pictures=[],
        editing=False,
        style=style,
        aspect=config.PANEL_ASPECT,
        negative="",
        gemini_model=config.PANEL_MODEL,
        gemini_image_size=config.PANEL_IMAGE_SIZE,
        log=log,
        progress=progress,
        cancelled=cancelled,
        seed=seed,
    )
    if len(texts) == 1:
        made = papercut.draw(
            board, n, text=texts[0], out_path=out_paths[0],
            label=f"panel {n}", **common,
        )
        landed = bool(made)
    else:
        pose_texts = [
            f"{text} Storyboard panel {index} of {len(texts)} "
            f"({config.panel_phase(index, len(texts))}): same locked-off setup as the opening, "
            "the subject further through the action. Graphite sketch, not the film."
            for index, text in enumerate(texts, start=1)
        ]
        landed = papercut.draw_frames(
            board, n, texts=pose_texts, out_paths=out_paths,
            label=f"panel {n}", **common,
        )
    if not landed and not out_paths[0].is_file():
        raise PanelsError(f"the panel for scene {n} did not render")
    _clear_extra_panels(board, n, sum(1 for path in out_paths if path.is_file()))
    if announce is not None:
        announce()
    return out_paths[0]


def _draw_seed(board: board_mod.Board, n: int, text: str) -> int:
    """A seed this panel has not been drawn on.

    The panel text is in the digest rather than a turn count, because a panel has no conversation
    to count turns of -- and hashing the text means an edited panel is redrawn differently while a
    plain retry of the same words still moves, since `fingerprint` also takes the board seed.
    """
    digest = board_mod.fingerprint(board.data.get("seed") or 0, n, text)
    return int(digest[:8], 16)


def draw_all(board: board_mod.Board, beats: list[int] | None = None, *,
             log: Callable[[str], None] = print,
             progress: Callable[[int, float], None] | None = None,
             announce: Callable[[], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> list[int]:
    """Draw these panels (every beat that has text and is missing a PNG by default), then the sheet.

    One Papercut scene per beat (a chain of three graphite frames) rather than one scene of
    the whole reel, and batching would buy nothing: `image/server/store.ts` chains every render
    through a single global lock, so the frames are serial either way. What the loop adds is a
    panel appearing on the canvas as soon as it lands, and a cancel that takes effect between
    beats instead of at the end.

    A panel that fails does not fail the pass. The rest are still worth having and the board shows
    which beats are blank -- the same judgement `papercut._render_scene` makes about one failed
    frame in a run of stills.
    """
    wanted = beats if beats is not None else [
        b["n"] for b in board.ordered_beats()
        if panel_texts(board, b["n"]) and not _panels_complete(board, b["n"])
    ]
    if not wanted:
        raise PanelsError(
            "every beat with a panel written already has one drawn. Write the panels first, or "
            "pick the scenes to redraw.",
            status=422,
        )

    made: list[int] = []
    for n in wanted:
        if cancelled is not None and cancelled():
            log("[panel] cancelled")
            break
        try:
            draw(board, n, log=log, progress=progress, announce=announce, cancelled=cancelled)
        except (PanelsError, papercut.PapercutError) as failed:
            log(f"[panel] beat {n}: {failed}")
            continue
        made.append(n)
    if made:
        sheet(board, log=log)
        if announce is not None:
            announce()
    return made


# ## The contact sheet
#
# PIL rather than ffmpeg's `tile` filter, and the caption is why: `tile` can lay panels out but
# cannot letter each cell without a font path and a `drawtext` escape dance, and a panel with no
# beat number under it is not a storyboard sheet, it is a pile of sketches. PIL is already a
# dependency -- `papercut._download` re-encodes every frame through it.
SHEET_COLUMNS = 3
SHEET_PANEL_WIDTH = 420           # a 9:16 panel at this width is 420x735, three across is legible
SHEET_GAP = 24
SHEET_CAPTION = 54                # two lines of the caption font, plus breathing room
SHEET_GROUND = (247, 245, 240)    # off-white, the paper the panels are drawn on
SHEET_INK = (32, 32, 32)
SHEET_EDGE = (176, 172, 164)


def _font(size: int):
    """A legible caption font, or PIL's bitmap default.

    Tried in order rather than hardcoded: the default font is a fixed tiny bitmap that does not
    scale, so a sheet built from it has captions nobody can read at panel width. Nothing here
    fails if none of the paths exist -- a sheet with small captions still shows the sequence.
    """
    from PIL import ImageFont

    for candidate in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                      "/System/Library/Fonts/Helvetica.ttc",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _caption(board: board_mod.Board, n: int, index: int = 1, total: int = 1) -> str:
    """The line under one panel: which beat, which frame of the action, how long, which join."""
    beat = board.beat(n)
    phase = config.panel_phase(index, total) if total > 1 else ""
    return (
        f"{n}"
        + (f" · {phase}" if phase else "")
        + f" · {board.seconds_for(beat):g}s · {board.source_for(beat)}"
    )


def sheet(board: board_mod.Board, *, log: Callable[[str], None] = print) -> Path | None:
    """Stitch every drawn panel into one numbered sheet. Returns the path, or None if there are none.

    Beats with no panel on disk are skipped rather than given a blank cell: a hole in the grid
    reads as a panel that failed, and the board already says which beats are blank. The sheet is
    rebuilt from scratch every time -- it is derived state that happens to be a file, so there is
    nothing to merge.
    """
    from PIL import Image, ImageDraw

    drawn: list[tuple[int, int, int, Path]] = []
    for beat in board.ordered_beats():
        paths = board.panel_paths(beat["n"])
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            drawn.append((beat["n"], index, total, path))
    if not drawn:
        board.sheet_path().unlink(missing_ok=True)
        return None

    # The cell is as tall as the panels actually are, read off the first one rather than assumed
    # from PANEL_ASPECT: the preset is a name the image server resolves, and a panel that was
    # drawn before the preset changed would then sit in a cell the wrong shape. Falls back to the
    # reel's own aspect when the file cannot be read at all.
    aspect = board_mod.image_aspect(drawn[0][3]) or (config.GEN_WIDTH / config.GEN_HEIGHT)
    cell_h = round(SHEET_PANEL_WIDTH / aspect)
    columns = min(SHEET_COLUMNS, len(drawn))
    rows = -(-len(drawn) // columns)  # ceiling division
    width = SHEET_GAP + columns * (SHEET_PANEL_WIDTH + SHEET_GAP)
    height = SHEET_GAP + rows * (cell_h + SHEET_CAPTION + SHEET_GAP)

    canvas = Image.new("RGB", (width, height), SHEET_GROUND)
    pen = ImageDraw.Draw(canvas)
    font = _font(22)

    for index, (n, frame, total, path) in enumerate(drawn):
        column, row = index % columns, index // columns
        x = SHEET_GAP + column * (SHEET_PANEL_WIDTH + SHEET_GAP)
        y = SHEET_GAP + row * (cell_h + SHEET_CAPTION + SHEET_GAP)
        with Image.open(path) as raw:
            # Fitted rather than cropped: a panel drawn at some other aspect is a panel to look at,
            # not a frame to hand a model, so losing its edges to a crop would be the one
            # destructive thing this function could do.
            panel = raw.convert("RGB")
            panel.thumbnail((SHEET_PANEL_WIDTH, cell_h))
            canvas.paste(panel, (x + (SHEET_PANEL_WIDTH - panel.width) // 2,
                                 y + (cell_h - panel.height) // 2))
        pen.rectangle([x, y, x + SHEET_PANEL_WIDTH, y + cell_h], outline=SHEET_EDGE, width=1)
        pen.text((x, y + cell_h + 12), _caption(board, n, frame, total), fill=SHEET_INK, font=font)

    out_path = board.sheet_path()
    canvas.save(out_path)
    log(f"[panel] sheet: {len(drawn)} panel{'' if len(drawn) == 1 else 's'} -> {out_path.name}")
    return out_path


def remove(board: board_mod.Board, n: int) -> None:
    """Delete one panel and rebuild the sheet without it.

    No guard beyond the beat existing. There is no renumbering race of the kind
    `DELETE /beats/{n}/refs/{index}` protects against -- `panel_path` is keyed by beat number, not
    by a position that compacts -- and `jobs.Runner` is one serial worker, so a queued draw either
    already ran or runs after this and puts the panel back, which is what pressing draw means.
    """
    if not any(b["n"] == n for b in board.beats):
        raise PanelsError(f"no such beat: {n}", status=404)
    _clear_extra_panels(board, n, 0)
    sheet(board)
