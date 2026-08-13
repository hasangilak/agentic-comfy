"""Looking at a finished still through one named lens, and saying what to do about it.

`stills.review` already looks at every still as it is rendered, and it asks one question: is
this the same cast, in the same medium, as the rest of the reel? That question is worth asking
automatically and it is the only one that can be, because it is the only one with an answer the
board already holds -- the cast reference is on disk.

The other three questions a director asks of a still have no such answer:

    style      is this actually the material this film is made of, made the way that
               material is made? Not "is it the same as the reference" but "is it right".
    blocking   is what is in this frame what the beat said would be in it, standing where
               the beat said it stands?
    story      is this the moment the script asked for, or an adjacent one?

Each is a different lens on the same picture, and the whole reason to ask them separately is
that they fail separately: a still can be flawless clay and the wrong moment, or the right
moment blocked backwards. A single reviewer asked all three at once answers about whichever
one it noticed first.

**Nothing here re-renders and nothing here edits the board's prompts.** A verdict is a
verdict plus a concrete suggested fix, written into the beat's own transcript next to the
automatic review's, and the director decides. That bound is deliberate and it is a money bound
as much as a design one: three lenses that could each reject and re-render would turn one
disagreeing panel into a run that spends its whole still budget on beat 3. Three vision calls
per still, once, is what this costs.

A lens is a structured call rather than a tool loop, for the reason `stills.converse`,
`pictures.converse` and `staging.converse` all are: there is exactly one shape of answer, so a
loop would spend a round trip deciding to produce it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import board as board_mod
from . import config, llm as llm_mod

VERDICT_SCHEMA = {
    "type": "object",
    "required": ["verdict", "problem", "fix"],
    "properties": {
        # First, and that ordering is `planner.REVIEW_SCHEMA`'s lesson: the decode follows
        # schema-property order, and a field declared before the judgement becomes the model's
        # scratchpad. The judgement is one word, so it commits before anything explains itself.
        "verdict": {
            "type": "string",
            "enum": ["pass", "fail"],
            "description": (
                "pass if the still is right on YOUR lens and yours only. fail if there is a "
                "problem you can name and point at."
            ),
        },
        "problem": {
            "type": "string",
            "description": (
                "What is wrong, in one sentence, naming what you can see. Empty on a pass -- do "
                "not invent a reservation to fill it."
            ),
        },
        "fix": {
            "type": "string",
            "description": (
                "What the director should change to fix it: the words to add to the prompt, the "
                "thing to move, the moment to render instead. Concrete enough to act on without "
                "looking at the picture again. Empty on a pass."
            ),
        },
    },
}


# One lens each. The name is what the verdict is filed under in the transcript, so it is short
# and it is the same word the director sees on the node.
LENSES: dict[str, dict] = {
    "style": {
        "role": "the style artist",
        "asks": (
            "Is this still actually made of this film's material, made the way that material is "
            "made? Judge the craft, not the story: the surface, the edges, the light, the "
            "construction, whether a real person could have built and photographed this. A "
            "still that looks like a digital illustration OF the medium rather than a "
            "photograph of the real thing is the failure you are here to catch."
        ),
    },
    "blocking": {
        "role": "the mise-en-scene artist",
        "asks": (
            "Is what is in this frame what the beat said would be in it, standing where the "
            "beat said it stands -- AND is the reel's bound cast still the subject of this "
            "frame, not a different film? Judge the staging: what the set holds, where each "
            "thing sits, which way it faces, how much room is above and around it, what is "
            "missing and what is there that nobody asked for. A still of one bird on a flock "
            "film fails unless blocking names it as a member of that flock and the rest are "
            "accounted for (bound, or named off-frame). Same for a set that vanished. Do not "
            "pass a dropped cast just because this beat's own text already said 'a single'."
        ),
    },
    "story": {
        "role": "the story editor",
        "asks": (
            "Is this the moment the script asked for, and is it still the same film? Judge "
            "the beat: whether this is the instant the scene and action describe rather than "
            "one just before or just after it -- and whether a viewer who saw the earlier "
            "beats would still recognise the same subjects. A flock that has become one bird "
            "is a different story, not a closer camera. A close-up of one member of a group "
            "already in the film is coverage; a replacement protagonist is not."
        ),
    },
}

SYSTEM = (
    "You are {role} on a {name} Instagram Reel, looking at one finished still.\n\n"
    "{asks}\n\n"
    "You are ONE of several people looking at this picture, each at a different thing, and the "
    "others are covering theirs. Anything outside your lens is not your verdict to give -- say "
    "nothing about it, and do not fail a still for it.\n\n"
    "You do not change anything and you do not render anything. You report, and the director "
    "decides. So a `fix` that says 'render it again' is worthless: say what should be "
    "DIFFERENT about it, specifically enough that someone could act on your sentence without "
    "looking at the picture.\n\n"
    "Pass a still that is right. This pass exists to catch real problems, and a reviewer that "
    "finds something wrong with everything is a reviewer nobody reads."
)


class InspectError(RuntimeError):
    """A still that cannot be looked at. `status` is the HTTP code the API answers with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def lenses() -> list[str]:
    return list(LENSES)


