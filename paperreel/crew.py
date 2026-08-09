"""The agents a reel needs, in stages, and the rule that says which stage is next.

The orchestration here is a state machine over derived state, not a graph framework and not a
model deciding. Both of those were considered and both would be a second authority over
something this repo already has exactly one of: `jobs.Runner` owns durable serial execution,
`storyboard.json` owns state, and `Board.states()` owns what is finished. A checkpointer would
be a second store of the same state -- the drift the whole derived-state design exists to
prevent -- and a director agent would spend a model turn per decision on a question four `if`
statements answer for nothing.

    script      no beats yet          script-writer, then the style artist
    storyboard  no panel written      the style artist, then mise-en-scene, then panels
    assets      beats waiting on a still   asset-maker, then three agents check its work
    None        nothing left that does not cost GPU money

**A stage is a cast, not an agent**, and that is the shape of the whole thing. A film crew is
several specialists on one scene rather than one generalist per phase, and the failure it
prevents here is specific: a single agent asked to write the story AND fix the material AND
block the frame answers about whichever of the three it noticed first. Each member of a cast
runs in turn on the same board, reading what the one before it left -- the board IS the
message passing, which is why no agent needs to be handed another's output.

The assets cast is the one with a shape of its own: the asset-maker renders, and then three
agents look at what came back through three different lenses -- craft, staging, story. They
**report and suggest; they never re-render.** See `critique.py` for why that bound is where it
is. Three vision calls per still, once, is the cost.

That last row of the table is the design. There is no fourth stage in `STAGES` and no cast a
fourth could resolve through, so "the crew is finished" and "only the paid stage is left" are
literally the same value. The studio's own `resolveStage` answers `"studio"` where this answers
`None`, and the difference is the whole boundary this module is drawn around.
"""

from __future__ import annotations

from . import board as board_mod
from . import config, critique, develop, llm as llm_mod, runtime, skills

# The stages, in order. Three, and none of them is the one that spends the GPU.
STAGES = ("script", "storyboard", "assets")

# The role a stage calls for, resolved to a skill at run time. `STYLE` is the only one that is
# not already a skill name: there are two style artists and which one runs is decided by what
# the reel is made of, not by the stage.
STYLE = "@style"

# Who works each stage, in the order they work it. Order matters and is not alphabetical:
#
#   script      the writer first, because there is nothing to style until there are beats.
#   storyboard  the style artist first, because mise-en-scene binds designs and cannot bind
#               one that has not been minted -- and the panels come last because
#               `panels._digest` names the designs a beat binds, so a panel written before the
#               binding is a panel written about a cast it could not see.
#   assets      the maker first and the three checkers after, which is the only order that
#               makes sense for a check.
STAGE_CAST: dict[str, tuple[str, ...]] = {
    "script": ("script-writer", STYLE),
    "storyboard": (STYLE, "mise-en-scene", "storyboarder"),
    "assets": ("asset-maker", STYLE, "mise-en-scene", "script-writer"),
}

# Which agents in a cast are there to CHECK the work rather than do it. They run only after
# something exists to check, they are told which lens is theirs, and none of them may render.
CHECKERS: dict[str, str] = {
    STYLE: "style",
    "mise-en-scene": "blocking",
    "script-writer": "story",
}

# One style artist per medium. A second medium is a table entry plus a SKILL.md; the resolution
# is here rather than in the skill's frontmatter because a skill does not know what board it is
# about to be run against.
STYLE_ARTIST: dict[str, str] = {
    config.PAPER_CUTOUT.key: "style-paper-cutout",
    config.CLAYMATION.key: "style-claymation",
}

