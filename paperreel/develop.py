"""Talking a script into existence, instead of asking for one and waiting.

`planner.plan` writes a whole film from a one-line concept and two numbers. It is a good path
and it stays exactly as it is -- but everything the director actually decides about a short
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
- **Two tools:** `ask_director` shapes the interview as a form the studio renders; `write_script`
  (from `planner.PLAN_SCHEMA` plus per-beat `seconds`) ends it. A prose numbered list that
  skipped the ask tool is recovered by `questions_from_prose` so the director still gets fields.
  Per-beat seconds is required: section 0's first question is about *mixed* lengths
  (`2 x 10s + 4 x 5s`), which the one-shot path cannot express because `ANSWERS` fixes one
  length for the whole film.
- **The self-check is `planner.review`, unchanged.** There is one implementation of the brief's
  section 11 in this repo and it stays one.
"""

from __future__ import annotations

import copy
import re
from typing import Callable

from . import board as board_mod
from . import config, gemini, planner, script

# Two sentences of framing, and nothing about the medium. Everything the model needs to know
# about what a script has to be is in the brief it is handed next; a summary here is the exact
# drift `planner`'s docstring exists to prevent.
SYSTEM = """You are interviewing a film director, following the brief you are about to be given.

Four things about the format of your replies, which the brief does not cover because they are
about this studio rather than about the film:

- When you need answers, call the ask_director tool with structured questions. That is how the
  director gets checkboxes and fields they can fill in -- do not bury the interview as a long
  prose list. One ask_director call per turn; keep the preamble short.
- For a closed set of options use kind "choice" (one answer) or "multi" (several). For open
  answers use kind "text". Put the real options in the options array -- the brief's beat-split
  list, shot counts, cast choices, and so on. Prefer ids beats, shots, cast, tone for the four
  section 0 questions.
- Do not write the script as prose, ever. Call write_script only when you have answers to all
  four section 0 topics (beat structure, camera setups, cast, tone and ending) -- or the
  director said "defaults" / "you decide" for the whole interview. A lone beat-split (e.g.
  "8 x 5s") is not enough: call ask_director again with the unanswered questions.
- That is the only way a script reaches the board.

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

# What ask_director may put on a question. Strings only -- a non-string enum on a function
# declaration answers 400 (see gemini._declarable).
QUESTION_KINDS = ("choice", "multi", "text")

# Closed sets the brief already names. When the model asks with kind "choice" but forgets the
# options array (or a prose fallback recovers the prompt with none), these fill the chips so
# the director is not handed a blank text field for a question that was never open-ended.
DEFAULT_OPTIONS = {
    "beats": (
        "4 × 5s",
        "2 × 10s",
        "6 × 5s",
        "8 × 5s",
        "4 × 10s",
        "2 × 10s + 4 × 5s",
        "1 × 10s + 6 × 5s",
        "3 × 10s + 2 × 5s",
        "6 × 10s",
    ),
    "shots": (
        "3 setups",
        "4 setups",
        "5 setups",
        "one long chained take",
        "no long chained take",
    ),
    "cast": (
        "design them",
        "I will paste a style bible",
    ),
}

INTERVIEW_TOPICS = ("beats", "shots", "cast", "tone")


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
        "answers to all four section 0 questions (beats, shots, cast, tone) -- or they said "
        "defaults / you decide for the whole interview. A single beat-split is not enough. "
        "This ends the interview and puts the film on the board.",
        properties,
        list(planner.PLAN_SCHEMA["required"]),
    )


def ask_tool() -> dict:
    """Structured interview questions the studio renders as a form.

    The wire stays a plain chat message when the director answers -- this tool only shapes
    what the *model asks*, so checkboxes and fields can be drawn without parsing prose.
    """
    return gemini.tool(
        "ask_director",
        "Ask the director one or more interview questions as a structured form. Call this "
        "instead of listing questions in prose. Do not call write_script in the same turn. "
        "For section 0 use ids beats / shots / cast / tone; put the brief's beat splits and "
        "shot-count choices in options.",
        {
            "preamble": {
                "type": "string",
                "description": "Optional short intro shown above the form. One or two sentences.",
            },
            "questions": {
                "type": "array",
                "description": "The questions to show, in order. Usually the unanswered section 0 items.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Stable handle, e.g. beats, shots, cast, tone.",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "The question the director sees.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": list(QUESTION_KINDS),
                            "description": (
                                "choice = pick one option; multi = pick several; "
                                "text = free answer."
                            ),
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Labels for choice/multi. Required for those kinds. "
                                "Omit or leave empty for text. For beats use the brief's "
                                "splits; for shots use 3/4/5 setups or one long chained take; "
                                "for cast use design them / I will paste a style bible."
                            ),
                        },
                    },
                    "required": ["id", "prompt", "kind"],
                },
            },
        },
        ["questions"],
    )


def _default_options_for(qid: str, prompt: str) -> list[str]:
    """Chip labels when the model named a known closed set but left options empty."""
    key = qid.strip().lower()
    if key in DEFAULT_OPTIONS:
        return list(DEFAULT_OPTIONS[key])
    lower = prompt.lower()
    if "beat" in lower or "split" in lower or "duration" in lower or "how long" in lower:
        return list(DEFAULT_OPTIONS["beats"])
    # Cast prompts often mention a reference from a previous shot, so the more specific
    # subject test must run before the camera/setup test.
    if "cast" in lower or "style_bible" in lower or "puppet" in lower:
        return list(DEFAULT_OPTIONS["cast"])
    if "shot" in lower or "camera" in lower or "setup" in lower:
        return list(DEFAULT_OPTIONS["shots"])
    return []


def normalise_questions(raw) -> list[dict]:
    """Validate ask_director arguments into what the studio can render.

    Drops broken entries rather than failing the turn: a partial form is still better than
    falling back to an unanswerable prose blob, and the director can always type instead.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "text").strip().lower()
        if kind not in QUESTION_KINDS:
            kind = "text"
        prompt = " ".join(str(item.get("prompt") or "").split())
        if not prompt:
            continue
        qid = " ".join(str(item.get("id") or f"q{index + 1}").split()) or f"q{index + 1}"
        options = []
        for option in item.get("options") or []:
            label = " ".join(str(option).split())
            if label and label not in options:
                options.append(label)
        if kind in ("choice", "multi") and len(options) < 2:
            options = _default_options_for(qid, prompt) or options
        if kind in ("choice", "multi") and len(options) < 2:
            # Still not enough to be a real choice -- degrade to a text field.
            kind = "text"
            options = []
        elif kind == "text" and not options:
            # A prose-recovered beat/shots/cast line with no bullets still deserves chips.
            filled = _default_options_for(qid, prompt)
            if filled:
                kind = "choice"
                options = filled
        out.append({"id": qid, "prompt": prompt, "kind": kind, "options": options})
    return out[:8]


