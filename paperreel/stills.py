"""Opening stills: Papercut Studio renders them, then the model looks at what came back.

`papercut.py` owns the HTTP seam to the image server. This owns the judgement around it --
which beats a still job may cover at all, and what happens when a still arrives that does
not belong in the reel.

The review pass is the point of this module. A style bible is words, and words land
differently on every generation: the same paragraph that produced a round-eared pink pig in
beat 1 produces a sharper-eared one in beat 4, and neither prompt was wrong. Conditioning
every still on the reel's locked cast reference fixed most of that, but not all of it, and
nothing in the pipeline ever checked. Now something does: the model has vision, and a look
costs one small request against the still it just paid for. A still that misses gets its asset
prompt rewritten against the specific thing that is wrong and is rendered again.

    made = stills.generate(board, [2, 3, 5], log=print)

The rewritten prompt is saved to the board, deliberately: the user should be able to read
what changed on the node, and the next render of that beat should start from the corrected
wording rather than from the one that already failed once.

The review can only ever answer one question -- "does this belong in the reel" -- and it is
not the question the director usually has. "The pig should be facing the other way" is not a
mismatch with anything; it is taste, and the review is explicitly told not to reject for it.
So every generated still also has a conversation of its own:

    stills.converse(board, 4, "she should be looking out of frame, not at us", log=print)

Same vision call, same cast reference alongside it, and it ends in the same two places: the
beat's `asset_prompt` is rewritten and the still is rendered again. What is deliberately
absent from that path is the automatic review -- see `converse`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import board as board_mod
from . import config, gemini, papercut

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
# `{medium}` rather than a literal, and this is the one substitution in this file that is not
# cosmetic: the reviewer REJECTS a still for not being the medium, so a clay reel judged against
# paper would have every still it rendered thrown away by its own review pass.
JUDGEMENT = (
    "Judge only these, and reject only for something you can name:\n"
    "- the characters: species, colour, markings, proportions, ear and tail shape, face. "
    "These must be the SAME design as the reference, in every detail.\n"
    "- the medium: {medium}, not a flat vector drawing.\n"
    "- the palette and the art direction.\n"
    "- the frame: tall vertical 9:16, the character fully inside it, no text, no watermark, "
    "no signature, no border or frame drawn around the picture.\n"
    "- whether the still actually shows what its prompt asked for.\n\n"
    "The setting, the framing, the scale and the pose are SUPPOSED to differ from the "
    "reference -- each beat is a different shot. Never reject a still for those."
)


# The conversation about one still. Same three outcomes every turn: the prompt this still will
# be drawn from next time, whether to draw it now, and what to tell the director.
#
# Declared in that order because the decode follows schema-property order, so the reply is
# written LAST -- after the model has committed to a prompt and to whether it is rendering.
# The other way round it announced changes it then did not make.
CHAT_SCHEMA = {
    "type": "object",
    "required": ["asset_prompt", "regenerate", "reply"],
    "properties": {
        "asset_prompt": {
            "type": "string",
            "description": (
                "The prompt this still is drawn from next time, rewritten to do what the "
                "director asked and nothing else. Return the current prompt unchanged when "
                "nothing about it should change."
            ),
        },
        "regenerate": {
            "type": "boolean",
            "description": (
                "true to render the still again now. Only false when the answer is words "
                "alone -- a question about the picture, or a note to keep for later."
            ),
        },
        "reply": {
            "type": "string",
            "description": (
                "One or two plain sentences to the director: what you changed, and what the "
                "next render will look like. No markdown, no lists."
            ),
        },
    },
}

CHAT_SYSTEM_TEMPLATE = (
    "You are the still editor for a {name} Instagram Reel studio. You are "
    "looking at ONE opening still with the director, and your job is to turn what they say "
    "about it into the prompt it is drawn from next time.\n\n"
    "The director is the authority on this picture. They are not asking you whether their note "
    "is a good idea -- if they want the puppet facing the other way, that is what the prompt "
    "now says. Two things are not theirs to overrule, because the rest of the pipeline depends "
    "on them: the medium ({essence}) and "
    "the frame (tall vertical 9:16, no text, no watermark, no signature, no drawn border).\n\n"
    "Rewrite the WHOLE prompt every time, carrying over every part of it the director did not "
    "ask you to change -- the setting, the framing, the scale, the moment, and the style "
    "bible's own words for the cast. Every other still in this reel is held to those words, so "
    "paraphrasing them here is how one shot quietly stops matching the rest.\n\n"
    "Rendering the still again sends the current image and its beat references through Gemini, "
    "so ask for it whenever the picture itself should change. Rendering the VIDEO is not something you can do; it "
    "costs real money and only the director starts it.\n\n"
    + config.MENTION_NOTE
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
    stills are the user's own work must not be able to spend a render on a stale tab's request;
    a beat that opens on the previous clip has nowhere to put a still.

    A reference beat used to be refused outright, because a still would have replaced the
    pictures it was conditioned on. It is the default cut now: the still goes in as <Picture 1>
    and the join does not move, so there is nothing left to protect it from.
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
    # The one reference shape that still cannot take a still: a beat carrying the previous
    # clip's tail already has its opening, and `config.build_prompt` may only ever give the model
    # one answer to where the shot begins. Generating for one would render a frame that never
    # reaches the graph, which is worse than refusing -- it looks like it worked.
    carrying = [n for n in beats if board.carries_motion(board.beat(n))]
    if carrying:
        raise StillsError(
            f"beats {carrying} open on the tail of the clip before them, so a still of their "
            "own would never be used. Turn off carrying on the node first if you want this "
            "scene to open on a still instead.",
        )
    if not beats:
        raise StillsError("no beat needs a still", status=422)
    return beats


def claim(board: board_mod.Board, beats: list[int]) -> None:
    """Record that these beats are getting a still of their own, before one is generated.

    An explicit per-beat request means "prepare this scene with its own image", even if it
    currently continues from the previous clip. Writing that down immediately keeps the canvas
    and the render queue agreeing about where the scene boundary is while generation is still
    queued.

    Only a plain continuation is moved, and it becomes a `reference` cut rather than an `asset`
    one -- that is what the default is now, so a beat arriving at its own shot arrives at the
    join a new shot would have been written as. The other three joins already have somewhere to
    put a still and are left exactly as they are:

      * a bridge lands on it, and promoting it would throw away the continuation the user chose;
      * a reference beat puts it in <Picture 1>, so there is nothing to change;
      * an `asset` beat wants its opening frame EXACTLY, which is the whole reason to pick that
        join over the default -- quietly moving it to ref2va would answer a request to redraw a
        still by changing which weights the scene renders on.
    """
    for n in beats:
        if board.source_for(board.beat(n)) == board_mod.SOURCE_CHAIN:
            board.beat(n)["source"] = board_mod.SOURCE_REFERENCE
    board.save()


def generate(board: board_mod.Board, beats: list[int], *,
             log: Callable[[str], None] = print,
             progress: Callable[[int, float], None] | None = None,
             announce: Callable[[], None] | None = None,
             cancelled: Callable[[], bool] | None = None,
             gemini_model: str | None = None,
             gemini_image_size: str | None = None) -> list[int]:
    """Render these beats' opening stills and review them. Returns the ones that landed.

    The beat that has no cast reference yet is rendered and reviewed entirely on its own
    first, before anything else starts. Its still IS the reference the rest are anchored to,
    so reviewing it at the end of the batch would be too late: rejecting it there would
    replace the reference every other still in the same run had already been matched against.

    What each still is conditioned on is `Board.still_pictures` -- the cast reference plus the
    director's uploads on that beat. The review below is deliberately shown only the cast
    reference: it answers "does this belong in the reel", and the uploads are the director's own
    intent for one shot rather than something the reel is held to.
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
                              announce=announce, cancelled=cancelled,
                              gemini_model=gemini_model, gemini_image_size=gemini_image_size)
            continue
        made += _attempts(board, remaining, log=log, progress=progress,
                          announce=announce, cancelled=cancelled,
                          gemini_model=gemini_model, gemini_image_size=gemini_image_size)
        remaining = []
    return sorted(set(made))


