"""Talking a script into existence, instead of asking for one and waiting.

`planner.plan` writes a whole film from a one-line concept and two numbers. It is a good path
and it stays exactly as it is -- but everything the director actually decides about a 40-second
film is settled by those two numbers before the model is ever called: how the time is split,
how many camera setups there are, who is in it, what the last frame leaves you with.

Those four questions are not this module's invention. They are **section 0 of
`prompts/40s-stop-motion-script.md`**, which opens "STOP -- interview the director first",
lists them, and ends "Only after you have answers do you write the script." `planner.brief`
splices that section out and replaces it with `planner.ANSWERS`, because the studio's form had
already answered it. This path splices out nothing: the brief is handed over whole and the
interview runs the way the document says it should.

That is the whole reason there is still exactly one copy of the specification. Nothing here
restates a rule of the medium, a beat length, or a shot count -- the file says all of it, and
`template()` is imported from `planner` rather than re-read, so the two paths cannot drift.

What IS here:

- **The board exists from the first message**, with `beats: []`. It costs a directory and a
  small JSON file, and it buys a transcript that survives a reload, a URL you can send someone,
  a draft you can delete through the route that already trashes rather than deletes -- and,
  the good part, `data["chat"]` as the transcript, so the interview and every later board
  conversation are one history rather than two.
- **One tool, `write_script`,** whose parameters are `planner.PLAN_SCHEMA` plus a per-beat
  `seconds`. The model talks in prose while it is interviewing and calls the tool when it has
  answers. Per-beat seconds is required rather than optional: section 0's first question is
  about *mixed* lengths (`2 x 10s + 4 x 5s`), which the one-shot path cannot express because
  `ANSWERS` fixes one length for the whole film.
- **The self-check is `planner.review`, unchanged.** There is one implementation of the brief's
  section 11 in this repo and it stays one.
"""

from __future__ import annotations

import copy
from typing import Callable

from . import board as board_mod
from . import config, gemini, planner, script

# Two sentences of framing, and nothing about the medium. Everything the model needs to know
# about what a script has to be is in the brief it is handed next; a summary here is the exact
# drift `planner`'s docstring exists to prevent.
SYSTEM = """You are interviewing a film director, following the brief you are about to be given.

Two things about the format of your replies, which the brief does not cover because they are
about this studio rather than about the film:

- When you offer the director a set of choices, put each one on its own line beginning with
  "- ". They are shown as buttons, so a choice buried mid-sentence cannot be tapped.
- Do not write the script as prose, ever. When you have the answers you need, call the
  write_script tool. That is the only way a script reaches the board.

Keep every reply short. This is a conversation, not a document."""

# Replaces the paragraph `planner.review` uses on the one-shot path, which says the director
# fixed one length for every beat in section 0. On this path they answered section 0 in their
# own words and may well have asked for a mixed rhythm, so the review is told what is settled
# here instead: the count and the lengths, because they were agreed in the conversation, not
# because a form imposed them.
SETTLED_BY_INTERVIEW = (
    "Items 1, 2, 3 and 12 are already settled and are NOT yours to fix: the director chose the "
    "beat count and every beat's length in the interview, and those choices are final however "
    "the rhythm reads to you. Do not change a single beat length, and do not add or remove a "
    "beat.\n\n"
)


