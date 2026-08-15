"""The tool loop, with the prompt and the toolbox handed in rather than written above it.

`agent.turn` is the studio's chat panel: one loop, one system prompt, one toolbox, and a
prompt order that was arrived at by watching it get an answer wrong. It stays exactly as it is.
This is the same loop with the three fixed things lifted out, so a skill file plus a named set
of tools makes an agent, and the crew in `crew.py` is three of them in a row.

That means there are two tool loops in this repo now, which is a real cost and was accepted for
a specific reason: moving `agent.turn` onto this one would buy nothing a user can see and would
put the most-exercised path in the product through untested code. What this loop borrows
instead is `agent.py`'s *behaviour*, and the three parts worth naming are all failures somebody
already paid for:

- **A tool failure comes back as text, never as an exception.** The model recovers from being
  told "beat 7 is not on this board"; it cannot recover from the turn ending.
- **The assistant message goes back into the transcript verbatim.** Gemini 3 signs its
  reasoning and checks the signature on the next turn, so `llm.answered` hands the provider's
  own parts back untouched.
- **Reaching the round cap is not an error.** Whatever landed is real and the board shows it.
  The loop says it stopped and returns what it has.

Two things this loop does that `agent.turn` does not, both because a crew run is longer than a
chat turn:

- **Cancellation is checked between rounds.** A crew run is many rounds across three agents,
  and a cancel that only takes effect once execution is already inside `stills.generate` is not
  a cancel.
- **`Context.state` survives the whole run**, which is where the still budget is counted. The
  round cap bounds turns, not money, and the one metered tool in the toolbox needs a bound of
  its own.
"""

from __future__ import annotations

import itertools
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import board as board_mod
from . import config, llm as llm_mod, skills


class ActivityCollector:
    """Structured events for one turn, mirrored to SSE and persisted on the chat turn."""

    def __init__(self, emit: Callable[[dict], None] | None = None) -> None:
        self.events: list[dict] = []
        self._emit = emit
        self._seq = itertools.count()

    def _publish(self, event: dict) -> None:
        self.events.append(event)
        if self._emit is not None:
            self._emit(event)

    def start(self, kind: str, **fields: object) -> str:
        event_id = uuid.uuid4().hex[:8]
        self._publish({
            "id": event_id,
            "kind": kind,
            "status": "running",
            "started_at": time.time(),
            **fields,
        })
        return event_id

    def finish(self, event_id: str, *, status: str = "done", summary: str | None = None) -> None:
        for event in self.events:
            if event.get("id") != event_id:
                continue
            event["status"] = status
            event["ended_at"] = time.time()
            if summary is not None:
                event["summary"] = summary
            if self._emit is not None:
                self._emit(event)
            return

    def note(self, kind: str, **fields: object) -> None:
        """One-shot event (round markers, failures with no start)."""
        self._publish({
            "id": uuid.uuid4().hex[:8],
            "kind": kind,
            "status": fields.pop("status", "done"),
            "started_at": time.time(),
            "ended_at": time.time(),
            **fields,
        })


@dataclass(frozen=True)
class Hooks:
    """The four callbacks `jobs.Runner` supplies, carried as one object.

    They travel together through every signature in this package, and threading four keyword
    arguments through a generic loop and then through every tool is precisely where one gets
    dropped -- which shows up as a job with no log, or a cancel button that does nothing.
    Frozen, so `Hooks()` is safe as a default argument.

    `log` defaults to `print` for the same reason every module here does: the CLI is a real
    caller, not a test harness.
    """

    log: Callable[[str], None] = print
    progress: Callable[[int, float], None] | None = None
    announce: Callable[[], None] | None = None
    cancelled: Callable[[], bool] | None = None
    # Who is working, and on what. The other four callbacks are `jobs.Runner`'s existing
    # contract; this one is new and exists because a crew job is not one call -- it is up to
    # four agents across three stages, and a job strip that says "crew" for six minutes tells
    # the director nothing about which of them is thinking or what it has done so far.
    phase: Callable[[str], None] | None = None
    activity: Callable[[dict], None] | None = None

    def stopping(self) -> bool:
        return bool(self.cancelled and self.cancelled())

    def say(self, line: str) -> None:
        self.log(line)

    def changed(self) -> None:
        if self.announce is not None:
            self.announce()

    def doing(self, what: str) -> None:
        if self.phase is not None:
            self.phase(what)

    def track(self) -> ActivityCollector:
        return ActivityCollector(self.activity)