def _attempts(board: board_mod.Board, beats: list[int], *,
              log: Callable[[str], None],
              progress: Callable[[int, float], None] | None,
              announce: Callable[[], None] | None,
              cancelled: Callable[[], bool] | None,
              gemini_model: str | None = None,
              gemini_image_size: str | None = None) -> list[int]:
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
            gemini_model=gemini_model, gemini_image_size=gemini_image_size,
            # The board is republished per still rather than per batch: a nine-beat run is
            # minutes long, and the canvas filling in one node at a time is the whole reason
            # the image server streams its progress at all.
            on_still=None if announce is None else (lambda _n: announce()),
            cancelled=cancelled,
            # A beat that already had a still is being asked for a DIFFERENT one, so its seed
            # has to move. Papercut derives a frame's seed as `scene.seed + index`, and a
            # single-beat run is always index 0 -- so ✦ regenerate with the prompt unchanged
            # came back byte-identical, which reads as a button that did nothing. Only the
            # conversation moved the seed before this. Left alone for a batch, where every beat
            # sits at a different index and the board seed is what makes a reel reproducible.
            seed=(_retry_seed(board, pending[0])
                  if len(pending) == 1 and board.asset_path(pending[0]).is_file() else None),
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
    # Every verdict is also written into the still's own conversation, pass or fail. The
    # director reads that panel to find out why a picture looks the way it does, and "the
    # reviewer rewrote your prompt between the render you asked for and the one you got" is
    # exactly the kind of thing that is baffling anywhere else and obvious there.
    #
    # Each carries `verdict` -- "pass", "kept" or "rewritten" -- so the Assets stage can put a
    # pip beside a scene without reading the copy. Sniffing for "Kept: " client-side would work
    # today and break silently the first time somebody improves the wording. One optional key on
    # an existing transcript object: it reaches no renderer, it is in no fingerprint, and a board
    # written before this simply has turns without it.
    touched = False
    for n in beats:
        if cancelled is not None and cancelled():
            break
        try:
            verdict = review(board, n)
        except gemini.GeminiError as unavailable:
            log(f"[stills] beat {n}: not reviewed ({unavailable})")
            continue
        touched = True
        if verdict.get("consistent"):
            log(f"[stills] beat {n}: matches the reel")
            remember(board, n, "gemini", "Checked this against the reel — it matches.",
                     verdict="pass")
            continue
        problems = [str(p).strip() for p in verdict.get("problems") or [] if str(p).strip()]
        corrected = " ".join(str(verdict.get("asset_prompt") or "").split()).strip()
        for problem in problems:
            log(f"[stills] beat {n}: {problem}")
        # No corrected prompt means there is nothing to render differently, so rendering again
        # would just spend wall clock on the same generation with a new seed.
        if not corrected or corrected == (board.beat(n).get("asset_prompt") or "").strip():
            log(f"[stills] beat {n}: kept, the review had no different prompt to offer")
            remember(board, n, "gemini", "Kept: " + ("; ".join(problems) or "nothing to change")
                     + ", but no different prompt to render from.", verdict="kept")
            continue
        board.beat(n)["asset_prompt"] = corrected
        failed.append(n)
        log(f"[stills] beat {n}: prompt rewritten -> {corrected}")
        remember(board, n, "gemini",
                 "; ".join(problems) or "This did not match the reel.", prompt=corrected,
                 regenerated=True, verdict="rewritten")
    if failed or touched:
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
        raise gemini.GeminiError(f"beat {n} has no still to look at")
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
    parts.append(f"REQUIRED OF EVERY STILL: {board.look().still}")
    if prompt:
        parts.append(f"THIS STILL WAS ASKED FOR AS: {prompt}")
        # This pass rewrites `asset_prompt` without anyone asking for it, and it is told to
        # correct "the problems and nothing else" -- which a model reads as licence to normalise
        # an unfamiliar @ref: token into prose. Of the five prompts that can rewrite a field
        # holding tokens, this is the one nobody is watching when it does.
        if config.mention_bodies(prompt):
            parts.append(config.MENTION_NOTE)
    parts.append(JUDGEMENT.format(medium=board.look().judge))
    parts.append("Return JSON only.")
    return gemini.structured(
        [{"role": "user", "content": "\n\n".join(parts),
          "images": [gemini.encode(path) for path in images]}],
        REVIEW_SCHEMA,
        # Near-deterministic on purpose: this is a check, and a reviewer that changes its mind
        # between runs turns "generate the stills" into a dice roll on how many get re-rendered.
        temperature=0.1,
        model=config.VISION_MODEL,
    )