class DevelopError(RuntimeError):
    """The board cannot be developed further. `status` is what the route should answer with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def brief(concept: str, medium_key: str | None = None) -> str:
    """The authoring prompt exactly as a human would paste it, interview included."""
    return planner.template(medium_key).replace("<<<CONCEPT>>>", concept.strip())


def write_tool() -> dict:
    """`write_script`, from `PLAN_SCHEMA` rather than beside it.

    Derived, never copied: a second literal of the script's shape is a second thing to update
    when the brief changes, and the one that gets forgotten is always the copy.
    """
    beats = copy.deepcopy(planner.PLAN_SCHEMA["properties"]["beats"])
    item = beats["items"]
    # A number, with the two legal values in the description rather than in an `enum`: a numeric
    # enum on a function declaration answers with a 400 naming the index and takes the whole
    # call with it (see `gemini._declarable`). Nothing is lost by saying it in words --
    # `config.snap_seconds` in `script.normalise` is what actually enforces it.
    item["properties"]["seconds"] = {
        "type": "number",
        "description": (
            "How long this beat runs. 5 or 10, nothing else, and the lengths must add up to "
            "the split the director chose."
        ),
    }
    item["required"] = [*item["required"], "seconds"]
    properties = {**planner.PLAN_SCHEMA["properties"], "beats": beats}
    return gemini.tool(
        "write_script",
        "Write the finished shooting script. Call this only once you have the director's "
        "answers to the section 0 questions -- it ends the interview and puts the film on the "
        "board.",
        properties,
        list(planner.PLAN_SCHEMA["required"]),
    )


def start(message: str) -> board_mod.Board:
    """The empty board a conversation begins on.

    Named from the first thing said rather than from a title, because there is no title yet,
    and through `free_slug` rather than bare `slugify`: `agent.create` deliberately reuses a
    directory when the same concept is planned again, but two *conversations* about a paper pig
    are two films.
    """
    concept = message.strip()
    if not concept:
        raise DevelopError("say what the film is about", status=422)
    return board_mod.Board.create(
        script.free_slug(board_mod.slugify(concept)),
        {
            "title": concept[:60],
            "concept": concept,
            "style_bible": "",
            "beats": [],
            "seconds": config.BEAT_LENGTHS[-1],
            "steps": config.DEFAULT_STEPS,
            "seed": 1101,
            "chat": [],
        },
    )


def developable(board: board_mod.Board) -> None:
    """Refuse to rewrite a script that has already been paid for.

    The only guard on this path, and it guards the one thing that costs real money: a beat with
    a `render` record has a clip on disk that a new script would orphan silently. Editing the
    board scene by scene is still open -- what is refused is replacing the whole thing.
    """
    rendered = [b["n"] for b in board.ordered_beats() if b.get("render")]
    if rendered:
        raise DevelopError(
            f"beat {', '.join(map(str, rendered))} {'has' if len(rendered) == 1 else 'have'} "
            "already been rendered, so rewriting the whole script here would leave paid clips "
            "attached to scenes that no longer exist. Edit the scenes in the conversation on "
            "the board instead, or discard those clips first."
        )


def history(board: board_mod.Board, limit: int = 20) -> str:
    """The interview so far, as a labelled block inside the question.

    The same shape `agent.transcript` uses, and for the same reason plus one more. The reason:
    a model reads its own earlier turn as the most authoritative thing in the prompt, so it has
    to be told what that section is. The extra one: replaying the turns as real `assistant`
    messages would mean reconstructing them text-only across an HTTP boundary, and Gemini 3
    signs its reasoning and checks that signature on the next turn of a tool loop.
    """
    turns = board.data.get("chat", [])[-limit:]
    if not turns:
        return ""
    spoken = "\n".join(
        f'{"DIRECTOR" if t["role"] == "user" else "YOU"}: {t["text"]}' for t in turns
    )
    return f"THE INTERVIEW SO FAR:\n{spoken}\n\n"


def turn(board: board_mod.Board, message: str, *,
         log: Callable[[str], None] = print,
         announce: Callable[[], None] | None = None) -> dict:
    """One turn of the interview. Returns `{"reply", "written"}`.

    One model call, not a loop, and deliberately: the tool's effect is the board, and the board
    is what the page is showing. Feeding "I wrote the script" back for a second turn would buy
    a sentence the director can already see is true.
    """
    developable(board)
    concept = str(board.data.get("concept") or message)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": (
            f"===== THE BRIEF =====\n{brief(concept, board.medium())}\n\n"
            f"===== THIS STUDIO =====\n{history(board)}DIRECTOR: {message}"
        )},
    ]
    assistant = gemini.chat(messages, tools=[write_tool()])
    reply = str(assistant.get("content") or "").strip()
    calls = [args for name, args in gemini.calls_of(assistant) if name == "write_script"]

    written = False
    if calls:
        if announce:
            announce()
        log("[develop] the interview is over; marking the draft against the brief")
        draft = reviewed(calls[0], concept, log=log, medium_key=board.medium())
        adopt(board, draft)
        written = True
        total = sum(b["seconds"] for b in board.ordered_beats())
        reply = reply or (
            f'Written: "{board.data.get("title")}" -- {len(board.data["beats"])} beats, '
            f"{total:.0f}s. Every line of it is yours to change from here."
        )

    chat = board.data.setdefault("chat", [])
    chat.append({"role": "user", "text": message})
    chat.append({
        "role": "gemini",
        "text": reply or "…",
        "ops": [{"op": "set_script", "summary": "wrote the script from the interview"}]
        if written else [],
    })
    board.save()
    return {"reply": reply, "written": written}


def reviewed(draft: dict, concept: str, *, log: Callable[[str], None],
             medium_key: str | None = None) -> dict:
    """The draft, marked against the brief's own section 11.

    `planner.review` is shown only the fields it may touch (`planner._as_json`), and `seconds`
    is not one of them -- correctly, because on this path the lengths are the director's answer
    to section 0 and the review is told so in `SETTLED_BY_INTERVIEW`. So they are re-attached
    here by position, which is safe because `review` rejects any result whose beat count moved.
    """
    numbered = planner.numbered(copy.deepcopy(draft))
    lengths = [beat.get("seconds") for beat in numbered.get("beats") or []]
    if not lengths:
        raise gemini.GeminiError("the model called write_script with no beats in it.")
    if not config.PLAN_REVIEW:
        return numbered
    checked = planner.review(numbered, brief(concept, medium_key), log=log,
                             settled=SETTLED_BY_INTERVIEW)
    for beat, seconds in zip(checked.get("beats") or [], lengths):
        beat["seconds"] = seconds
    return checked


def adopt(board: board_mod.Board, draft: dict) -> None:
    """Write a finished script onto the board the conversation is already on.

    A merge rather than a create, and the slug is the reason: `script.adopt` would mint a new
    directory, which would move the page out from under a director mid-sentence and strand the
    interview on a board nobody is looking at. `chat` and `canvas` are left alone for the same
    reason -- the conversation is what got here.
    """
    plan = script.normalise(draft)
    for key in ("title", "concept", "style_bible", "beats", "seconds"):
        board.data[key] = plan[key]
    board.data.setdefault("steps", plan["steps"])
    board.data.setdefault("seed", plan["seed"])
    board.save()