@dataclass
class Context:
    """What a tool is given: the board, the hooks, the transport, and a scratchpad.

    `board` is `None` only before the script agent has minted one -- every other agent is
    handed a board and its tools may assume it. A tool that creates one sets it here, and the
    loop reads it back out afterwards, which is how `crew.start` gets a slug it never named.
    """

    board: board_mod.Board | None
    hooks: Hooks
    llm: llm_mod.LLM
    state: dict = field(default_factory=dict)

    def need_board(self) -> board_mod.Board:
        """The board, or a sentence for the model. Raised, caught by the dispatcher, told."""
        if self.board is None:
            raise ToolRefused("there is no board yet -- write the script first")
        return self.board


class ToolRefused(RuntimeError):
    """A tool saying no in words the model can act on.

    Distinct from an unexpected exception only in the log line it produces: both come back to
    the model as text, but a refusal is an ordinary state and a crash is worth a louder line.
    """


# What a tool hands back: (what the model is told, what the user is shown). The pair is
# `agent._run_tool`'s, kept because the two are genuinely different -- a `read_board` tells the
# model a great deal and changes nothing the user should be shown as an edit.
Outcome = tuple[str, list[dict]]


@dataclass(frozen=True)
class Tool:
    spec: dict
    run: Callable[[Context, dict], Outcome]


@dataclass(frozen=True)
class Agent:
    skill: skills.Skill
    tools: dict[str, Tool]
    llm: llm_mod.LLM

    @property
    def declarations(self) -> list[dict]:
        return [tool.spec for tool in self.tools.values()]


@dataclass
class Turn:
    """What one agent did, once."""

    reply: str
    ops: list[dict]
    rounds: int
    # "answered" -- the model stopped calling tools; "cap" -- it hit max_rounds; "cancelled" --
    # the job was cancelled between rounds.
    stopped: str
    data: dict | None = None
    board: board_mod.Board | None = None
    agent: str = ""
    activity: list[dict] = field(default_factory=list)


def build(name: str, *, llm: llm_mod.LLM | None = None,
          toolbox: dict[str, Tool] | None = None,
          board: board_mod.Board | None = None) -> Agent:
    """Load a skill, resolve its tool names, bind a transport.

    The tool names are checked HERE and not in `skills.py`, and the check is the reason this
    function exists rather than being three lines in `run`. A SKILL.md is data a user can edit,
    so "the tools this skill asks for all exist" is a validation, and doing it at build time
    means `crew.py --list` catches a typo without a single model call -- and means there is no
    way to name a tool the toolbox does not offer, which is what keeps a render out of reach.

    `board` is only so `{{BRIEF}}` splices this reel's medium rather than the default. A
    papercraft board built without it is handed the paper-cutout authoring brief, which is
    how a director's pick still produced a cutout script.
    """
    from . import tools as tools_mod

    skill = skills.load(name, medium=board.medium() if board is not None else None)
    speaker = llm or llm_mod.provider()
    # The toolbox is built against the provider, not against a module-level default: a tool
    # declaration is written in the provider's own dialect (`llm.tool`), so building it once at
    # import would bake one provider's shape into every agent.
    if toolbox is not None:
        available = toolbox
    elif name == "director":
        available = tools_mod.director_toolbox(speaker)
    else:
        available = tools_mod.toolbox(speaker)
    chosen: dict[str, Tool] = {}
    for wanted in skill.tools:
        found = available.get(wanted)
        if found is None:
            raise skills.SkillError(
                f"{skill.path}: no tool called {wanted!r}. Available: "
                f"{', '.join(sorted(available))}."
            )
        chosen[wanted] = found
    return Agent(skill=skill, tools=chosen, llm=speaker)


