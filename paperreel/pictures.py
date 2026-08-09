"""Reference pictures the studio draws, rather than only receives.

A beat's reference pictures used to be uploads and nothing else: a file, a one-line note saying
what it was for, and a delete button. Gemini is next door through the image server, so they are now the same
kind of thing the beat's own still is -- drawn from a prompt, talked about, redrawn.

    index = pictures.draw(board, 3, prompt="a close-up of an iron-grey club, side on")
    pictures.converse(board, 3, index, "make it longer and more battered")

**This is not `stills.py` and must not become it.** That module's whole substance is the review:
holding a generated still against the reel's locked cast reference and rejecting it when the
puppet has drifted. None of that transfers here, and running it would be actively wrong. A
reference picture is *supposed* to differ from the cast -- it is a prop sheet, a set with nobody
in it, a costume detail, a colour chart -- so a reviewer told to reject anything that does not
match the cast would reject almost every one. That is not a hypothetical: it is the failure
recorded in `stills.JUDGEMENT`, one step further out. **There is no review pass here, ever.**

What a picture is drawn from is `conditioning`: itself when it already exists, then the reel's
cast reference. Papercut is asked for `consistency="edit"`, the mode that omits its continuity
clause -- because that clause ends "but move the subject into a clearly different pose and
position", which is right for the next frame of a moving sequence and exactly wrong when the
reference IS the picture being changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import board as board_mod
from . import config, gemini, papercut

# One turn of the conversation about one picture. The same three outcomes as a still's, because
# there are the same three things that can come out of looking at a picture with someone.
#
# Declared in this order because the decode follows schema-property order, so `reply` is written
# LAST -- after the model has committed to a prompt and to whether it is drawing. The other way
# round it announces a change it then does not make, which is the lesson `stills.CHAT_SCHEMA` and
# `agent.REVISE_SCHEMA` both record.
#
# `ref_prompts` is deliberately NOT a fourth field. The role sentence is written for the video
# model -- it becomes "<Picture 3> is ..." -- and a conversation about how a picture LOOKS must
# not quietly rewrite what the clip is told the picture is FOR. It stays hand-editable.
DRAW_CHAT_SCHEMA = {
    "type": "object",
    "required": ["ref_draw", "regenerate", "reply"],
    "properties": {
        "ref_draw": {
            "type": "string",
            "description": (
                "The prompt this picture is drawn from next time, rewritten to do what the "
                "director asked and nothing else. Return the current prompt unchanged when "
                "nothing about it should change."
            ),
        },
        "regenerate": {
            "type": "boolean",
            "description": (
                "true to draw the picture again now. Only false when the answer is words alone "
                "-- a question about it, or a note to keep for later."
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

DRAW_SYSTEM = (
    "You are the reference-picture editor for a paper-cutout stop-motion Instagram Reel studio. "
    "You are looking at ONE reference picture with the director, and your job is to turn what "
    "they say about it into the prompt it is drawn from next time.\n\n"
    "A reference picture is not a shot. It is a design sheet the video model is shown so that a "
    "prop, a costume or a set reads the same way in every scene that uses it: the subject "
    "complete and centred on a plain ground, nothing cropped, no scenery, no staging, no implied "
    "camera. If the director asks for a composition, they are asking for the wrong thing here "
    "and you should say so in your reply while still doing what they asked to the subject.\n\n"
    "WHAT THIS PICTURE IS OF DOES NOT CHANGE. The prompt you write describes the SAME subject "
    "the current prompt describes, with the director's note applied to it. If it is a club, it "
    "stays a club. You are shown the reel's cast reference alongside it, and that image is there "
    "for the paper, the palette and the light ONLY -- it is not what this picture is of, and "
    "nothing in it belongs in your prompt unless it is already there. Replacing the subject with "
    "what the cast reference shows is the one mistake that makes this picture useless.\n\n"
    "The director is the authority on this picture. What is not theirs to overrule is the medium "
    "-- layered paper cutout, visible paper grain, soft contact shadows -- because every other "
    "image in the reel is made of it.\n\n"
    "Rewrite the WHOLE prompt every time, carrying over every part the director did not ask you "
    "to change. Drawing it again is a new Gemini request, so ask for it "
    "whenever the picture itself should change. Rendering the VIDEO is not something you can do; "
    "it costs real money and only the director starts it.\n\n"
    + config.MENTION_NOTE
)


class PicturesError(RuntimeError):
    """A picture job that must not run. `status` is the HTTP code the API answers with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def drawable(board: board_mod.Board, n: int, index: int | None = None,
             draw_prompt: str | None = None) -> None:
    """May this picture be drawn at all? Raises with the reason if not.

    Mirrors `stills.discussable` in shape and in most of its refusals, because the reasons an
    image must not be generated are properties of the board rather than of the request -- and
    there are two ways in, the canvas and (eventually) the agent.

    The join guard is the one that is specific to a picture. Both `pictures_for` and
    `still_pictures` are gated on `uses_refs`, so a picture on a chained or keyframe beat
    conditions neither render: drawing into one is work nobody will ever see. It refuses rather
    than silently moving the join, because moving a join underneath a redraw is a different act
    from the one the director asked for. Adding a picture is where the join legitimately moves,
    and the route that does it says so first.
    """
    if board.data.get("manual_stills") and index is None:
        raise PicturesError(
            "this reel supplies its own pictures, so new image generation is off. Upload the "
            "picture first, or switch the stills back to generated on the script node.",
        )
    if not any(b["n"] == n for b in board.beats):
        raise PicturesError(f"no such beat: {n}", status=404)
    source = board.source_for(board.beat(n))
    if not board_mod.uses_refs(source):
        raise PicturesError(
            f"scene {n} is on the {source} join, so its reference pictures reach neither the "
            "still nor the clip. Move it onto the reference join first, and the picture becomes "
            "something the render can see.",
        )
    count = len(board.ref_paths(n))
    if index is None:
        if board.next_ref_index(n) is None:
            raise PicturesError(
                f"scene {n} is at {config.MAX_REF_IMAGES} pictures, which is the model's limit "
                "-- and two of those slots are this scene's own still and the reel's cast "
                "reference. Remove one first.",
            )
        return
    if not 1 <= index <= count:
        raise PicturesError(f"scene {n} has no reference picture {index}", status=404)
    if not board.ref_draws(n)[index - 1].strip() and not (draw_prompt or "").strip():
        # Reached by a picture that was uploaded, or minted by a still-chat attachment: there is
        # a file but nobody ever said what it should be. Redrawing it from nothing would replace
        # the director's own image with an invention.
        raise PicturesError(
            f"picture {index} of scene {n} was uploaded, not drawn, so there is no prompt to "
            "draw it from. Say what it should be first.",
            status=422,
        )