def questions_from_prose(text: str) -> list[dict]:
    """Recover a form from a prose interview turn that never called ask_director.

    Mirrors `questionsFromProse` in the studio: numbered lines become questions. Bullets with
    an em-dash become suggestions; plain bullets are subquestions appended to the prompt. Run
    through `normalise_questions` so default chips still land when options were omitted.
    """
    found: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        if len(current["options"]) >= 2:
            current["kind"] = "choice"
        found.append(current)
        current = None

    for raw in text.splitlines():
        line = raw.strip()
        numbered = re.match(r"^\*{0,2}(\d+)[.)]\s+(.*?)\*{0,2}$", line)
        if numbered:
            flush()
            prompt = re.sub(r"\*\*", "", numbered.group(2))
            prompt = " ".join(prompt.split())
            if prompt:
                current = {
                    "id": f"q{numbered.group(1)}",
                    "prompt": prompt,
                    "kind": "text",
                    "options": [],
                }
            continue
        if current and line.startswith("- "):
            bullet = " ".join(line[2:].split())
            separator = re.search(r"\s+[—–]\s+|\s+--\s+", bullet)
            backtick = re.match(r"^`([^`]+)`", bullet)
            if separator or backtick:
                label = backtick.group(1) if backtick else bullet[:separator.start()]
                label = label.replace("`", "").rstrip(".,;:").strip()
                if label and len(label) <= 72 and label not in current["options"]:
                    current["options"].append(label)
            elif bullet:
                current["prompt"] = f'{current["prompt"]} {bullet}'
            continue
        # Models often put the bold numbered heading and question body on separate lines.
        if current and line:
            current["prompt"] = f'{current["prompt"]} {line.replace("**", "")}'
    flush()
    return normalise_questions(found)