def run(agent: Agent, message: str, *, board: board_mod.Board | None = None,
        prelude: str = "", hooks: Hooks = Hooks(), state: dict | None = None,
        collector: ActivityCollector | None = None,
        images: list[Path] | None = None) -> Turn:
    """One agent, one message, tools and all.

    `prelude` is where everything per-run goes -- the board digest, the beat list, the
    director's standing note. It is deliberately NOT part of the system prompt: `agent.turn`
    puts the board in the question for a measured reason (the model answered from whichever
    board-shaped text sat nearest the question), and keeping the system prompt free of per-run
    state is also what lets `skills.py` cache a rendered skill on its mtime.

    `images` land on that same user turn. Mise-en-scène has to *see* the design sheets (and
    on lock, the panels) rather than read a sentence about them; a 12-round loop that
    re-sends every still of the reel every round is the cost to avoid, so the caller caps
    the pack and leaves per-beat stills on `inspect_still`.
    """
    skill = agent.skill
    context = Context(board=board, hooks=hooks, llm=agent.llm,
                      state=state if state is not None else {})
    trace = collector if collector is not None else hooks.track()
    encoded = [agent.llm.encode(path) for path in images or [] if path.is_file()]
    question: dict = {"role": "user", "content": prelude + message}
    if encoded:
        question["images"] = encoded

    if skill.schema is not None:
        # One structured call, no loop. A skill whose whole output is one object has nothing to
        # decide between rounds, and the justification is already written three times in this
        # repo (`stills.converse`, `pictures.converse`, `staging.converse`): with only one
        # shape of answer available, a loop spends a round trip deciding to produce it.
        data = agent.llm.structured(
            [{"role": "system", "content": skill.system}, question],
            skill.schema, think=skill.think, temperature=skill.temperature, model=skill.model)
        return Turn(reply="", ops=[], rounds=1, stopped="answered", data=data,
                    board=context.board, agent=skill.name, activity=trace.events)

    messages: list[dict] = [
        {"role": "system", "content": skill.system},
        question,
    ]
    applied: list[dict] = []
    reply = ""
    stopped = "answered"
    rounds = 0
    for rounds in range(1, skill.max_rounds + 1):
        hooks.doing(f"{skill.name} · round {rounds}")
        trace.note("round", agent=skill.name, summary=f"round {rounds}")
        assistant = agent.llm.chat(messages, tools=agent.declarations, think=skill.think,
                                   temperature=skill.temperature, model=skill.model)
        spoken = str(assistant.get("content") or "").strip()
        if spoken:
            reply = spoken
        calls = agent.llm.calls_of(assistant)
        if not calls:
            break
        results: list[tuple[str, str]] = []
        for name, arguments in calls:
            outcome, summaries = _dispatch(agent, context, name, arguments, trace)
            results.append((name, outcome))
            applied += summaries
        messages += agent.llm.answered(assistant, results)
        if hooks.stopping():
            # Between rounds rather than inside one: a round that has already started has told
            # a tool to do something, and abandoning it halfway is how a half-written board
            # happens. The tools pass `hooks.cancelled` down themselves where they can honour it.
            hooks.say(f"[{skill.name}] cancelled after {rounds} rounds")
            stopped = "cancelled"
            break
        if rounds == skill.max_rounds:
            # Not an error. Whatever landed is real and the board shows it -- but the turn is
            # over, and saying so beats a reply that reads as though more was coming.
            hooks.say(f"[{skill.name}] stopped after {skill.max_rounds} tool rounds")
            stopped = "cap"

    if not reply:
        # An agent that spent its whole turn on tools and then said nothing still owes the
        # director a sentence, and the ops are the honest one.
        reply = ("Done: " + "; ".join(op["summary"] for op in applied) if applied
                 else "Nothing to change.")
    return Turn(reply=reply, ops=applied, rounds=rounds, stopped=stopped,
                board=context.board, agent=skill.name, activity=trace.events)