def conditioning(board: board_mod.Board, n: int, index: int | None) -> papercut.Pictures:
    """What a picture is drawn FROM: itself first, then the beat's visual context.

    New drawings use the existing cast, opening still and beat uploads as visual context. A
    redraw puts the picture being edited first, then the same context with that file removed.
    This gives Nano Banana the thing to preserve and the other images that explain the paper,
    palette, cast or prop, without requiring the director to upload the same context again.
    """
    context: papercut.Pictures = []
    asset = board.asset_path(n)
    if asset.is_file():
        context.append((asset, ""))
    context.extend(board.still_pictures(n, config.MAX_REF_IMAGES))
    if index is None:
        return context[:config.MAX_REF_IMAGES]
    current = board.ref_path(n, index)
    if not current.is_file():
        return context[:config.MAX_REF_IMAGES]
    return [(current, "")] + [picture for picture in context if picture[0] != current][
        :max(0, config.MAX_REF_IMAGES - 1)
    ]


def draw_text(board: board_mod.Board, n: int, index: int | None, prompt: str) -> str:
    """The frame text one picture is drawn from: the director's prompt, with its tokens resolved.

    Nothing else. The medium rides on the scene's `style` instead -- `papercut.draw` sends
    `REF_DRAW_STYLE_SUFFIX` there, which is where a still's suffix goes too, so the two are
    assembled the same way rather than one of them being special.

    **The board's style bible must not reach this render, and that is the whole reason `style` is
    overridable at all.** It describes the cast and the set -- on a real board it reads "A single
    fox cut from warm orange cardstock with a cream chest, on layered green paper hills" -- and a
    still is supposed to contain those. A prop sheet is not. Left on the default, Gemini was
    effectively handed "a single iron-grey club. A single fox ... on layered green paper hills."
    and drew the fox, which is exactly what it was asked for. Measured on a live render.
    """
    pictures = conditioning(board, n, index)
    return config.expand_mentions(
        prompt.strip(), board.mentions(n, pictures), prose=True
    ).strip()