def _sheet_label(board: board_mod.Board, entry: dict) -> str:
    kind = board.stage_kind(entry)
    name = board.stage_name(entry)
    if kind == config.STAGE_ENVIRONMENT:
        return f"{name}, a set sheet -- the place empty of cast, a place lock not a story pose."
    return f"{name}, a character sheet -- identity lock, not a story pose."


def shot_pictures(board: board_mod.Board, n: int) -> list[tuple[Path, str]]:
    """Pictures the blocking lens looks at for beat `n`, numbered in this order.

    Bound design sheets first (character AND environment -- the still renderer drops sets to
    words; the auditor must not), then the panel if it is on disk, then the still last.

    Never the reel's locked cast reference (beat 1's composed still). That picture turns
    every verdict into "does it match", which is `stills.review`'s job. Identity-lock sheets
    are a different picture: they answer whether this frame holds the designed puppet
    standing where blocking said.
    """
    found: list[tuple[Path, str]] = []
    for entry in board.bound_staging(n):
        path = board.stage_path(str(entry.get("id")))
        if path.is_file():
            found.append((path, _sheet_label(board, entry)))
    # A beat that never bound anything is the flock failure: inspect would otherwise look
    # at the still alone and pass a one-bird frame against a sentence. Fall back to every
    # drawn sheet so the roster is still in the frame.
    if not found:
        for entry in board.staging:
            path = board.stage_path(str(entry.get("id")))
            if path.is_file():
                found.append((path, _sheet_label(board, entry)))
    panel = board.panel_path(n)
    if panel.is_file():
        found.append((
            panel,
            f"beat {n}'s storyboard panel -- a sketch of the shot, not the film's medium.",
        ))
    still = board.asset_path(n)
    if still.is_file():
        found.append((still, "the still you are judging."))
    return found


def context_pictures(board: board_mod.Board | None,
                     phase: str | None) -> list[tuple[Path, str]]:
    """The mise agent's opening-turn pack: sheets, and on lock the panels too.

    Capped at `MAX_STILL_REFS` so a 12-round tool loop does not re-send every still of the
    reel every round. Per-beat stills stay on `inspect_still`. Extract attaches nothing
    -- nothing has been drawn yet. Sheets are attached on seams (block against the
    puppets) and lock (panels plus sheets).
    """
    if board is None or phase in (None, "extract", "script", "panels", "sheets", "stills"):
        return []
    found = [
        (board.stage_path(str(entry.get("id"))), _sheet_label(board, entry))
        for entry in board.staging
        if board.stage_path(str(entry.get("id"))).is_file()
    ]
    if phase == "lock":
        found += [
            (board.panel_path(beat["n"]),
             f"beat {beat['n']}'s storyboard panel -- a sketch of the shot, "
             "not the film's medium.")
            for beat in board.ordered_beats()
            if board.panel_path(beat["n"]).is_file()
        ]
    return found[:config.MAX_STILL_REFS]


