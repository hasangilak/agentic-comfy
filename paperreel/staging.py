"""The reel's cast and sets, designed once: drawing a staging sheet, and talking about one.

`pictures.py` is this module one level down -- the same three operations on a picture that
belongs to one beat. The difference is scope, and scope is the whole point: a picture uploaded
to beat 3 conditions beat 3, so a second character had nowhere to live and the same clearing was
redrawn from the same paragraph in every shot that used it. A staging entry is named, written
down, drawn once, and bound to whichever beats contain it.

    entry = board.add_stage(kind="character", name="Vera", draw="a fox mother, side on")
    staging.draw(board, entry["id"])
    staging.converse(board, entry["id"], "her chest should be cream, not white")

There is deliberately no "hold this sheet to the cast still" pass, for the reason
`pictures.py` gives at greater length: a design sheet is *supposed* to differ from a composed
shot -- it is a second character, an empty set, a prop -- and a reviewer told to match the
cast would reject almost every one. What *does* run, from `draw` so an agent cannot skip it,
is hold the sheet to **its own note**: the eye count the bible named, the closed palette, no
extra parts, no characters on a set, no scenery on a character. That is a different question
from the stills reviewer, and this module is upstream of that reviewer rather than beside it:
what these sheets are is the thing later stills get held to.

Three things about how a sheet is drawn are the same measured lesson wearing different clothes,
and all three are recorded next to the code that acts on them:

  * **nothing conditions a first draw** unless the director names a sibling with `@stage:`. A
    model shown the cast draws the cast -- "a single iron-grey club" against a fox reference came
    back as the fox. The medium travels as words instead.
  * **the board's style bible never reaches this render.** It describes the cast and the set, and
    a prop sheet is neither. `board.look().sheet` (prop), `.model` (character) or `.set`
    goes in the scene `style` slot in its place.
  * **a redraw is `consistency="edit"`**, the one conditioned mode with no continuity clause.
    Every other clause ends "but move the subject into a clearly different pose and position",
    which is right for the next frame of a moving sequence and exactly wrong when the reference
    IS the picture being changed and the note said "make the club longer".
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import board as board_mod
from . import config, gemini, papercut

# One turn of the conversation about one staging sheet. The same three outcomes as a still's and
# a picture's, and declared in the same order and for the same reason: the decode follows
# schema-property order, so `reply` is written LAST -- after the model has committed to a prompt
# and to whether it is drawing. The other way round it announces a change it then does not make.
#
# `note` is deliberately NOT a fourth field, exactly as `ref_prompts` is absent from
# `pictures.DRAW_CHAT_SCHEMA`. What a design IS -- the sentence both prompts are told, "Vera, the
# fox mother in warm orange" -- is the director's, and a conversation about how the sheet LOOKS
# must not quietly rewrite what every shot in the film is told this character is.
CHAT_SCHEMA = {
    "type": "object",
    "required": ["draw", "regenerate", "reply"],
    "properties": {
        "draw": {
            "type": "string",
            "description": (
                "The prompt this design sheet is drawn from next time, rewritten to do what the "
                "director asked and nothing else. Return the current prompt unchanged when "
                "nothing about it should change."
            ),
        },
        "regenerate": {
            "type": "boolean",
            "description": (
                "true to draw the sheet again now. Only false when the answer is words alone -- "
                "a question about it, or a note to keep for later."
            ),
        },
        "reply": {
            "type": "string",
            "description": (
                "One or two plain sentences to the director: what you changed, and what the "
                "next draw will look like. No markdown, no lists."
            ),
        },
    },
}

# Same shape as `stills.REVIEW_SCHEMA`, and `draw` last for the same decode-order reason that
# module records: a field written first becomes the model's scratchpad.
REVIEW_SCHEMA = {
    "type": "object",
    "required": ["consistent", "problems", "draw"],
    "properties": {
        "consistent": {
            "type": "boolean",
            "description": (
                "true if this sheet is the thing its note describes. Only false for a concrete, "
                "nameable mismatch with the note or the kind's framing -- not for a preference "
                "about pose."
            ),
        },
        "problems": {
            "type": "array",
            "description": "One short line per mismatch, naming what is wrong. Empty when consistent.",
            "items": {"type": "string"},
        },
        "draw": {
            "type": "string",
            "description": (
                "The prompt to draw this sheet again from, correcting the problems and nothing "
                "else. Return the original prompt unchanged when consistent is true."
            ),
        },
    },
}

# Hold the sheet to its note, never to a still. `{kind_rules}` is the one substitution that is
# not cosmetic: a character sheet judged as a set would fail for having a subject, which is
# the whole point of a character sheet.
KIND_RULES = {
    "character": (
        "This is a CHARACTER model sheet: four labeled sections of ONE puppet on a plain "
        "ground (turnaround, expressions, head detail, sampled palette). Many cells of the "
        "SAME character are required, not a fail. Reject a second distinct character, scenery, "
        "a composed shot, or identity drift across cells -- eye count, limb count, markings, "
        "garment, palette vs the NOTE. Head crops in expression and detail cells are correct; "
        "a cropped turnaround figure is a fail. A missing required section is a fail. Do not "
        "reject for messy labels or inaccurate hex. Anatomy must match the NOTE and style "
        "bible exactly -- an extra spinneret or a missing eye is a fail. Colours only those "
        "the note or bible named for this character."
    ),
    "prop": (
        "This is a PROP sheet: one object complete and centred on a plain ground. Reject "
        "scenery, characters, or a second object the note did not name. Colours only those "
        "the note or bible named for this prop."
    ),
    "environment": (
        "This is a SET sheet: ONE single continuous view of the place, empty of cast, framed "
        "as the shots in it will be framed. A sheet split into several stacked or tiled "
        "frames -- a grid, a triptych, a storyboard of views -- is a fail: it conditions "
        "every still as if the set were three different places. A figure in the set is a "
        "fail. No decorative picture-frame, shadowbox, "
        "wooden tabletop, or gallery wall around the diorama unless the NOTE asked for one "
        "-- that frame becomes the set. Colours only those the note or bible named for this "
        "place; a new sky or ground colour is a fail. The architecture the note described, "
        "not a different place."
    ),
}

JUDGEMENT = (
    "Judge only these, and reject only for something you can name:\n"
    "- the note: this sheet must BE the thing the note describes, in every countable detail.\n"
    "- the kind's framing:\n{kind_rules}\n"
    "- the medium: {medium}. A sheet that looks like a 3D render or a live photograph is a fail.\n"
    "Do NOT reject this sheet for differing from a still or from another shot -- a design "
    "sheet is not a shot, and it is supposed to look like a design sheet. Do not reject for "
    "pose taste when the anatomy is otherwise the note.\n"
    "If you reject, rewrite the draw prompt to fix the named problems and nothing else."
)

# What a design sheet is FOR, in the words the model needs before it can write one. Deliberately
# insistent about the two failures that were measured on reference pictures one level down: a
# sheet that comes back as a composed shot, and a sheet whose subject has been replaced by
# whatever else the model was shown.
SYSTEM_TEMPLATE = (
    "You are the design editor for a {name} Instagram Reel studio. You are "
    "looking at ONE design sheet with the director -- a character, a set, or a prop -- and your "
    "job is to turn what they say about it into the prompt it is drawn from next time.\n\n"
    "A design sheet is not a shot. It is the locked design of one thing, drawn so that every "
    "shot in the film containing that thing can be held to it. A PROP sheet shows the subject "
    "complete and centred on a plain ground: nothing cropped, no scenery, no staging, no "
    "implied camera. A CHARACTER sheet is a model sheet of that one puppet -- turnaround, "
    "expressions, head angles and a sampled palette, packed as labeled sections on a plain "
    "ground; many cells of the SAME character, never a second creature, never a composed "
    "shot. Keep that layout on a redraw. A SET sheet is the place itself with no characters "
    "in it at all, framed as the shots in it will be framed. If the director asks a character "
    "sheet for a story composition, they are asking for the wrong thing here; say so in your "
    "reply while still doing what they asked to the subject, and keep the model-sheet layout.\n\n"
    "WHAT THIS SHEET IS OF DOES NOT CHANGE. The prompt you write describes the SAME thing the "
    "current prompt describes, with the director's note applied to it. If it is a club, it stays "
    "a club. If you are shown other sheets alongside it, they are there for the paper, the "
    "palette and the light ONLY -- replacing this sheet's subject with something from one of "
    "them is the single mistake that makes it useless.\n\n"
    "The director is the authority on this design. What is not theirs to overrule is the medium "
    "-- {essence} -- because every other "
    "image in the reel is made of it.\n\n"
    "Rewrite the WHOLE prompt every time, carrying over every part the director did not ask you "
    "to change. Drawing it again is a new Gemini request, so ask for it whenever the sheet "
    "itself should change. Rendering the VIDEO is not something you can do; it costs real money "
    "and only the director starts it.\n\n"
    + config.MENTION_NOTE
)


class StagingError(RuntimeError):
    """A staging job that must not run. `status` is the HTTP code the API answers with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def style_for(board: board_mod.Board, entry: dict) -> str:
    """The scene `style` one sheet is drawn under -- the medium, never the board's style bible.

    Three suffixes, picked off the kind, and the split is not cosmetic. `look().sheet` asks
    for "the subject complete and centred" on a "plain neutral background" with "no scenery",
    which describes a prop and is the exact opposite of a set -- handed that suffix, "a
    moonlit clearing ringed with birches" comes back as a single birch on grey. `look().model`
    is the other half of that lesson: a character sheet that shares the prop suffix comes back
    as one centred portrait, and H3 never sees the turnaround. A set gets `look().set`.

    The board's style bible is absent from all three, and that is what `papercut.draw`'s
    `style` override exists for. It describes the cast and the set, so on a real board Gemini
    would be handed "a single iron-grey club. A single fox ... on layered green paper hills."
    and draw the fox. Measured on a live render, one level down, in `pictures.draw_text`.
    """
    kind = board.stage_kind(entry)
    look = board.look()
    if kind == config.STAGE_ENVIRONMENT:
        return look.set
    if kind == config.STAGE_CHARACTER:
        return look.model
    return look.sheet