# ## Talking to one still
#
# The transcript lives on the beat, in `storyboard.json`, for the same reason the board's own
# conversation does: it is the record, so it survives a reload, a restart and a hand edit, and
# there is still exactly one database.


def remember(board: board_mod.Board, n: int, role: str, text: str, **extra) -> dict:
    """Add one line to a still's conversation and hand it back, so a later step can amend it.

    Returned rather than just appended because a turn is written BEFORE the render it asks
    for -- the node should show the rewritten prompt while the picture is being drawn, not
    after -- and what happened to that render is only known a minute later. The caller holds
    the dict and fills the outcome in.

    Trimmed to `config.ASSET_CHAT_MEMORY`, because this is the one thing on a beat that grows
    without bound and the board document is read whole on every canvas refresh.
    """
    turn = {"role": role, "text": text,
            **{key: value for key, value in extra.items() if value is not None}}
    chat = board.beat(n).setdefault("asset_chat", [])
    chat.append(turn)
    del chat[:-config.ASSET_CHAT_MEMORY]
    return turn


def discussable(board: board_mod.Board, n: int) -> None:
    """May this beat's still be talked about at all? Raises with the reason if not.

    Batch generation still refuses a manual-stills reel, but an uploaded still may be refined:
    that is an edit of a supplied image, not filling the reel's stills automatically. The other
    guards are a beat that opens on the previous clip, a beat number that is not on this board,
    and the requirement that there be a picture to look at.
    """
    if board.data.get("manual_stills"):
        if not any(b["n"] == n for b in board.beats):
            raise StillsError(f"no such beat: {n}", status=404)
        if board.carries_motion(board.beat(n)):
            raise StillsError(
                f"beat {n} opens on the previous clip's tail, so it has no still to refine.",
            )
    else:
        wanted(board, [n])
    if not board.asset_path(n).is_file():
        raise StillsError(
            f"beat {n} has no still yet. Generate or upload one, then say what to change "
            "about it.",
            status=422,
        )