def picture_legend(pictures: list[tuple[Path, str]]) -> str:
    """Numbered like `stills._chat_messages`, never `<Picture N>`."""
    if not pictures:
        return ""
    listed = [f"{index}. {label}" for index, (_path, label) in enumerate(pictures, start=1)]
    header = (f"You are given {len(pictures)} images, in this order:\n"
              if len(pictures) > 1 else "You are given one image:\n")
    return header + "\n".join(listed)


def look_parts(board: board_mod.Board, n: int, lens: str) -> tuple[list[str], list[Path]]:
    """The blocking/style/story vision prompt, without encoding. For `look` and dry-run."""
    if lens not in LENSES:
        raise InspectError(f"no lens called {lens!r}. Lenses: {', '.join(LENSES)}.", status=404)
    beat = board.beat(n)
    if beat is None:
        raise InspectError(f"there is no beat {n} on this board", status=404)
    still = board.asset_path(n)
    if not still.is_file():
        raise InspectError(f"beat {n} has no still to look at yet", status=422)

    look_at = board.look()
    parts = [f"STYLE BIBLE: {board.identity()}"]
    if lens == "style":
        # The lens that judges the craft is the one that has to be told what the craft IS, and
        # in the words the render was asked in -- otherwise it judges against its own idea of
        # clay, which is not the idea the still was made from.
        parts.append(f"WHAT THIS FILM IS MADE OF: {look_at.judge}.")
        parts.append(f"REQUIRED OF EVERY STILL: {look_at.still}")
    pictures: list[tuple[Path, str]]
    if lens == "blocking":
        blocking = str(beat.get("blocking") or "").strip()
        parts.append(f"WHERE THINGS STAND IN THIS SHOT: {blocking}" if blocking else
                     "NOBODY HAS WRITTEN THE BLOCKING FOR THIS SHOT. Judge it against the scene "
                     "line and the still's own prompt, and say in `fix` what the blocking "
                     "should say.")
        staging = board.staging_text(n, [])
        if staging:
            parts.append(f"THE DESIGNS THIS SHOT CONTAINS: {staging}")
        roster = [f"{board.stage_name(entry)} ({board.stage_kind(entry)})"
                  for entry in board.staging]
        if roster:
            parts.append("THE REEL'S CAST AND SETS, designed once: " + "; ".join(roster))
        pictures = shot_pictures(board, n)
        legend = picture_legend(pictures)
        parts.append(legend if legend else "The still is below.")
    else:
        pictures = [(still, "the still you are judging.")]
        parts.append("The still is below.")
    if lens in ("blocking", "story"):
        earlier = [
            f"beat {other['n']}: {(other.get('action') or '')[:160]}"
            for other in board.ordered_beats() if other["n"] < n
        ]
        if earlier:
            parts.append(
                "EARLIER BEATS, for who the film is about:\n" + "\n".join(earlier)
            )
    parts.append(f"THE SHOT: {beat.get('scene', '')}")
    parts.append(f"WHAT MOVES ONCE THE CLIP STARTS: {beat.get('action', '')}")
    if beat.get("asset_prompt"):
        parts.append(f"THIS STILL WAS ASKED FOR AS: {beat['asset_prompt']}")
    return parts, [path for path, _label in pictures]