def aspect_for(board: board_mod.Board, entry: dict) -> str:
    """The shape one sheet is drawn at: landscape for a character pack, square for a prop,
    the reel's frame for a set.

    A set is the one design sheet whose framing is load-bearing -- what a still needs from it
    is how much of this clearing sits above the puppet's head, and a square answers a
    different question. A character model sheet is the other kind the square is wrong for:
    four labeled sections side by side, crushed at 1:1. None of these presets constrain
    anything downstream: a sheet is conditioning and is never handed to H3 as a frame, so
    unlike `config.PAPERCUT_ASPECT` they are free to be retuned.
    """
    kind = board.stage_kind(entry)
    if kind == config.STAGE_ENVIRONMENT:
        return config.PAPERCUT_SET_ASPECT
    if kind == config.STAGE_CHARACTER:
        return config.PAPERCUT_CHAR_ASPECT
    return config.PAPERCUT_REF_ASPECT


def drawable(board: board_mod.Board, entry_id: str, draw_prompt: str | None = None) -> dict:
    """May this sheet be drawn at all? Returns the entry, or raises with the reason.

    Far shorter than `pictures.drawable`, and the missing checks are the interesting part. There
    is no join guard: a beat's picture reaches no renderer unless the beat is on the reference
    join, but a staging sheet reaches every join -- as pictures where there are picture slots,
    and as words everywhere else -- so there is nothing here that could be drawn into a render
    nobody will see. And there is no slot budget: `MAX_STAGE_SHEETS` is enforced when an entry is
    minted, because unlike a picture a sheet has no numbered position to run out of.
    """
    if board.data.get("manual_stills"):
        raise StagingError(
            "this reel supplies its own pictures, so new image generation is off. Upload the "
            "sheet instead, or switch the stills back to generated on the script node.",
        )
    try:
        entry = board.stage_entry(entry_id)
    except KeyError:
        raise StagingError(f"no design called {entry_id!r} on this reel", status=404)
    stored = str(board.stage_field(entry, "draw")).strip()
    if not stored and not (draw_prompt or "").strip():
        # Two different situations behind one missing prompt, and they want different sentences.
        # A sheet on disk with nothing written about it was UPLOADED, and drawing it from nothing
        # would replace the director's own image with an invention; a design with neither has
        # simply not been described yet, and telling that user their picture was uploaded is
        # baffling.
        raise StagingError(
            (f"{board.stage_name(entry)} was uploaded, not drawn, so there is no prompt to draw "
             "it from. Say what it should be first."
             if board.stage_path(entry_id).is_file()
             else f"say what {board.stage_name(entry)} should look like first"),
            status=422,
        )
    return entry


