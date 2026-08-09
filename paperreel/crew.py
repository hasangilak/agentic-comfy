"""Three agents in the order a reel needs them, and the rule that says which one is next.

The orchestration here is a state machine over derived state, not a graph framework and not a
model deciding. Both of those were considered and both would be a second authority over
something this repo already has exactly one of: `jobs.Runner` owns durable serial execution,
`storyboard.json` owns state, and `Board.states()` owns what is finished. A checkpointer would
be a second store of the same state -- the drift the whole derived-state design exists to
prevent -- and a director agent would spend a model turn per decision on a question four `if`
statements answer for nothing.

    script      no beats yet
    storyboard  beats, but no panel written
    assets      panels, but beats still waiting on a still
    None        nothing left that does not cost GPU money

That last row is the design. There is no fourth stage in `STAGES` and no key in `AGENT_FOR` a
fourth could resolve through, so "the crew is finished" and "only the paid stage is left" are
literally the same value. The studio's own `resolveStage` answers `"studio"` where this answers
`None`, and the difference is the whole boundary this module is drawn around.
"""

from __future__ import annotations

from . import board as board_mod
from . import config, develop, llm as llm_mod, runtime, skills

# The stages, in order. Three, and none of them is the one that spends the GPU.
STAGES = ("script", "storyboard", "assets")
AGENT_FOR = {
    "script": "script-writer",
    "storyboard": "storyboarder",
    "assets": "asset-maker",
}

# What each agent is told when the orchestrator starts it and the director said nothing
# specific. Deliberately short: the standing instructions are in the skill, and a brief that
# restated them would be a second copy drifting from the first.
BRIEF_FOR = {
    "script": "Write this reel's script. Follow the brief, interview included.",
    "storyboard": ("Design what this reel reuses, then storyboard every shot. Work out what "
                   "the designs should be from the script."),
    "assets": ("Render the opening still for every beat waiting on one, look at what came "
               "back, and fix what is wrong before you render anything twice."),
}


def next_stage(board: board_mod.Board | None) -> str | None:
    """Which agent this board is waiting on, or None when only the GPU stage is left.

    The four reads mirror `resolveStage` in `studio/src/route.ts` line for line, and the
    duplication is deliberate rather than accidental: the Python answer is not reachable from
    the browser without adding a route, and a route is a bigger change than four lines twice.
    `crew.py --where` is what makes a disagreement between the two observable in one command.

    `to_json()` hashes every conditioning image on the board, so this is called once per stage
    transition and never inside a round.
    """
    if board is None or not board.beats:
        return "script"
    if not any(str(beat.get("panel") or "").strip() for beat in board.ordered_beats()):
        return "storyboard"
    if not board.data.get("manual_stills") and board.to_json()["assets_needed"]:
        return "assets"
    return None


def one(name: str, board: board_mod.Board | None, message: str, *,
        hooks: runtime.Hooks = runtime.Hooks(),
        llm: llm_mod.LLM | None = None,
        state: dict | None = None) -> runtime.Turn:
    """One named agent, one message, no orchestration.

    The transcript is written here rather than inside `runtime.run` because a turn with no
    board -- the script agent before it has written anything -- has nowhere to write one, and a
    loop that sometimes records and sometimes does not is worse than a caller that decides.
    """
    agent = runtime.build(name, llm=llm)
    text = prelude(board)
    hooks.say(f"[{name}] {message.strip()[:120]}")
    turn = runtime.run(agent, message, board=board, prelude=text, hooks=hooks, state=state)
    if turn.board is not None:
        runtime.remember(turn.board, name, message, turn)
        hooks.changed()
    return turn


def start(concept: str, *, beats: int = 4, seconds: float = config.BEAT_LENGTHS[-1],
          hooks: runtime.Hooks = runtime.Hooks(),
          llm: llm_mod.LLM | None = None) -> board_mod.Board:
    """Mint a board from a concept and run the script agent on it.

    The empty board comes from `develop.start`, which is what `POST /api/reels/develop` uses:
    the board exists from the first message so the conversation has somewhere to live, and
    `data["chat"]` is that conversation rather than two synthetic turns written afterwards.
    """
    board = develop.start(concept)
    hooks.say(f"[crew] new reel {board.slug}")
    hooks.changed()
    one("script-writer", board,
        f"{BRIEF_FOR['script']} The director wants about {beats} beats at "
        f"{seconds:g} seconds each. The film is: {concept}",
        hooks=hooks, llm=llm)
    return board_mod.Board.load(board.slug)


def run(board: board_mod.Board, *, note: str = "", stop_after: str | None = None,
        hooks: runtime.Hooks = runtime.Hooks(),
        llm: llm_mod.LLM | None = None) -> list[runtime.Turn]:
    """Walk the board forward until nothing is left that does not cost GPU money.

    `state` is shared across every stage on purpose: it carries the still budget, and a budget
    that reset between agents would not be one.
    """
    if stop_after is not None and stop_after not in STAGES:
        raise ValueError(f"stop_after has to be one of {', '.join(STAGES)}")
    shared: dict = {}
    turns: list[runtime.Turn] = []
    done: str | None = None
    while True:
        if hooks.stopping():
            hooks.say("[crew] cancelled")
            break
        stage = next_stage(board)
        if stage is None:
            hooks.say("[crew] nothing left that does not cost money to render")
            break
        if stage == done:
            # `next_stage` is a pure read, so a stage that ran and left the board in the same
            # place would be started again forever. No automatic retry is offered: the log says
            # what happened and the director decides, which is the same judgement
            # `planner.review` makes about a review that came back wrong.
            hooks.say(f"[crew] {stage} ran and the board did not move; stopping")
            break
        hooks.say(f"[crew] {stage}: {AGENT_FOR[stage]}")
        message = BRIEF_FOR[stage] + (f"\n\nThe director says: {note.strip()}" if note.strip()
                                      else "")
        turns.append(one(AGENT_FOR[stage], board, message,
                         hooks=hooks, llm=llm, state=shared))
        done = stage
        # Reloaded rather than reused: every `api.py` handler loads the board per job for the
        # same reason, and it guards against a tool that replaced `board.data` wholesale rather
        # than mutating it.
        board = board_mod.Board.load(board.slug)
        if stop_after == stage:
            hooks.say(f"[crew] stopping after {stage}, as asked")
            break
    return turns


def prelude(board: board_mod.Board | None) -> str:
    """The per-run half of the prompt: the board as it is, right now.

    Not part of the system prompt, and that is `agent.turn`'s measurement rather than a style
    choice -- the model answered about the reel from whichever board-shaped text sat nearest
    the question. It is also what lets `skills.py` cache a rendered skill on its mtime: two
    runs against two different boards share a system prompt because it says nothing about one.
    """
    if board is None:
        return "There is no board yet.\n\n"
    from . import agent as agent_mod

    return ("THE BOARD AS IT IS RIGHT NOW -- this is the only current state, and every answer "
            f"about the reel comes from here:\n{agent_mod.board_digest(board)}\n\n")


def catalogue() -> list[dict]:
    """Every agent this crew can run, for `--list` and `GET /api/agents`."""
    return skills.catalogue()
