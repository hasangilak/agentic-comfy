"""Opening stills: Papercut Studio renders them, then the model looks at what came back.

`papercut.py` owns the HTTP seam to the image server. This owns the judgement around it --
which beats a still job may cover at all, and what happens when a still arrives that does
not belong in the reel.

The review pass is the point of this module. A style bible is words, and words land
differently on every generation: the same paragraph that produced a round-eared pink pig in
beat 1 produces a sharper-eared one in beat 4, and neither prompt was wrong. Conditioning
every still on the reel's locked cast reference fixed most of that, but not all of it, and
nothing in the pipeline ever checked. Now something does: the model has vision, it is local
and unmetered, and a look costs about three seconds. A still that misses gets its asset
prompt rewritten against the specific thing that is wrong and is rendered again.

    made = stills.generate(board, [2, 3, 5], log=print)

The rewritten prompt is saved to the board, deliberately: the user should be able to read
what changed on the node, and the next render of that beat should start from the corrected
wording rather than from the one that already failed once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import board as board_mod
from . import config, papercut, qwen

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["consistent", "problems", "asset_prompt"],
    "properties": {
        "consistent": {
            "type": "boolean",
            "description": (
                "true if this still belongs in the reel as it is. Only false for a concrete, "
                "nameable mismatch -- not for a preference about the composition."
            ),
        },
        "problems": {
            "type": "array",
            "description": "One short line per mismatch, naming what is wrong. Empty when consistent.",
            "items": {"type": "string"},
        },
        "asset_prompt": {
            "type": "string",
            "description": (
                "The prompt to render this still again from, correcting the problems and "
                "nothing else -- same setting, same framing, same moment. Return the original "
                "prompt unchanged when consistent is true."
            ),
        },
    },
}

# What the reviewer is allowed to reject for. Written as an exclusion list rather than a
# checklist because the first version, which only asked "does this match?", rejected almost
# everything: a still whose setting differed from the reference IS correct -- that is what a
# cut is for -- and a model with no instruction to the contrary reads any difference as drift.
JUDGEMENT = (
    "Judge only these, and reject only for something you can name:\n"
    "- the characters: species, colour, markings, proportions, ear and tail shape, face. "
    "These must be the SAME design as the reference, in every detail.\n"
    "- the medium: layered paper-cutout with visible paper grain, crisp cut edges, soft "
    "contact shadows. Not a photograph, not a 3D render, not a flat vector drawing.\n"
    "- the palette and the art direction.\n"
    "- the frame: tall vertical 9:16, the character fully inside it, no text, no watermark, "
    "no signature, no border or frame drawn around the picture.\n"
    "- whether the still actually shows what its prompt asked for.\n\n"
    "The setting, the framing, the scale and the pose are SUPPOSED to differ from the "
    "reference -- each beat is a different shot. Never reject a still for those."
)


class StillsError(RuntimeError):
    """A still job that must not run. `status` is the HTTP code the API answers with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def wanted(board: board_mod.Board, requested: list[int] | None) -> list[int]:
    """Which beats a still job may cover, or why it may not run.

    Enforced here rather than in the HTTP layer alone, because there are three ways in now --
    the canvas button, a conversation asking for stills, and the CLI -- and the reasons a
    generation must not happen are properties of the board, not of the request. A board whose
    stills are the user's own work must not be able to spend a render on a stale tab's
    request; a reference beat must not be handed a still, which would silently drop its
    pictures and turn it into a cut.
    """
    if board.data.get("manual_stills"):
        raise StillsError(
            "this reel supplies its own opening stills, so image generation is off. Upload "
            "them, or switch the stills back to generated on the script node.",
        )
    beats = board.to_json()["assets_needed"] if requested is None else list(requested)
    unknown = [n for n in beats if not any(b["n"] == n for b in board.beats)]
    if unknown:
        raise StillsError(f"no such beats: {unknown}", status=404)
    referenced = [n for n in beats
                  if board_mod.uses_refs(board.source_for(board.beat(n)))]
    if referenced:
        raise StillsError(
            f"beats {referenced} are conditioned on reference pictures, not on an opening "
            "still. Change the join on the node first if you want a still instead.",
        )
    if not beats:
        raise StillsError("no beat needs a still", status=422)
    return beats


def claim(board: board_mod.Board, beats: list[int]) -> None:
    """Record that these beats are getting a still of their own, before one is generated.

    An explicit per-beat request means "prepare this scene with its own image", even if it
    currently continues from the previous clip. Writing that down immediately keeps the canvas
    and the render queue agreeing about where the scene boundary is while generation is still
    queued. A bridge is left alone -- it already has its own image, as the frame it lands on,
    and promoting it to a cut would throw away the continuation the user chose.
    """
    for n in beats:
        if board.source_for(board.beat(n)) != board_mod.SOURCE_BRIDGE:
            board.beat(n)["source"] = board_mod.SOURCE_ASSET
    board.save()