def conditioning(board: board_mod.Board, entry_id: str, prompt: str) -> papercut.Pictures:
    """What a sheet is drawn FROM: itself when it exists, then any sibling the prompt names.

    **Nothing else, and a first draw of an unnamed sheet is conditioned on nothing at all.** The
    obvious design anchors every sheet on the reel's cast reference so the whole bible is made of
    the same paper. It was tried one level down and it does not work: Gemini reproduces the
    subject it is shown, so "a single iron-grey club" against a fox reference came back as the
    fox. The medium travels as words instead -- see `style_for`.

    What the director CAN do is name a sibling with `@stage:`, and then it conditions. That is
    the explicit version of the same idea, with the failure mode visible: ask for the wardrobe
    "in the same lacquered black as @stage:a1b2c3" and you have said which picture and why.
    Papercut gets `edit` for that case as well as for a redraw, because `edit` is the one
    conditioned mode with no continuity clause -- and a clause telling the model to reproduce
    what it is shown is precisely the fox.
    """
    found: papercut.Pictures = []
    own = board.stage_path(entry_id)
    if own.is_file():
        found.append((own, ""))
    for body in config.mention_bodies(prompt):
        if not body.startswith(config.STAGE_MENTION_PREFIX):
            continue
        named = body[len(config.STAGE_MENTION_PREFIX):]
        if named == entry_id:
            continue
        path = board.stage_path(named)
        if path.is_file() and all(path != seen for seen, _note in found):
            try:
                found.append((path, board.stage_role(board.stage_entry(named))))
            except KeyError:
                continue
    return found