def chat_system(board: board_mod.Board) -> str:
    """The system prompt, with this board's medium in it.

    A function rather than a constant because the sentence it opens with names the film's
    medium, and a clay reel told it is a paper-cutout studio would take that as the
    instruction it is -- this prompt's whole job is to say which half of a director's note
    the pipeline will not let them overrule."""
    look = board.look()
    return CHAT_SYSTEM_TEMPLATE.format(name=look.name, essence=look.essence)


def converse(board: board_mod.Board, n: int, message: str, *,
             attached: int = 0,
             log: Callable[[str], None] = print,
             progress: Callable[[int, float], None] | None = None,
             announce: Callable[[], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> dict:
    """One turn of the conversation about beat `n`'s still, render included.

    A structured call rather than a tool loop, unlike `agent.turn`. There are only two things
    that can come out of looking at a picture with someone -- what it should say instead, and
    whether to draw it again -- so a loop would be a round trip spent deciding to do the only
    thing available.

    `attached` is how many reference pictures the director sent along with this note; they are
    already on the beat by the time this runs (the API stores them, exactly as the node's
    ⤒ add picture does, because `Board.still_pictures` is what the still renderer reads and it
    reads it off the board). It forces the re-render rather than leaving that to the model: new
    pictures change what the still is drawn from, so the one on screen is out of date whatever
    the model makes of the words.

    **The automatic review deliberately does not run on what this renders.** The reviewer's
    whole job is holding a still to the cast reference, and half of what a director asks for
    here is a departure from it: told "make her ears rounder", the reviewer compares the result
    against a reference with sharp ears, calls it drift, and rewrites the prompt back. The
    review is for stills nobody has looked at. This one has been.

    The seed is held across an edit and moved for a plain retry, which is `papercut.generate`'s
    one reason to take one -- see `_scene_body`.
    """
    discussable(board, n)
    before = (board.beat(n).get("asset_prompt") or "").strip()
    verdict = gemini.structured(
        _chat_messages(board, n, message, attached=attached), CHAT_SCHEMA,
        # Warmer than the review's 0.1: this one is writing a prompt, not checking one, and a
        # near-deterministic decode answers a second attempt at the same note with the same
        # words -- which reads as not having listened.
        temperature=0.4,
        model=config.VISION_MODEL,
    )
    corrected = " ".join(str(verdict.get("asset_prompt") or "").split()).strip()
    reply = " ".join(str(verdict.get("reply") or "").split()).strip()
    # A picture arriving with the note is not the model's call: the conditioning changed, so
    # what is on screen was drawn from something the beat no longer says.
    regenerate = bool(verdict.get("regenerate")) or attached > 0
    changed = bool(corrected) and corrected != before
    # A rewrite that drops an @ref: token does not fail -- it renders a still no longer told
    # about a picture it is still conditioned on. Unrepairable here (only the model knows where
    # it meant them), so it goes in the transcript the director already reads.
    lost = config.lost_mentions(before, corrected) if changed else []
    if lost:
        log(f"[stills] beat {n}: the rewrite dropped {', '.join(lost)}")
    if changed:
        board.beat(n)["asset_prompt"] = corrected
        log(f"[stills] beat {n}: prompt rewritten -> {corrected}")

    remember(board, n, "user", message)
    spoken = remember(
        board, n, "gemini",
        reply or ("Rewrote the prompt." if changed else "Nothing to change."),
        prompt=corrected if changed else None,
        regenerated=regenerate or None,
        error=(f"this rewrite dropped {', '.join(lost)} -- put it back if it mattered"
               if lost else None),
    )
    # Saved and published before the render starts, not after: the rewritten prompt is what the
    # picture is about to be drawn from, and the node should be showing it while that happens.
    board.save()
    if announce is not None:
        announce()
    if not regenerate:
        return {"reply": spoken["text"], "asset_prompt": corrected or before, "regenerated": False}

    if board.reference_for(n) is None:
        # Not a refusal -- redrawing the reference has to stay possible or the first image a
        # board ever produced would lock its cast forever. But it is worth saying out loud.
        log(f"[stills] beat {n} IS this reel's cast reference, so this redraws what every "
            "other still is matched against")
    try:
        made = papercut.generate(
            board, [n], log=log, progress=progress,
            gemini_model=board.beat(n).get("gemini_model"),
            gemini_image_size=board.beat(n).get("gemini_image_size"),
            on_still=None if announce is None else (lambda _n: announce()),
            cancelled=cancelled,
            seed=None if changed else _retry_seed(board, n),
        )
    except papercut.PapercutError as unavailable:
        # The prompt is already saved, and that is most of the value of the turn. Failing the
        # whole job here would throw away a rewrite the director can see is right, over an
        # image server they can start in one command and then press ✦ regenerate.
        log(f"[stills] beat {n}: {unavailable}")
        spoken["regenerated"] = False
        spoken["error"] = str(unavailable)
        board.save()
        if announce is not None:
            announce()
        return {"reply": spoken["text"], "asset_prompt": corrected or before,
                "regenerated": False, "error": str(unavailable)}
    spoken["regenerated"] = bool(made)
    board.save()
    if announce is not None:
        announce()
    return {"reply": spoken["text"], "asset_prompt": corrected or before,
            "regenerated": bool(made)}


def _retry_seed(board: board_mod.Board, n: int) -> int:
    """A seed this beat has not been drawn on before, for a re-render with no prompt change.

    Counted off the length of the conversation, so it moves once per turn and a board that is
    reloaded, or hand-edited, or asked the same thing twice lands somewhere new each time --
    without needing a clock or a random number, neither of which belongs in a document that is
    supposed to describe the same reel tomorrow.
    """
    turns = len(board.beat(n).get("asset_chat") or [])
    return int(board.data.get("seed") or 0) + 1000 * n + turns


def _history(board: board_mod.Board, n: int) -> str:
    """What has already been said about this still, labelled as history rather than as fact.

    Same lesson as `agent.transcript`: the model treats its own earlier sentences as the most
    authoritative thing in the prompt, so an unlabelled transcript has it answering about the
    version of the picture it was describing three turns ago instead of the one it can see.
    """
    turns = (board.beat(n).get("asset_chat") or [])[-config.ASSET_CHAT_HISTORY:]
    if not turns:
        return ""
    lines = "\n".join(
        f'{"DIRECTOR" if turn.get("role") == "user" else "YOU"}: {turn.get("text", "")}'
        for turn in turns
    )
    return (
        "WHAT HAS ALREADY BEEN SAID ABOUT THIS STILL -- history only. Some of it describes a "
        "version that has since been rendered again. Never answer a question about what the "
        "picture looks like from here; look at the image.\n"
        f"{lines}"
    )


def _chat_messages(board: board_mod.Board, n: int, message: str,
                   attached: int = 0) -> list[dict]:
    """The prompt for one turn: the pictures, the reel's rules, the history, then the note.

    The pictures are `Board.still_pictures` -- what this still is actually drawn from -- and
    then the still itself, last. Showing the director's own uploads here rather than only the
    cast reference is what makes "give her the scarf from the picture I just sent" answerable
    instead of guessed at. The automatic `review` is deliberately shown only the cast reference,
    because it answers a different question: whether the still belongs in the reel, which the
    director's intent for one shot has no bearing on.

    Numbered rather than tagged. `<Picture 1>` is the *video* model's vocabulary, and asked in
    it the reviewer answered about one of the two images it had been given -- but "the first
    image / the second image" does not scale past two, so the images are simply listed.

    The director's note goes LAST, after everything it might be about, for the reason
    `agent.turn` puts the board after the transcript -- whatever sits nearest the question is
    what gets answered.
    """
    beat = board.beat(n)
    reference = board.reference_for(n)
    drawn_from = board.still_pictures(n)
    images: list[Path] = [path for path, _ in drawn_from] + [board.asset_path(n)]
    parts: list[str] = []
    listed = []
    for index, (path, note) in enumerate(drawn_from, start=1):
        if reference is not None and path == reference:
            listed.append(
                f"{index}. this reel's locked cast reference: it fixes what the characters, the "
                "materials and the palette look like, and this still is held to it."
            )
        else:
            said = " ".join(str(note or "").split())
            listed.append(
                f"{index}. a picture the director attached to this shot, which this still is "
                "drawn from as well as the clip"
                + (f". They say it is for: {said}" if said else ".")
            )
    listed.append(f"{len(images)}. the still you are talking about.")
    parts.append(
        (f"You are given {len(images)} images, in this order:\n" if len(images) > 1
         else "You are given one image:\n") + "\n".join(listed)
    )
    if reference is None:
        parts.append(
            "This still is also this reel's cast reference -- every other still in the film is "
            "matched against it. Anything you change about the characters here changes them for "
            "the whole reel, which is allowed, and is worth saying in your reply."
        )
    if attached:
        # Not necessarily all of them in the list above: the still renderer takes far fewer
        # pictures than the video model, so a beat already at `config.MAX_STILL_REFS` steers its
        # clip with the new picture and its still with the older ones.
        parts.append(
            f'The director has just attached {attached} new '
            + (f"pictures to this shot, so the still on screen was drawn before they existed."
               if attached != 1 else
               "picture to this shot, so the still on screen was drawn before it existed.")
            + " Write the prompt so the next render uses what the pictures above show, and "
              "render it again."
        )

    history = _history(board, n)
    if history:
        parts.append(history)
    parts.append(f"STYLE BIBLE: {board.identity()}")
    parts.append(f"REQUIRED OF EVERY STILL: {board.look().still}")
    if beat.get("scene"):
        parts.append(f"THE SHOT THIS STILL BELONGS TO: {beat['scene']}")
    if beat.get("action"):
        parts.append(f"WHAT MOVES ONCE THE CLIP STARTS: {beat['action']}")
    # A bridge's still is where the clip ENDS, so a note about "the opening frame" would be
    # about the wrong picture entirely.
    parts.append(
        f"THIS STILL IS THE FRAME BEAT {n} MUST ARRIVE AT, at the end of a shot that carries "
        "on from the beat before it."
        if board.source_for(beat) == board_mod.SOURCE_BRIDGE else
        f"THIS STILL IS THE COMPOSITION BEAT {n} OPENS ON."
    )
    parts.append(f"THE PROMPT IT WAS DRAWN FROM: {(beat.get('asset_prompt') or '').strip()}")
    parts.append(f"THE DIRECTOR SAYS: {message}")
    parts.append("Return JSON only.")
    return [
        {"role": "system", "content": chat_system(board)},
        {"role": "user", "content": "\n\n".join(parts),
         "images": [gemini.encode(path) for path in images]},
    ]