def generate(board: board_mod.Board, beats: list[int], *,
             log: Callable[[str], None] = print,
             progress: Callable[[int, float], None] | None = None,
             announce: Callable[[], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> list[int]:
    """Render these beats' opening stills and review them. Returns the ones that landed.

    The beat that has no cast reference yet is rendered and reviewed entirely on its own
    first, before anything else starts. Its still IS the reference the rest are anchored to,
    so reviewing it at the end of the batch would be too late: rejecting it there would
    replace the reference every other still in the same run had already been matched against.
    """
    made: list[int] = []
    remaining = list(beats)
    while remaining:
        if cancelled is not None and cancelled():
            break
        if board.reference_for(remaining[0]) is None:
            head = remaining.pop(0)
            log(f"[stills] beat {head} defines the look, so it is settled before the rest")
            made += _attempts(board, [head], log=log, progress=progress,
                              announce=announce, cancelled=cancelled)
            continue
        made += _attempts(board, remaining, log=log, progress=progress,
                          announce=announce, cancelled=cancelled)
        remaining = []
    return sorted(set(made))


def _attempts(board: board_mod.Board, beats: list[int], *,
              log: Callable[[str], None],
              progress: Callable[[int, float], None] | None,
              announce: Callable[[], None] | None,
              cancelled: Callable[[], bool] | None) -> list[int]:
    """Render, review, rewrite the rejects' prompts, render those again."""
    made: set[int] = set()
    pending = list(beats)
    for attempt in range(1, max(1, config.STILL_ATTEMPTS) + 1):
        if not pending or (cancelled is not None and cancelled()):
            break
        if attempt > 1:
            log(f"[stills] beats {pending}: rendering again from the rewritten prompts")
        landed = papercut.generate(
            board, pending, log=log, progress=progress,
            # The board is republished per still rather than per batch: a nine-beat run is
            # minutes long, and the canvas filling in one node at a time is the whole reason
            # the image server streams its progress at all.
            on_still=None if announce is None else (lambda _n: announce()),
            cancelled=cancelled,
        )
        made |= set(landed)
        if not landed or not config.STILL_REVIEW or attempt >= max(1, config.STILL_ATTEMPTS):
            break
        pending = _rejected(board, landed, log=log, cancelled=cancelled)
        if pending and announce is not None:
            # The rewritten prompts are on the board now, so the nodes should show them
            # before the second render starts rather than after it finishes.
            announce()
    return sorted(made)


def _rejected(board: board_mod.Board, beats: list[int], *,
              log: Callable[[str], None],
              cancelled: Callable[[], bool] | None) -> list[int]:
    """Review each still; rewrite the prompt of the ones that missed. Returns those beats.

    A review that fails outright is treated as a pass. The still is on disk and usable, the
    user can see it, and losing a finished frame because the model would not answer about it
    would be the wrong trade in every case.
    """
    failed: list[int] = []
    for n in beats:
        if cancelled is not None and cancelled():
            break
        try:
            verdict = review(board, n)
        except qwen.OllamaError as unavailable:
            log(f"[stills] beat {n}: not reviewed ({unavailable})")
            continue
        if verdict.get("consistent"):
            log(f"[stills] beat {n}: matches the reel")
            continue
        problems = [str(p).strip() for p in verdict.get("problems") or [] if str(p).strip()]
        corrected = " ".join(str(verdict.get("asset_prompt") or "").split()).strip()
        for problem in problems:
            log(f"[stills] beat {n}: {problem}")
        # No corrected prompt means there is nothing to render differently, so rendering again
        # would just spend wall clock on the same generation with a new seed.
        if not corrected or corrected == (board.beat(n).get("asset_prompt") or "").strip():
            log(f"[stills] beat {n}: kept, the review had no different prompt to offer")
            continue
        board.beat(n)["asset_prompt"] = corrected
        failed.append(n)
        log(f"[stills] beat {n}: prompt rewritten -> {corrected}")
    if failed:
        board.save()
    return failed


def review(board: board_mod.Board, n: int) -> dict:
    """Look at one finished still and say whether it belongs in this reel.

    Two images when there is a cast reference, one when this beat IS the reference. Referred
    to as "the first image" and "the second image" rather than with tags like <Picture 1>:
    the tag vocabulary is what the *video* model was trained on, and asked that way here the
    reviewer answered about only one of the two pictures it had been given.
    """
    still = board.asset_path(n)
    if not still.is_file():
        raise qwen.OllamaError(f"beat {n} has no still to look at")
    reference = board.reference_for(n)
    prompt = (board.beat(n).get("asset_prompt") or "").strip()
    parts: list[str] = []
    images: list[Path] = []
    if reference is not None:
        images.append(reference)
        parts.append(
            "The first image is this reel's locked cast reference: it fixes what the "
            "characters, the materials and the palette look like. The second image is a new "
            "opening still for a different shot in the same reel."
        )
    else:
        parts.append(
            "The image is the opening still that will define the look of a whole reel, so "
            "there is nothing yet to compare it against -- judge it against the description "
            "below instead."
        )
    images.append(still)
    parts.append(f"STYLE BIBLE: {board.identity()}")
    parts.append(f"REQUIRED OF EVERY STILL: {config.ASSET_STYLE_SUFFIX}")
    if prompt:
        parts.append(f"THIS STILL WAS ASKED FOR AS: {prompt}")
    parts.append(JUDGEMENT)
    parts.append("Return JSON only.")
    return qwen.structured(
        [{"role": "user", "content": "\n\n".join(parts),
          "images": [qwen.encode(path) for path in images]}],
        REVIEW_SCHEMA,
        # Near-deterministic on purpose: this is a check, and a reviewer that changes its mind
        # between runs turns "generate the stills" into a dice roll on how many get re-rendered.
        temperature=0.1,
        model=config.QWEN_VISION_MODEL,
    )