def draw_text(board: board_mod.Board, entry_id: str, prompt: str,
              pictures: papercut.Pictures) -> str:
    """The frame text one sheet is drawn from: the director's prompt, with its tokens resolved.

    Nothing else. `prose=True`, because this prompt carries no `<Picture i>` tags -- that
    vocabulary is the video model's, and Papercut is handed images and a paragraph.
    """
    return config.expand_mentions(
        prompt.strip(), board.stage_mentions(pictures), prose=True
    ).strip()


def remember(board: board_mod.Board, entry_id: str, role: str, text: str, **extra) -> dict:
    """Add one line to a sheet's conversation and hand it back, so a later step can amend it.

    Returned rather than only appended for the reason `pictures.remember` is: a turn is written
    BEFORE the draw it asks for, because the panel should show the rewritten prompt while the
    sheet is being made, and what happened to that draw is only known half a minute later.
    """
    turn = {"role": role, "text": text,
            **{key: value for key, value in extra.items() if value is not None}}
    turns = board.stage_field(board.stage_entry(entry_id), "chat")
    turns.append(turn)
    board.set_stage_chat(entry_id, turns[-config.STAGE_CHAT_MEMORY:])
    return turn


def draw(board: board_mod.Board, entry_id: str, *,
         prompt: str | None = None,
         gemini_model: str | None = None,
         gemini_image_size: str | None = None,
         log: Callable[[str], None] = print,
         progress: Callable[[int, float], None] | None = None,
         announce: Callable[[], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> str:
    """Draw one staging sheet into `stage_<id>.png`. Returns the id it landed on.

    Simpler than `pictures.draw` in the one way that matters: there is no slot to allocate. A
    picture's index is read at run time rather than captured at submit because uploads are not
    serialised with the worker and `remove_ref` renumbers; a sheet's id is minted once and
    survives everything, which is the whole reason `stage_path` is keyed by it.

    So the ordering trap that method documents does not exist here either -- a draw prompt is
    stored on an entry that already exists, rather than on a slot that only exists because a file
    does, and a failed first draw keeps the typed prompt.

    The note-check lives here, not in a tool the agent could skip: one flash vision after the
    image, one rewrite-and-redraw when it fails, same `STILL_ATTEMPTS` bound the stills pass
    uses. Cheaper than the image it is checking.
    """
    entry = drawable(board, entry_id, prompt)
    text = prompt if prompt is not None else str(board.stage_field(entry, "draw"))
    if not (text or "").strip():
        raise StagingError(f"say what {board.stage_name(entry)} should look like first",
                           status=422)

    attempts = max(1, config.STILL_ATTEMPTS) if config.STILL_REVIEW else 1
    out_path = board.stage_path(entry_id)
    for attempt in range(1, attempts + 1):
        if attempt > 1 and cancelled is not None and cancelled():
            break
        entry = board.stage_entry(entry_id)
        pictures = conditioning(board, entry_id, text)
        made = papercut.draw(
            board, papercut.NO_BEAT,
            pictures=pictures,
            text=draw_text(board, entry_id, text, pictures),
            out_path=out_path,
            # True when the first conditioning image IS this sheet, which is what `conditioning`
            # puts there for a redraw. Only the fallback warning reads it; the mode itself is
            # `edit` whenever there is anything to condition on, because `edit` is the one that
            # omits the continuity clause.
            editing=bool(pictures) and pictures[0][0] == out_path,
            style=style_for(board, entry),
            aspect=aspect_for(board, entry),
            label=f"{board.stage_kind(entry)} {board.stage_name(entry)}",
            gemini_model=gemini_model,
            gemini_image_size=gemini_image_size,
            log=log,
            progress=progress,
            cancelled=cancelled,
            # Held across an edit, moved for a plain retry, for the reason `pictures.draw`
            # documents: Papercut derives a frame's seed as `scene.seed + index`, and a sheet is
            # always frame 0 of its own scene -- so two draws off the same board seed come back
            # byte-identical, which reads as a button that did nothing. A review retry must
            # move the seed too, or the rewrite is spent on the same pixels.
            seed=None if (attempt == 1 and prompt is not None) else _draw_seed(board, entry_id),
        )
        if not made:
            raise StagingError(f"{board.stage_name(entry)} did not render")

        entry["draw"] = " ".join(text.split())
        board.save()
        if announce is not None:
            announce()
        if attempt >= attempts or not config.STILL_REVIEW:
            break
        if cancelled is not None and cancelled():
            break
        try:
            verdict = review(board, entry_id)
        except gemini.GeminiError as failed:
            log(f"[staging] {board.stage_name(entry)}: not reviewed ({failed})")
            break
        if verdict.get("consistent"):
            remember(board, entry_id, "gemini", "This sheet matches its note.", verdict="pass")
            board.save()
            if announce is not None:
                announce()
            break
        problems = [str(item).strip() for item in (verdict.get("problems") or []) if str(item).strip()]
        proposed = " ".join(str(verdict.get("draw") or "").split()).strip()
        why = "; ".join(problems) or "the sheet does not match its note"
        kept, lost = config.guarded_text(text, proposed)
        if lost:
            log(f"[staging] {board.stage_name(entry)}: rewrite dropped {', '.join(lost)}; "
                "prompt left as it was")
        if not kept or kept == text.strip() or lost:
            remember(
                board, entry_id, "gemini",
                f"Kept the prompt: {why}"
                + (f" — the rewrite dropped {', '.join(lost)}" if lost else ".") ,
                verdict="fail",
            )
            board.save()
            if announce is not None:
                announce()
            log(f"[staging] {board.stage_name(entry)}: kept the prompt ({why})")
            break
        text = kept
        entry["draw"] = kept
        remember(
            board, entry_id, "gemini",
            f"Rewrote the prompt: {why}.",
            prompt=kept,
            verdict="fail",
        )
        board.save()
        if announce is not None:
            announce()
        log(f"[staging] {board.stage_name(entry)}: rewriting -- {why}")
        log(f"[staging] {board.stage_name(entry)}: rendering again from the rewritten prompt")
    return entry_id


def review(board: board_mod.Board, entry_id: str) -> dict:
    """Look at one finished sheet and say whether it is the thing its note describes.

    The sheet alone -- not next to a still. Showing the cast still here is the measured
    failure one level down: a model shown the cast draws (and then judges) the cast.
    """
    path = board.stage_path(entry_id)
    if not path.is_file():
        raise gemini.GeminiError(f"design {entry_id} has no sheet to look at")
    entry = board.stage_entry(entry_id)
    kind = board.stage_kind(entry)
    note = " ".join(str(board.stage_field(entry, "note") or "").split())
    prompt = " ".join(str(board.stage_field(entry, "draw") or "").split())
    look = board.look()
    parts = [
        f"This image is a {kind} design sheet named {board.stage_name(entry)!r}.",
        f"STYLE BIBLE: {board.identity()}",
        f"NOTE (what this design IS): {note or '(none written)'}",
        f"IT WAS DRAWN FROM: {prompt or '(none written)'}",
        JUDGEMENT.format(
            kind_rules=KIND_RULES.get(kind, KIND_RULES["character"]),
            medium=look.judge,
        ),
        "Return JSON only.",
    ]
    return gemini.structured(
        [{"role": "user", "content": "\n\n".join(parts),
          "images": [gemini.encode(path)]}],
        REVIEW_SCHEMA,
        temperature=0.1,
        model=config.VISION_MODEL,
    )


def _draw_seed(board: board_mod.Board, entry_id: str) -> int:
    """A seed this sheet has not been drawn on, without stride arithmetic to get wrong.

    `fingerprint` rather than packing fields into one integer, for the reason
    `pictures._draw_seed` gives: a stride that holds only while three separate caps stay below
    it is a constant nobody re-checks when one of them moves.
    """
    turns = len(board.stage_field(board.stage_entry(entry_id), "chat"))
    digest = board_mod.fingerprint(board.data.get("seed") or 0, entry_id, turns)
    return int(digest[:8], 16)


def system_for(board: board_mod.Board) -> str:
    """The system prompt, with this board's medium in it.

    A function rather than a constant because the sentence it opens with names the film's
    medium, and a clay reel told it is a paper-cutout studio would take that as the
    instruction it is -- this prompt's whole job is to say which half of a director's note
    the pipeline will not let them overrule."""
    look = board.look()
    return SYSTEM_TEMPLATE.format(name=look.name, essence=look.essence)


def converse(board: board_mod.Board, entry_id: str, message: str, *,
             log: Callable[[str], None] = print,
             progress: Callable[[int, float], None] | None = None,
             announce: Callable[[], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> dict:
    """One turn of the conversation about one design sheet, its redraw included.

    A structured call rather than a tool loop, for the reason `stills.converse` and
    `pictures.converse` both are: there are only two things that come out of looking at a picture
    with someone -- what it should say instead, and whether to draw it again -- so a loop would
    spend a round trip deciding to do the only thing available.

    No attachments. Here the sheet IS the subject, and a file sent with the note would have to
    become a second sheet nobody asked for; the panel is where a design is added or replaced.
    """
    try:
        entry = board.stage_entry(entry_id)
    except KeyError:
        raise StagingError(f"no design called {entry_id!r} on this reel", status=404)
    if not board.stage_path(entry_id).is_file():
        raise StagingError(
            f"{board.stage_name(entry)} has no sheet yet. Draw or upload it, then say what to "
            "change about it.",
            status=422,
        )

    before = str(board.stage_field(entry, "draw")).strip()
    verdict = gemini.structured(
        _chat_messages(board, entry, message), CHAT_SCHEMA,
        # Warmer than a review's 0.1 for the reason the other two conversations are: this is
        # writing a prompt rather than checking one, and a near-deterministic decode answers a
        # second attempt at the same note with the same words, which reads as not having listened.
        temperature=0.4,
        model=config.VISION_MODEL,
    )
    proposed = " ".join(str(verdict.get("draw") or "").split()).strip()
    reply = " ".join(str(verdict.get("reply") or "").split()).strip()
    regenerate = bool(verdict.get("regenerate"))
    kept, lost = config.guarded_text(before, proposed)
    changed = kept != before
    if lost:
        log(f"[staging] {board.stage_name(entry)}: the rewrite dropped {', '.join(lost)}; "
            "prompt left as it was")
    if changed:
        entry["draw"] = kept
        log(f"[staging] {board.stage_name(entry)}: prompt rewritten -> {kept}")

    remember(board, entry_id, "user", message)
    spoken = remember(
        board, entry_id, "gemini",
        reply or ("Rewrote the prompt." if changed else "Nothing to change."),
        prompt=kept if changed else None,
        regenerated=regenerate or None,
        error=(f"this rewrite dropped {', '.join(lost)} -- the prompt was left as it was"
               if lost else None),
    )
    # Saved and published before the draw starts, not after: the rewritten prompt is what the
    # sheet is about to be made from, and the panel should show it while that happens.
    board.save()
    if announce is not None:
        announce()
    if not regenerate:
        return {"reply": spoken["text"], "draw": kept or before, "regenerated": False}

    try:
        draw(board, entry_id, prompt=kept if changed else None,
             log=log, progress=progress, announce=announce, cancelled=cancelled)
    except (StagingError, papercut.PapercutError) as failed:
        # The prompt is already saved, and that is most of the value of the turn. Failing the
        # whole job here would throw away a rewrite the director can see is right, over an image
        # server they can start in one command and then press ✦ again.
        log(f"[staging] {board.stage_name(entry)}: {failed}")
        spoken["regenerated"] = False
        spoken["error"] = str(failed)
        board.save()
        if announce is not None:
            announce()
        return {"reply": spoken["text"], "draw": kept or before,
                "regenerated": False, "error": str(failed)}
    spoken["regenerated"] = True
    board.save()
    if announce is not None:
        announce()
    return {"reply": spoken["text"], "draw": kept or before, "regenerated": True}


def _history(board: board_mod.Board, entry: dict) -> str:
    """What has already been said about this sheet, labelled as history rather than as fact.

    Same lesson as `pictures._history`, `stills._history` and `agent.transcript`: the model
    treats its own earlier sentences as the most authoritative thing in the prompt, so an
    unlabelled transcript has it answering about the version it was describing three turns ago
    instead of the one it can see.
    """
    turns = board.stage_field(entry, "chat")[-config.STAGE_CHAT_HISTORY:]
    if not turns:
        return ""
    lines = "\n".join(
        f'{"DIRECTOR" if turn.get("role") == "user" else "YOU"}: {turn.get("text", "")}'
        for turn in turns
    )
    return (
        "WHAT HAS ALREADY BEEN SAID ABOUT THIS DESIGN -- history only. Some of it describes a "
        "version that has since been drawn again. Never answer a question about what the sheet "
        "looks like from here; look at the image.\n"
        f"{lines}"
    )


def _chat_messages(board: board_mod.Board, entry: dict, message: str) -> list[dict]:
    """The prompt for one turn: the sheet, what it is for, the history, then the note.

    Shown: this sheet and nothing else. Not the reel's other designs and not the cast reference,
    which is where this differs from `pictures._chat_messages` -- there, the cast is included
    because a beat's picture belongs to a film whose look is already fixed. Here the sheets ARE
    what fixes it, so handing the model a second one gives it something to drift the subject
    towards for no question it was asked. A sibling named with `@stage:` still expands to that
    sibling's role text, which is words rather than an image and is enough to answer "the same
    black as the wardrobe".

    The director's note goes LAST, after everything it might be about, for the reason `agent.turn`
    puts the board after the transcript -- whatever sits nearest the question is what gets
    answered.
    """
    entry_id = str(entry.get("id"))
    kind = board.stage_kind(entry)
    parts: list[str] = [
        "You are given one image: the design sheet you are talking about. Everything the "
        "director says is about this one, and the prompt you write describes this one."
    ]
    parts.append(
        f"WHAT IT IS: {board.stage_name(entry)}, "
        + {
            config.STAGE_CHARACTER: "a character in this film.",
            config.STAGE_ENVIRONMENT: "a set in this film. It is drawn empty -- no characters, "
                                      "no people, no animals anywhere in it.",
            config.STAGE_PROP: "a prop in this film.",
        }[kind]
    )
    note = " ".join(str(board.stage_field(entry, "note")).split()).strip()
    if note:
        parts.append(
            "WHAT EVERY SHOT IN THE FILM IS TOLD ABOUT IT, in the director's words -- this is "
            f"not yours to rewrite: {note}"
        )
    drawn = str(board.stage_field(entry, "draw")).strip()
    parts.append(
        f"THE PROMPT IT WAS LAST DRAWN FROM: {drawn}" if drawn
        else "This sheet was uploaded rather than drawn, so it has no prompt yet. Write the one "
             "that would produce what you can see, with the director's change applied."
    )

    history = _history(board, entry)
    if history:
        parts.append(history)
    # Labelled as the film's look rather than handed over bare, because bare it reads as a
    # description of the thing being drawn -- and a bible saying "a single fox on green hills"
    # then comes back as the prompt for a sheet that was of a club.
    identity = board.identity()
    if identity:
        parts.append(
            "WHAT THIS FILM LOOKS LIKE, for the materials and the palette only. This is NOT a "
            "description of the design you are working on, and none of it belongs in your prompt "
            f"unless the design is already of it: {identity}"
        )
    parts.append(f"REQUIRED OF EVERY {kind.upper()} SHEET: {style_for(board, entry)}")
    # Both field names spelled out, because given only the schema the model filled `reply` with
    # the prompt -- reading the object as a form to copy its input into. Same fix as
    # `agent._revise_messages` and `pictures._chat_messages`.
    parts.append(f"THE DIRECTOR SAYS: {message}")
    parts.append(
        "Answer with the rewritten prompt in `draw`, whether to draw it again in `regenerate`, "
        "and what you did in `reply`. `reply` is for the director; never put the prompt in it."
    )
    return [
        {"role": "system", "content": system_for(board)},
        {"role": "user", "content": "\n\n".join(parts),
         "images": [gemini.encode(board.stage_path(entry_id))]},
    ]