# What each agent is told when the orchestrator starts it and the director said nothing
# specific. Deliberately short: the standing instructions are in the skill, and a brief that
# restated them would be a second copy drifting from the first.
BRIEF_FOR: dict[tuple[str, str], str] = {
    ("script", "script-writer"): "Write this reel's script. Follow the brief, interview included.",
    ("script", STYLE): ("Set the medium if it is not set, then write this reel's style bible "
                        "from the script. Do not draw anything yet."),
    ("storyboard", STYLE): ("Design what this film reuses -- the characters, the sets, the "
                            "props that appear in more than one shot -- and draw their sheets."),
    ("storyboard", "mise-en-scene"): ("Block every shot: what each frame holds and where "
                                      "everything stands in it. Bind the designs each shot "
                                      "contains."),
    ("storyboard", "storyboarder"): ("Storyboard every shot. The designs and the blocking are "
                                     "already on the board -- write the shot grammar and draw "
                                     "the panels."),
    ("assets", "asset-maker"): ("Render the opening still for every beat waiting on one, look "
                                "at what came back, and fix what is wrong before you render "
                                "anything twice."),
}

# What a checker is told. One string for all three, with its own lens spliced in, because the
# instruction genuinely is the same one -- look at every still through your lens, report, do not
# fix. Which lens is theirs is already in their skill; saying it again here is what stops a
# checker reviewing on somebody else's axis when it is run as part of a cast.
CHECK_BRIEF = (
    "The stills for this reel have been rendered. Look at every beat that has one, through the "
    "{lens} lens and that lens only, using inspect_still. Report what you find and suggest a "
    "fix for each problem. Do not render anything and do not rewrite any prompt -- the director "
    "reads your verdicts and decides."
)


def style_artist(board: board_mod.Board | None) -> str:
    """Which style artist this reel calls for, from what it is made of.

    Falls back to paper for the reason `config.medium` does: the key comes off a hand-editable
    document and a reel naming a medium nobody ships should still get an artist.
    """
    key = board.medium() if board is not None else config.DEFAULT_MEDIUM
    return STYLE_ARTIST.get(key, STYLE_ARTIST[config.DEFAULT_MEDIUM])


def cast_for(stage: str, board: board_mod.Board | None) -> list[str]:
    """The skill names that work one stage, in order, with the style role resolved."""
    return [style_artist(board) if name is STYLE or name == STYLE else name
            for name in STAGE_CAST.get(stage, ())]