def remember(board: board_mod.Board, n: int, index: int, role: str, text: str, **extra) -> dict:
    """Add one line to a picture's conversation and hand it back, so a later step can amend it.

    Returned rather than only appended for the same reason `stills.remember` is: a turn is
    written BEFORE the draw it asks for, because the node should show the rewritten prompt while
    the picture is being made, and what happened to that draw is only known half a minute later.

    Trimmed to `config.REF_CHAT_MEMORY`, which is far shorter than a still's. A beat has one
    still and up to nine pictures, and `to_json` serialises the whole board on every refetch --
    so this grows in two dimensions where the still's transcript grows in one.
    """
    turn = {"role": role, "text": text,
            **{key: value for key, value in extra.items() if value is not None}}
    chats = board.ref_chats(n)
    if not 1 <= index <= len(chats):
        raise PicturesError(f"scene {n} has no reference picture {index}", status=404)
    chats[index - 1].append(turn)
    del chats[index - 1][:-config.REF_CHAT_MEMORY]
    board.store_ref_chats(n, chats)
    return turn


def draw(board: board_mod.Board, n: int, index: int | None = None, *,
         prompt: str | None = None,
         gemini_model: str | None = None,
         gemini_image_size: str | None = None,
         log: Callable[[str], None] = print,
         progress: Callable[[int, float], None] | None = None,
         announce: Callable[[], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> int:
    """Draw one of beat `n`'s reference pictures. Returns the slot it landed in.

    `index` None means a new picture, in whatever slot `next_ref_index` offers. That is read
    HERE rather than captured when the job was queued: the worker is serial but uploads are not,
    so a picture dropped on the tray between the click and the run would otherwise be drawn on
    top of.

    The order below is load-bearing for a NEW picture -- file first, prompt second. `_ref_slots`
    sizes every per-picture list off `ref_paths`, so a draw prompt stored for a slot with no file
    on disk is trimmed straight back to nothing: a text box that silently forgets what you typed,
    which is the sharpest piece of dead state this feature could have had.

    The cost is that a failed first draw loses the typed prompt. It is in the job's detail and in
    the log, and the alternative -- creating the slot with a placeholder image -- would put a
    blank picture where `pictures_for` could pick it up and a render could pay for it.
    """
    drawable(board, n, index, prompt)
    slot = index if index is not None else board.next_ref_index(n)
    if slot is None:
        raise PicturesError(
            f"scene {n} filled its last picture slot while this was queued. Remove one and try "
            "again.",
        )
    text = prompt if prompt is not None else board.ref_draws(n)[slot - 1]
    if not (text or "").strip():
        raise PicturesError(f"say what picture {slot} of scene {n} should be first", status=422)

    # An existing picture is an edit; a new picture may still have contextual references, but it
    # has no subject to hold. Papercut uses edit mode for that context-only case so the continuity
    # clause does not turn a prop sheet into a copy of the cast reference.
    editing = index is not None and board.ref_path(n, index).is_file()
    made = papercut.draw(
        board, n,
        pictures=conditioning(board, n, index),
        text=draw_text(board, n, index, text),
        out_path=board.ref_path(n, slot),
        editing=editing,
        gemini_model=gemini_model or board.beat(n).get("gemini_model"),
        gemini_image_size=gemini_image_size or board.beat(n).get("gemini_image_size"),
        log=log,
        # `_render_scene` already calls this with the beat number, since the run is [n].
        progress=progress,
        cancelled=cancelled,
        # Held across an edit, moved for a plain retry, for the reason `_scene_body` documents:
        # Papercut derives a frame's seed as `scene.seed + index`, and a picture is always frame
        # 0 of its own scene -- so two draws off the same board seed come back byte-identical.
        seed=None if prompt is not None else _draw_seed(board, n, slot),
    )
    if not made:
        raise PicturesError(f"picture {slot} of scene {n} did not render")

    board.set_ref_draw(n, slot, text)
    board.save()
    if announce is not None:
        announce()
    return slot


def _draw_seed(board: board_mod.Board, n: int, index: int) -> int:
    """A seed this picture has not been drawn on, without stride arithmetic to get wrong.

    `stills._retry_seed` packs the beat number and a turn count into one integer with a stride of
    1000, which works only while `ASSET_CHAT_MEMORY` stays under it. A picture needs a third
    field, and a stride that holds only while three separate caps stay below it is a constant
    nobody will re-check when one of them moves. `fingerprint` is already this board's answer to
    "identify these inputs", so use it.
    """
    turns = len(board.ref_chats(n)[index - 1]) if index <= len(board.ref_paths(n)) else 0
    digest = board_mod.fingerprint(board.data.get("seed") or 0, n, index, turns)
    return int(digest[:8], 16)


def converse(board: board_mod.Board, n: int, index: int, message: str, *,
             log: Callable[[str], None] = print,
             progress: Callable[[int, float], None] | None = None,
             announce: Callable[[], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> dict:
    """One turn of the conversation about one reference picture, its redraw included.

    A structured call rather than a tool loop, for the same reason `stills.converse` is: there
    are only two things that come out of looking at a picture with someone -- what it should say
    instead, and whether to draw it again -- so a loop would spend a round trip deciding to do
    the only thing available.

    No attachments, unlike the still's conversation. There, a picture sent with a note means
    "here is what I mean" and is stored because `Board.still_pictures` reads the beat. Here the
    picture IS the subject, and storing an attachment would mint a tenth reference nobody asked
    for -- the tray is where a picture is added.
    """
    if not any(b["n"] == n for b in board.beats):
        raise PicturesError(f"no such beat: {n}", status=404)
    if not 1 <= index <= len(board.ref_paths(n)):
        raise PicturesError(f"scene {n} has no reference picture {index}", status=404)
    if not board.ref_path(n, index).is_file():
        raise PicturesError(
            f"picture {index} of scene {n} is not on disk yet. Draw or upload it, then say what "
            "to change about it.",
            status=422,
        )

    before = board.ref_draws(n)[index - 1].strip()
    verdict = gemini.structured(
        _chat_messages(board, n, index, message), DRAW_CHAT_SCHEMA,
        # Warmer than a review's 0.1 for the reason `stills.converse` gives: this is writing a
        # prompt rather than checking one, and a near-deterministic decode answers a second
        # attempt at the same note with the same words, which reads as not having listened.
        temperature=0.4,
        model=config.VISION_MODEL,
    )
    corrected = " ".join(str(verdict.get("ref_draw") or "").split()).strip()
    reply = " ".join(str(verdict.get("reply") or "").split()).strip()
    regenerate = bool(verdict.get("regenerate"))
    changed = bool(corrected) and corrected != before

    lost = config.lost_mentions(before, corrected) if changed else []
    if lost:
        # Cannot be repaired -- only the model knows where in the new sentence it meant them --
        # but a token silently dropped is a picture the render stops being told about, so it
        # goes in the transcript the director already reads to find out why a picture changed.
        log(f"[picture] beat {n} picture {index}: the rewrite dropped {', '.join(lost)}")

    if changed:
        board.set_ref_draw(n, index, corrected)
        log(f"[picture] beat {n} picture {index}: prompt rewritten -> {corrected}")

    remember(board, n, index, "user", message)
    spoken = remember(
        board, n, index, "gemini",
        reply or ("Rewrote the prompt." if changed else "Nothing to change."),
        prompt=corrected if changed else None,
        regenerated=regenerate or None,
        error=(f"this rewrite dropped {', '.join(lost)} -- put it back if it mattered"
               if lost else None),
    )
    # Saved and published before the draw starts, not after: the rewritten prompt is what the
    # picture is about to be made from, and the node should show it while that happens.
    board.save()
    if announce is not None:
        announce()
    if not regenerate:
        return {"reply": spoken["text"], "ref_draw": corrected or before, "regenerated": False}

    try:
        draw(board, n, index, prompt=corrected if changed else None,
             log=log, progress=progress, announce=announce, cancelled=cancelled)
    except (PicturesError, papercut.PapercutError) as failed:
        # The prompt is already saved, and that is most of the value of the turn. Failing the
        # whole job here would throw away a rewrite the director can see is right, over an image
        # server they can start in one command and then press ✦ again.
        log(f"[picture] beat {n} picture {index}: {failed}")
        spoken["regenerated"] = False
        spoken["error"] = str(failed)
        board.save()
        if announce is not None:
            announce()
        return {"reply": spoken["text"], "ref_draw": corrected or before,
                "regenerated": False, "error": str(failed)}
    spoken["regenerated"] = True
    board.save()
    if announce is not None:
        announce()
    return {"reply": spoken["text"], "ref_draw": corrected or before, "regenerated": True}


def _history(board: board_mod.Board, n: int, index: int) -> str:
    """What has already been said about this picture, labelled as history rather than as fact.

    Same lesson as `stills._history` and `agent.transcript`: the model treats its own earlier
    sentences as the most authoritative thing in the prompt, so an unlabelled transcript has it
    answering about the version it was describing three turns ago instead of the one it can see.
    """
    turns = (board.ref_chats(n)[index - 1])[-config.REF_CHAT_HISTORY:]
    if not turns:
        return ""
    lines = "\n".join(
        f'{"DIRECTOR" if turn.get("role") == "user" else "YOU"}: {turn.get("text", "")}'
        for turn in turns
    )
    return (
        "WHAT HAS ALREADY BEEN SAID ABOUT THIS PICTURE -- history only. Some of it describes a "
        "version that has since been drawn again. Never answer a question about what the picture "
        "looks like from here; look at the image.\n"
        f"{lines}"
    )


def _chat_messages(board: board_mod.Board, n: int, index: int, message: str) -> list[dict]:
    """The prompt for one turn: the pictures, what this one is for, the history, then the note.

    Shown: the reel's cast reference when there is one, and the picture itself, last. NOT the
    beat's other pictures and NOT its still. Vision tokens are wall clock, and the question is
    about one picture -- a mention of a sibling expands to that sibling's role text, which is
    words rather than an image and is enough to answer "and put the club from picture 2 in it".

    Numbered `1. 2.` rather than tagged, exactly as `stills._chat_messages` is: `<Picture i>` is
    the VIDEO model's vocabulary, and asked in it the reviewer answered about only one of the two
    images it had been given.

    The director's note goes LAST, after everything it might be about, for the reason
    `agent.turn` puts the board after the transcript -- whatever sits nearest the question is
    what gets answered.
    """
    beat = board.beat(n)
    cast = board.reference_path()
    own = board.ref_path(n, index)
    images: list[Path] = ([cast] if cast is not None and cast != own else []) + [own]
    listed = []
    if len(images) > 1:
        listed.append(
            "1. this reel's locked cast reference. It is here for the paper, the palette and the "
            "light, and for NOTHING else. It is not this picture's subject and it is not what "
            "this picture should become. Do not describe anything you can see in it."
        )
    listed.append(
        f"{len(images)}. THE REFERENCE PICTURE YOU ARE TALKING ABOUT. Everything the director "
        "says is about this one, and the prompt you write describes this one."
    )
    parts: list[str] = [
        (f"You are given {len(images)} images, in this order:\n" if len(images) > 1
         else "You are given one image:\n") + "\n".join(listed)
    ]

    role = board.ref_prompts(n)[index - 1].strip()
    if role:
        parts.append(
            "WHAT THIS PICTURE IS FOR, in the director's words -- this is what the video model "
            f"is told about it, and it is not yours to rewrite: {role}"
        )
    else:
        parts.append(
            "Nobody has said what this picture is for yet. Draw what is asked for; the director "
            "writes the note that goes with it."
        )
    drawn = board.ref_draws(n)[index - 1].strip()
    parts.append(
        f"THE PROMPT IT WAS LAST DRAWN FROM: {drawn}" if drawn
        else "This picture was uploaded rather than drawn, so it has no prompt yet. Write the "
             "one that would produce what you can see, with the director's change applied."
    )

    history = _history(board, n, index)
    if history:
        parts.append(history)
    # Labelled as the cast's description rather than handed over bare, because bare it reads as
    # a description of the thing being drawn -- and a bible saying "a single fox on green hills"
    # then comes back as the prompt for a picture that was of a club.
    parts.append(
        "WHAT THE REEL'S CAST AND SET LOOK LIKE, for the materials and the palette only. This "
        "is NOT a description of the picture you are working on, and none of it belongs in your "
        f"prompt unless the picture is already of it: {board.identity()}"
    )
    parts.append(f"REQUIRED OF EVERY REFERENCE PICTURE: {config.REF_DRAW_STYLE_SUFFIX}")
    if beat.get("scene"):
        parts.append(f"THE SHOT THIS PICTURE IS USED IN: {beat['scene']}")
    parts.append(f"THE DIRECTOR SAYS: {message}")
    # Both field names spelled out, because given only the schema the model filled `reply` with
    # the prompt -- reading the object as a form to copy its input into. Same fix as
    # `agent._revise_messages`.
    parts.append(
        "Answer with the rewritten prompt in `ref_draw`, whether to draw it again in "
        "`regenerate`, and what you did in `reply`. `reply` is for the director; never put the "
        "prompt in it."
    )
    return [
        {"role": "system", "content": DRAW_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts),
         "images": [gemini.encode(path) for path in images]},
    ]