def _answer_values(raw) -> dict[str, str]:
    """The form's machine-readable answers, narrowed to the four interview topics."""
    if not isinstance(raw, dict):
        return {}
    return {
        topic: " ".join(str(raw.get(topic) or "").split())
        for topic in INTERVIEW_TOPICS
        if str(raw.get(topic) or "").strip()
    }


def _deferred(value: str) -> bool:
    lower = value.lower()
    return "you decide" in lower or lower == "defaults"


def _beat_total(value: str) -> int | None:
    """Read explicit `N x 5s + M x 10s` answers; prose remains a clarification."""
    parts = re.findall(r"(\d+)\s*[x×]\s*(5|10)\s*s?", value.lower())
    if not parts:
        return None
    return sum(int(count) * int(seconds) for count, seconds in parts)


def _clarifications(answers: dict[str, str]) -> list[dict]:
    """Questions that remain incomplete, independent of what the model tried to do."""
    questions: list[dict] = []

    beats = answers.get("beats", "")
    if not beats:
        beat_prompt = (
            "How long should the film run, and how do you want that split across 5s and "
            "10s beats?"
        )
    elif not _deferred(beats) and _beat_total(beats) is None:
        beat_prompt = (
            "I couldn't read that as a 5s/10s split (e.g. `4 × 5s` or `2 × 10s + 4 × 5s`). "
            "Choose a chip or write another combination."
        )
    else:
        beat_prompt = ""
    if beat_prompt:
        questions.append({
            "id": "beats",
            "prompt": beat_prompt,
            "kind": "choice",
            "options": list(DEFAULT_OPTIONS["beats"]),
        })

    shots = answers.get("shots", "")
    shot_lower = shots.lower()
    shot_counts = set(re.findall(r"\b[345]\b", shot_lower))
    has_count = len(shot_counts) == 1
    chooses_no_take = "no long" in shot_lower
    affirmative = shot_lower.replace("no long chained take", "")
    chooses_take = (
        "long" in affirmative or "chain" in affirmative
        or "unbroken" in affirmative or "continuous" in affirmative
    )
    has_take_choice = chooses_take != chooses_no_take
    if not shots or (not _deferred(shots) and (not has_count or not has_take_choice)):
        missing = []
        if not has_count:
            missing.append("a total shot count")
        if not has_take_choice:
            missing.append("whether to include a long chained take")
        detail = f" Please choose {' and '.join(missing)}." if missing else ""
        questions.append({
            "id": "shots",
            "prompt": f"Set the camera plan: 3–5 setups, plus yes or no to one long chained take.{detail}",
            "kind": "multi",
            "options": list(DEFAULT_OPTIONS["shots"]),
        })

    if not answers.get("cast"):
        questions.append({
            "id": "cast",
            "prompt": "Should the model design the cast, or will you provide a locked style bible?",
            "kind": "choice",
            "options": list(DEFAULT_OPTIONS["cast"]),
        })

    if not answers.get("tone"):
        questions.append({
            "id": "tone",
            "prompt": "What mood or final image should the film leave with the audience?",
            "kind": "text",
            "options": [],
        })
    return questions