def _dispatch(agent: Agent, context: Context, name: str, arguments: dict,
              trace: ActivityCollector) -> Outcome:
    """One tool call, with every way it can go wrong turned into a sentence for the model.

    The blanket `except Exception` is the same judgement `agent._run_tool` makes and
    `jobs._run_one` makes one level up: these tools reach a separate image server over HTTP and
    write files, so the ways they fail are open-ended, and a turn that dies on the third of five
    calls loses the two that would have worked.
    """
    tool = agent.tools.get(name)
    if tool is None:
        trace.note("tool_call", tool=name, agent=agent.skill.name, status="failed",
                   summary=f"unknown tool {name}")
        return f"there is no tool called {name}", []
    event_id = trace.start("tool_call", tool=name, agent=agent.skill.name)
    try:
        outcome, summaries = tool.run(context, arguments if isinstance(arguments, dict) else {})
    except ToolRefused as refused:
        context.hooks.say(f"[{agent.skill.name}] {name}: {refused}")
        trace.finish(event_id, status="failed", summary=str(refused))
        return str(refused), []
    except Exception as failed:  # noqa: BLE001 -- see the docstring
        context.hooks.say(f"[{agent.skill.name}] {name} failed: {failed}")
        trace.finish(event_id, status="failed", summary=str(failed))
        return f"that did not work: {failed}", []
    summary = "; ".join(item["summary"] for item in summaries) if summaries else outcome[:120]
    trace.finish(event_id, status="done", summary=summary)
    return outcome, summaries


def remember(board: board_mod.Board, agent: str, message: str, turn: Turn) -> None:
    """Write the turn into the board's own transcript, under the agent's name.

    Into `data["chat"]` rather than a per-agent log, because that is where `agent.turn`,
    `develop.turn` and `agent.revise` all write and `revise`'s docstring states why: an edit
    hidden in a corner leaves the next conversational turn reading a board that changed for no
    reason it can see. An agent that rewrote five beats is that failure with more beats in it.

    Only the final pair lands here. The per-round tool chatter goes to the job log, which is
    where the studio already shows it, and a transcript that carried every round would be a
    transcript nobody reads.
    """
    chat = board.data.setdefault("chat", [])
    chat.append({"role": "user", "text": message})
    entry: dict = {"role": agent, "text": turn.reply, "ops": turn.ops}
    if turn.activity:
        entry["activity"] = turn.activity
    chat.append(entry)
    board.save()


def preview(agent: Agent, message: str = "", prelude: str = "",
            pictures: list[tuple[Path, str]] | None = None) -> str:
    """Exactly what would go to the model, for `crew.py --dry-run`. Calls nothing.

    This is the review artifact the money guard rests on: a prompt is read by a human before a
    turn is ever paid for, and a skill edit can be checked without spending anything.
    """
    lines = [
        f"# skill: {agent.skill.name}   ({agent.skill.path})",
        f"# model: {agent.skill.model or config.TEXT_MODEL}   think: {agent.skill.think}   "
        f"temperature: {agent.skill.temperature}   max_rounds: {agent.skill.max_rounds}",
        "",
        "===== SYSTEM =====",
        agent.skill.system,
        "",
        "===== USER =====",
        prelude + message,
        "",
    ]
    if pictures:
        lines.append("===== IMAGES =====")
        for index, (path, label) in enumerate(pictures, start=1):
            lines.append(f"  {index}. {label}  ({path.name})")
        lines.append("")
    lines.append("===== TOOLS =====")
    for tool in agent.tools.values():
        parameters = (tool.spec.get("parameters") or {}).get("properties") or {}
        required = set((tool.spec.get("parameters") or {}).get("required") or [])
        names = ", ".join(f"{key}*" if key in required else key for key in parameters) or "-"
        lines.append(f"  {tool.spec['name']}({names})")
        lines.append(f"      {tool.spec.get('description', '')}")
    return "\n".join(lines)