def next_stage(board: board_mod.Board | None) -> str | None:
    """Which stage this board is waiting on, or None when only the GPU stage is left.

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


def stage(name: str, board: board_mod.Board, *, note: str = "",
          hooks: runtime.Hooks = runtime.Hooks(),
          llm: llm_mod.LLM | None = None,
          state: dict | None = None) -> list[runtime.Turn]:
    """One stage: every agent in its cast, in order, on the same board.

    The board is reloaded between members for the reason `run` reloads between stages -- an
    agent that replaced `board.data` rather than mutating it would otherwise hand the next one
    a stale object -- and it is what makes the board the message passing. Nobody is handed
    anybody's output; everyone reads the board the one before it left.

    A member that fails does not take the stage with it. These are separate specialists and the
    storyboard is still worth having when the design pass fell over; what would be lost by
    stopping is every member after the one that broke.
    """
    if name not in STAGE_CAST:
        raise ValueError(f"no stage called {name!r}. Stages: {', '.join(STAGES)}")
    shared = state if state is not None else {}
    turns: list[runtime.Turn] = []
    for role, who in zip(STAGE_CAST[name], cast_for(name, board)):
        if hooks.stopping():
            hooks.say("[crew] cancelled")
            break
        lens = CHECKERS.get(role) if _is_check(name, role) else None
        if lens and not _checkable(board):
            hooks.say(f"[crew] {who}: nothing to check yet")
            continue
        message = _brief(name, role, lens, note)
        hooks.say(f"[crew] {name}/{who}")
        try:
            turns.append(one(who, board, message, hooks=hooks, llm=llm, state=shared))
        except (skills.SkillError, llm_mod.LLMError) as failed:
            # Logged and stepped over rather than raised. See the docstring: the rest of the
            # cast is still worth running, and the job log is where a director looks.
            hooks.say(f"[crew] {who} failed: {failed}")
        board = board_mod.Board.load(board.slug)
    return turns


def start(concept: str, *, beats: int = 4, seconds: float = config.BEAT_LENGTHS[-1],
          medium: str | None = None,
          hooks: runtime.Hooks = runtime.Hooks(),
          llm: llm_mod.LLM | None = None) -> board_mod.Board:
    """Mint a board from a concept and run the script stage on it.

    The empty board comes from `develop.start`, which is what `POST /api/reels/develop` uses:
    the board exists from the first message so the conversation has somewhere to live, and
    `data["chat"]` is that conversation rather than two synthetic turns written afterwards.

    `medium` is written onto the board BEFORE the script stage runs, and only when it is not the
    default. Before, because the writer is shown the medium in its digest and the brief it works
    from has this medium's physics spliced into section 4 -- paper's rules produce a stiff clay
    film. Only when non-default, so a paper reel's document is byte-identical to what it was.
    """
    board = develop.start(concept)
    if medium and config.medium(medium).key != config.DEFAULT_MEDIUM:
        board.data["medium"] = config.medium(medium).key
        board.save()
    hooks.say(f"[crew] new reel {board.slug} in {board.look().name}")
    hooks.changed()
    stage("script", board,
          note=(f"The director wants about {beats} beats at {seconds:g} seconds each. "
                f"The film is: {concept}"),
          hooks=hooks, llm=llm)
    return board_mod.Board.load(board.slug)


def run(board: board_mod.Board, *, note: str = "", stop_after: str | None = None,
        hooks: runtime.Hooks = runtime.Hooks(),
        llm: llm_mod.LLM | None = None) -> list[runtime.Turn]:
    """Walk the board forward until nothing is left that does not cost GPU money.

    `state` is shared across every agent of every stage on purpose: it carries the still
    budget, and a budget that reset between agents would not be one.
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
        where = next_stage(board)
        if where is None:
            hooks.say("[crew] nothing left that does not cost money to render")
            break
        if where == done:
            # `next_stage` is a pure read, so a stage that ran and left the board in the same
            # place would be started again forever. No automatic retry is offered: the log says
            # what happened and the director decides, which is the same judgement
            # `planner.review` makes about a review that came back wrong.
            hooks.say(f"[crew] {where} ran and the board did not move; stopping")
            break
        turns += stage(where, board, note=note, hooks=hooks, llm=llm, state=shared)
        done = where
        # Reloaded rather than reused: every `api.py` handler loads the board per job for the
        # same reason, and it guards against an agent that replaced `board.data` wholesale.
        board = board_mod.Board.load(board.slug)
        if stop_after == where:
            hooks.say(f"[crew] stopping after {where}, as asked")
            break
    return turns


def _is_check(stage_name: str, role: str) -> bool:
    """Is this cast member here to check rather than to make?

    Keyed on the pair and not on the role alone, which is the whole reason this is a function.
    The style artist MAKES on the script and storyboard stages and CHECKS on the assets stage;
    so does the script writer. A role is not a job -- a stage plus a role is.
    """
    return stage_name == "assets" and role in CHECKERS


def _checkable(board: board_mod.Board) -> bool:
    """Is there anything on this board for a checker to look at?

    A checker with no still renders nothing and reports nothing, so running one is a wasted
    model turn rather than an error -- it is skipped with a line in the log.
    """
    return any(board.asset_path(beat["n"]).is_file() for beat in board.ordered_beats())


def _brief(stage_name: str, role: str, lens: str | None, note: str) -> str:
    if lens:
        body = CHECK_BRIEF.format(lens=lens)
    else:
        body = BRIEF_FOR.get((stage_name, role), "Do what this board needs next.")
    return body + (f"\n\nThe director says: {note.strip()}" if note.strip() else "")


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


def plan_of(board: board_mod.Board | None) -> list[dict]:
    """What the crew would do to this board, without doing any of it. Calls nothing.

    Free, and it is what `--where` prints in long form: the stages left, who works each, and
    which of them are there to check rather than to make.
    """
    where = next_stage(board)
    if where is None:
        return []
    remaining = STAGES[STAGES.index(where):]
    return [{"stage": name,
             "cast": [{"agent": who,
                       "lens": CHECKERS.get(role) if _is_check(name, role) else None}
                      for role, who in zip(STAGE_CAST[name], cast_for(name, board))]}
            for name in remaining]


# Re-exported so a caller that wants to look at one still without an agent -- the studio, a
# script, a director at a REPL -- reaches the same three lenses the crew uses rather than a
# second copy of the question.
lenses = critique.lenses