def _hold_for_answers(board: board_mod.Board, message: str, raw_answers) -> dict | None:
    """Persist valid progress and return a clarification turn while anything is incomplete."""
    if raw_answers is None:
        return None
    merged = _answer_values(board.data.get("interview_answers"))
    merged.update(_answer_values(raw_answers))
    board.data["interview_answers"] = merged
    questions = _clarifications(merged)
    if not questions:
        return None

    chat = board.data.setdefault("chat", [])
    chat.append({"role": "user", "text": message})
    reply = "A couple of answers still need to be settled before I can write the script."
    chat.append({
        "role": "gemini",
        "text": reply,
        "ops": [{"op": "ask_director", "summary": f"clarified {len(questions)} answer"
                 f"{'' if len(questions) == 1 else 's'}"}],
        "questions": questions,
    })
    board.save()
    return {"reply": reply, "written": False, "questions": questions}


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
         answers=None,
         log: Callable[[str], None] = print,
         announce: Callable[[], None] | None = None) -> dict:
    """One turn of the interview. Returns `{"reply", "written", "questions"}`.

    One model call, not a loop, and deliberately: the tool's effect is the board (or the form
    the director is about to fill), and the board is what the page is showing. Feeding "I wrote
    the script" back for a second turn would buy a sentence the director can already see is true.
    """
    developable(board)
    held = _hold_for_answers(board, message, answers)
    if held:
        return held
    concept = str(board.data.get("concept") or message)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": (
            f"===== THE BRIEF =====\n{brief(concept, board.medium())}\n\n"
            f"===== THIS STUDIO =====\n{history(board)}DIRECTOR: {message}"
        )},
    ]
    assistant = gemini.chat(messages, tools=[ask_tool(), write_tool()])
    reply = str(assistant.get("content") or "").strip()
    calls = gemini.calls_of(assistant)
    writes = [args for name, args in calls if name == "write_script"]
    asks = [args for name, args in calls if name == "ask_director"]

    written = False
    questions: list[dict] = []
    if writes:
        # Writing ends the interview. An ask in the same turn is dropped -- the form would sit
        # under a finished script and the director would have nothing left to answer.
        if announce:
            announce()
        log("[develop] the interview is over; marking the draft against the brief")
        draft = reviewed(writes[0], concept, log=log, medium_key=board.medium())
        adopt(board, draft)
        written = True
        total = sum(b["seconds"] for b in board.ordered_beats())
        reply = reply or (
            f'Written: "{board.data.get("title")}" -- {len(board.data["beats"])} beats, '
            f"{total:.0f}s. Every line of it is yours to change from here."
        )
    elif asks:
        payload = asks[0] if isinstance(asks[0], dict) else {}
        questions = normalise_questions(payload.get("questions"))
        preamble = " ".join(str(payload.get("preamble") or "").split())
        if preamble:
            reply = preamble
        elif questions:
            reply = reply or "Answer these, or leave any as you decide."
        else:
            reply = reply or "…"

    # Model dumped a numbered interview as prose and skipped the tool -- synthesize the same
    # form the studio would have drawn from ask_director, so the director still gets fields.
    if not written and not questions and reply:
        questions = questions_from_prose(reply)

    chat = board.data.setdefault("chat", [])
    chat.append({"role": "user", "text": message})
    entry: dict = {
        "role": "gemini",
        "text": reply or "…",
        "ops": [{"op": "set_script", "summary": "wrote the script from the interview"}]
        if written else (
            [{"op": "ask_director", "summary": f"asked {len(questions)} question"
              f"{'' if len(questions) == 1 else 's'}"}]
            if questions else []
        ),
    }
    if questions:
        entry["questions"] = questions
    chat.append(entry)
    board.save()
    return {"reply": reply, "written": written, "questions": questions}


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
    board.data.pop("interview_answers", None)
    board.data.setdefault("steps", plan["steps"])
    board.data.setdefault("seed", plan["seed"])
    board.save()