def look(board: board_mod.Board, n: int, lens: str, *,
         llm: llm_mod.LLM | None = None,
         log: Callable[[str], None] = print) -> dict:
    """One lens, one still, one verdict. Renders nothing and writes nothing to the board.

    Style and story stills go in on their own with no cast reference beside them, which is
    the deliberate difference from `stills.review`. That pass compares two pictures and asks
    whether they are the same production; those two lenses ask whether ONE picture is right,
    and a composed reference in the frame would drag every answer back towards "does it
    match".

    Blocking is the exception: it encodes the bound design sheets and the panel next to the
    still. Those are identity/place locks, not the reel's locked cast still -- so the
    question stays "is this the designed puppet standing where blocking said", not "does it
    match beat 1".
    """
    parts, paths = look_parts(board, n, lens)
    look_at = board.look()
    system = SYSTEM.format(role=LENSES[lens]["role"], name=look_at.name,
                           asks=LENSES[lens]["asks"])
    # Through the provider the agent is running on, not through `gemini` directly. This is the
    # one call in the crew that looks at a picture, and reaching past the protocol for it would
    # mean a second provider could drive every agent and still send its vision calls to Gemini.
    speaker = llm or llm_mod.provider()
    verdict = speaker.structured(
        [{"role": "system", "content": system},
         {"role": "user", "content": "\n\n".join(parts),
          "images": [speaker.encode(path) for path in paths]}],
        VERDICT_SCHEMA, model=config.VISION_MODEL)

    said = str(verdict.get("verdict") or "").strip().lower()
    passed = said != "fail"
    problem = " ".join(str(verdict.get("problem") or "").split())
    fix = " ".join(str(verdict.get("fix") or "").split())
    log(f"[{lens}] beat {n}: {'pass' if passed else 'FAIL'}"
        + (f" -- {problem}" if problem and not passed else ""))
    return {"lens": lens, "beat": n, "passed": passed, "problem": problem, "fix": fix}


def record(board: board_mod.Board, n: int, verdict: dict) -> dict:
    """File a verdict in the beat's own transcript, beside the automatic review's.

    `stills.remember` is what writes there, and using it rather than a list of its own is the
    point: a director reading why a still was redrawn should find all of it in one place, in
    order. The `verdict` key is the same one `stills.review` stamps, so the node's existing
    pips read these without knowing they came from somewhere new.
    """
    from . import stills

    lens = verdict["lens"]
    text = (f"{lens}: looks right." if verdict["passed"]
            else f"{lens}: {verdict['problem']}"
                 + (f" Suggested fix: {verdict['fix']}" if verdict["fix"] else ""))
    return stills.remember(board, n, lens, text,
                           verdict="pass" if verdict["passed"] else "fail")


def filed(board: board_mod.Board, *, recent: int | None = None) -> list[dict]:
    """Every lens verdict on this board, in beat order, read back out of `asset_chat`.

    Out of the transcript rather than a list of its own, because the transcript is where
    `record` files them and a second store would be the drift the derived-state design exists
    to prevent. Each entry carries the beat number so a caller formatting a report does not
    have to walk the beats again. `recent` keeps only the last N verdict turns per beat --
    the director's synthesis wants the tail, the maker's brief wants the standing state.
    """
    found: list[dict] = []
    for beat in board.ordered_beats():
        turns = [turn for turn in (beat.get("asset_chat") or [])
                 if turn.get("verdict") and turn.get("role") in LENSES]
        if recent is not None:
            turns = turns[-recent:]
        for turn in turns:
            found.append({"beat": int(beat["n"]), "lens": str(turn["role"]),
                          "verdict": str(turn["verdict"]),
                          "text": str(turn.get("text") or "")})
    return found


def failing(board: board_mod.Board) -> list[dict]:
    """The verdicts still standing against this board: the LATEST per (beat, lens), fails only.

    Latest rather than any, because a fail that was fixed and re-inspected to a pass is
    history -- a maker told about it would spend a metered render un-fixing the fix. This is
    the read `crew` uses both to decide whether the inspect gate reopens the stills phase and
    to tell the asset-maker what the inspectors said.
    """
    latest: dict[tuple[int, str], dict] = {}
    for item in filed(board):
        latest[(item["beat"], item["lens"])] = item
    return [item for _key, item in sorted(latest.items()) if item["verdict"] == "fail"]


def failing_report(board: board_mod.Board) -> str:
    """The standing failures as prose for a brief. Empty when every latest verdict passes."""
    return "\n".join(f"beat {item['beat']} ({item['lens']}): {item['text']}"
                     for item in failing(board))
