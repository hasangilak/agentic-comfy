"""The agents a reel needs, in stages, and the rule that says which stage is next.

The orchestration here is a state machine over derived state, not a graph framework and not a
model deciding. Both of those were considered and both would be a second authority over
something this repo already has exactly one of: `jobs.Runner` owns durable serial execution,
`storyboard.json` owns state, and `Board.states()` owns what is finished. A checkpointer would
be a second store of the same state -- the drift the whole derived-state design exists to
prevent -- and a director agent would spend a model turn per decision on a question four `if`
statements answer for nothing.

    script      no beats yet          script-writer, then the style artist
    storyboard  no panel written      style, character-sheet, set-designer, mise, coherence,
                                      continuity, panels
    assets      beats waiting on a still   asset-maker, then three agents check its work
    None        nothing left that does not cost GPU money

**A stage is a cast, not an agent**, and that is the shape of the whole thing. A film crew is
several specialists on one scene rather than one generalist per phase, and the failure it
prevents here is specific: a single agent asked to write the story AND fix the material AND
block the frame answers about whichever of the three it noticed first. Each member of a cast
runs in turn on the same board, reading what the one before it left -- the board IS the
message passing, which is why no agent needs to be handed another's output.

**A phase is a slice of a cast that stops at a gate.** Storyboard and assets are long enough
that running the whole cast in one job never gives the director a moment to approve sheets or
seams before the next specialist builds on them. Default crew work therefore runs one phase
and writes `data["crew"]` so the studio can show the gate; `ungated` is the escape hatch that
burns through a stage the way this module used to. The cursor is intentional workflow state --
like `chat`, not like a fingerprint -- and it is in no render hash.

The assets cast is the one with a shape of its own: the asset-maker renders, and then three
agents look at what came back through three different lenses -- craft, staging, story. They
**report and suggest; they never re-render.** See `critique.py` for why that bound is where it
is. Three vision calls per still, once, is the cost. Their verdicts do feed back, through the
board rather than a message: an inspect phase that leaves a standing failure points `awaiting`
back at the stills phase, and the asset-maker's next brief quotes those verdicts. The director
approves that re-run like any other gate, so the checkers stay advisory and the one place a
re-render is decided is still the maker's turn.

That last row of the table is the design. There is no fourth stage in `STAGES` and no cast a
fourth could resolve through, so "the crew is finished" and "only the paid stage is left" are
literally the same value. The studio's own `resolveStage` answers `"studio"` where this answers
`None`, and the difference is the whole boundary this module is drawn around. Two phases can
still be awaiting after `next_stage` has answered None: `inspect`, because stills exist so the
stage read says "studio" but the checkers have not run yet -- and `stills` again, when inspect
left standing failures and reopened the gate for the maker.
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
#   storyboard  the style artist first (bible + medium), then character-sheet (cast locks),
#               then set-designer (place locks), then mise-en-scene binds, then coherence
#               reconciles action/blocking/still fights, then continuity audits seams --
#               and the panels come last because `panels._digest` names the designs a beat
#               binds, so a panel written before the binding is a panel written about a cast
#               it could not see.
#   assets      the maker first and the three checkers after, which is the only order that
#               makes sense for a check.
STAGE_CAST: dict[str, tuple[str, ...]] = {
    "script": ("script-writer", STYLE),
    "storyboard": (STYLE, "character-sheet", "set-designer", "mise-en-scene",
                   "coherence", "continuity", "storyboarder"),
    "assets": ("asset-maker", STYLE, "mise-en-scene", "script-writer"),
}

# Named gates inside a stage. Each phase is a contiguous slice of `STAGE_CAST` for that stage
# -- never a second cast table -- so adding a specialist means editing STAGE_CAST and the
# slice that should include it. Script has one phase (no mid-stage gate): the consistency
# problem the gates exist for lives on storyboard and assets.
STAGE_PHASES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "script": (
        ("script", ("script-writer", STYLE)),
    ),
    "storyboard": (
        ("designs", (STYLE, "character-sheet", "set-designer")),
        ("seams", ("mise-en-scene", "coherence", "continuity")),
        ("panels", ("storyboarder",)),
    ),
    "assets": (
        ("stills", ("asset-maker",)),
        ("inspect", (STYLE, "mise-en-scene", "script-writer")),
    ),
}

PHASES: tuple[str, ...] = tuple(
    phase for stage in STAGES for phase, _roles in STAGE_PHASES[stage]
)
PHASE_STAGE: dict[str, str] = {
    phase: stage for stage, slices in STAGE_PHASES.items() for phase, _roles in slices
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
    ("storyboard", STYLE): ("Set the medium if needed and polish this reel's style bible. "
                            "Do not mint or draw designs -- characters belong to "
                            "character-sheet, environments to set-designer."),
    ("storyboard", "character-sheet"): ("Develop every recurring character as an identity-lock "
                                        "design sheet from the script and style bible, draw "
                                        "each sheet once, and bind them into the beats that "
                                        "feature them."),
    ("storyboard", "set-designer"): ("Develop every recurring environment as a place-lock "
                                     "design sheet from the script and style bible, draw each "
                                     "sheet once, and bind them into the beats that use them."),
    ("storyboard", "mise-en-scene"): ("Block every shot: what each frame holds and where "
                                      "everything stands in it. Bind the designs each shot "
                                      "contains."),
    ("storyboard", "coherence"): ("Audit action against blocking, asset prompts and look-only "
                                  "design notes for fights that make the video model walk in "
                                  "place or invent idle prop motion. Fix what fails, then "
                                  "re-audit."),
    ("storyboard", "continuity"): ("Audit every chain and bridge seam: identical scene lines "
                                   "within a shot, continuity phrases, bridge landings, no "
                                   "three pure chains, no shot past 20 seconds. Fix what fails."),
    ("storyboard", "storyboarder"): ("Storyboard every shot. The designs, blocking and seams "
                                     "are already on the board -- write the shot grammar and "
                                     "draw the panels."),
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
    return [_resolve(name, board) for name in STAGE_CAST.get(stage, ())]


def roles_for_phase(phase: str) -> tuple[str, ...]:
    """The STAGE_CAST roles that make up one named phase."""
    stage = PHASE_STAGE.get(phase)
    if stage is None:
        raise ValueError(f"no phase called {phase!r}. Phases: {', '.join(PHASES)}")
    for name, roles in STAGE_PHASES[stage]:
        if name == phase:
            return roles
    raise ValueError(f"no phase called {phase!r}. Phases: {', '.join(PHASES)}")


def cast_for_phase(phase: str, board: board_mod.Board | None) -> list[str]:
    """Skill names for one phase, style role resolved."""
    return [_resolve(name, board) for name in roles_for_phase(phase)]


def phases_for(stage: str) -> list[str]:
    """Phase ids for a stage, in order."""
    return [name for name, _roles in STAGE_PHASES.get(stage, ())]


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


def crew_record(board: board_mod.Board | None) -> dict:
    """The persisted phase cursor, or empty when the board never started a gated run.

    Absent means default -- same rule as medium. Not derived render state and not in any
    fingerprint: editing it re-prices nothing.
    """
    if board is None:
        return {"done": [], "awaiting": None}
    raw = board.data.get("crew")
    if not isinstance(raw, dict):
        return {"done": [], "awaiting": None}
    done = [str(item) for item in (raw.get("done") or []) if str(item) in PHASE_STAGE]
    awaiting = raw.get("awaiting")
    awaiting = str(awaiting) if awaiting in PHASE_STAGE else None
    return {"done": done, "awaiting": awaiting}


def awaiting_phase(board: board_mod.Board | None) -> str | None:
    """Which phase the director should run or approve next.

    Prefers an explicit `awaiting` on the board -- including the two that outlive
    `next_stage`: `inspect` after the stills landed, and `stills` again after inspect
    reopened it over standing failures. Otherwise the first incomplete phase of the current
    stage. Boards that never gated start at the first phase of `next_stage`.
    """
    record = crew_record(board)
    if record["awaiting"] is not None:
        return record["awaiting"]
    where = next_stage(board)
    if where is None:
        return None
    done = set(record["done"])
    for phase in phases_for(where):
        if phase not in done:
            return phase
    return None


def write_crew(board: board_mod.Board, *, done: list[str], awaiting: str | None) -> None:
    """Persist the phase cursor. Workflow state only -- never fingerprinted.

    Deletes the key when both are empty so a board that finished every gate looks like one
    that never started, the same way setting medium back to paper deletes the key.
    """
    clean_done = [phase for phase in done if phase in PHASE_STAGE]
    clean_await = awaiting if awaiting in PHASE_STAGE else None
    if not clean_done and clean_await is None:
        board.data.pop("crew", None)
    else:
        board.data["crew"] = {"done": clean_done, "awaiting": clean_await}
    board.save()


def mark_phase_done(board: board_mod.Board, phase: str) -> None:
    """Record that a phase finished and point awaiting at the next gate (or clear it).

    Re-running an earlier phase drops every later phase from `done` -- sheets redrawn after
    seams ran would otherwise leave a stale "seams done" flag over a board that no longer
    matches what continuity audited.
    """
    if phase not in PHASE_STAGE:
        raise ValueError(f"no phase called {phase!r}. Phases: {', '.join(PHASES)}")
    stage = PHASE_STAGE[phase]
    order = phases_for(stage)
    index = order.index(phase)
    # Keep phases from other stages (e.g. storyboard done while on assets), then this stage
    # up through the one just finished.
    other = [item for item in crew_record(board)["done"] if PHASE_STAGE.get(item) != stage]
    done = other + order[: index + 1]
    awaiting = order[index + 1] if index + 1 < len(order) else None
    write_crew(board, done=done, awaiting=awaiting)


def reopen_phase(board: board_mod.Board, phase: str) -> None:
    """Point `awaiting` back at an earlier phase, dropping it and everything after from `done`.

    The inspect -> stills back-edge is the caller: a standing checker failure is work for the
    asset-maker, and a cursor that says "assets is finished" over verdicts that just failed is
    the studio lying about what the checkers said. This moves the gate and runs nothing -- the
    director approves the re-run like any other phase, which is what keeps the loop from being
    autonomous: each pass around it costs one explicit approval.
    """
    if phase not in PHASE_STAGE:
        raise ValueError(f"no phase called {phase!r}. Phases: {', '.join(PHASES)}")
    stage_name = PHASE_STAGE[phase]
    order = phases_for(stage_name)
    index = order.index(phase)
    other = [item for item in crew_record(board)["done"] if PHASE_STAGE.get(item) != stage_name]
    write_crew(board, done=other + order[:index], awaiting=phase)


def mark_stage_done(board: board_mod.Board, stage: str) -> None:
    """Mark every phase of a stage complete -- what an ungated stage run leaves behind."""
    if stage not in STAGE_PHASES:
        raise ValueError(f"no stage called {stage!r}. Stages: {', '.join(STAGES)}")
    order = phases_for(stage)
    other = [item for item in crew_record(board)["done"] if PHASE_STAGE.get(item) != stage]
    write_crew(board, done=other + order, awaiting=None)


def one(name: str, board: board_mod.Board | None, message: str, *,
        hooks: runtime.Hooks = runtime.Hooks(),
        llm: llm_mod.LLM | None = None,
        state: dict | None = None,
        via_director: bool = False,
        collector: runtime.ActivityCollector | None = None) -> runtime.Turn:
    """One named agent, one message, no orchestration.

    The transcript is written here rather than inside `runtime.run` because a turn with no
    board -- the script agent before it has written anything -- has nowhere to write one, and a
    loop that sometimes records and sometimes does not is worse than a caller that decides.

    When `via_director` is set, the specialist's reply lands in the director's activity tree
    rather than as a top-level chat turn -- the director synthesizes for the user afterwards.
    """
    agent = runtime.build(name, llm=llm)
    text = prelude(board)
    hooks.say(f"[{name}] {message.strip()[:120]}")
    trace = collector if collector is not None else hooks.track()
    event_id = trace.start("agent_start", agent=name, summary=message.strip()[:120])
    hooks.doing(f"director · delegating {name}" if via_director else name)
    try:
        turn = runtime.run(agent, message, board=board, prelude=text, hooks=hooks,
                           state=state, collector=trace)
    except (skills.SkillError, llm_mod.LLMError) as failed:
        trace.finish(event_id, status="failed", summary=str(failed))
        raise
    trace.finish(event_id, status="done", summary=turn.reply[:200] if turn.reply else "done")
    if turn.board is not None and not via_director:
        runtime.remember(turn.board, name, message, turn)
        hooks.changed()
    elif turn.board is not None:
        hooks.changed()
    return turn


def stage(name: str, board: board_mod.Board, *, note: str = "",
          phase: str | None = None,
          hooks: runtime.Hooks = runtime.Hooks(),
          llm: llm_mod.LLM | None = None,
          state: dict | None = None,
          via_director: bool = False,
          collector: runtime.ActivityCollector | None = None) -> list[runtime.Turn]:
    """One stage -- or one phase of it -- every agent in that slice, in order.

    `phase` narrows the cast to that gate's slice and writes the crew cursor when it finishes.
    Without it the whole stage runs (ungated) and every phase is marked done.

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
    if phase is not None:
        if phase not in PHASE_STAGE:
            raise ValueError(f"no phase called {phase!r}. Phases: {', '.join(PHASES)}")
        if PHASE_STAGE[phase] != name:
            raise ValueError(f"phase {phase!r} belongs to {PHASE_STAGE[phase]}, not {name}")
        roles = roles_for_phase(phase)
    else:
        roles = STAGE_CAST[name]
    shared = state if state is not None else {}
    turns: list[runtime.Turn] = []
    for role in roles:
        if hooks.stopping():
            hooks.say("[crew] cancelled")
            break
        who = _resolve(role, board)
        lens = CHECKERS.get(role) if _is_check(name, role) else None
        if lens and not _checkable(board):
            hooks.say(f"[crew] {who}: nothing to check yet")
            continue
        message = _brief(name, role, lens, note, board)
        label = f"{name} · {phase} · {who}" if phase else f"{name} · {who}"
        hooks.say(f"[crew] {name}/{who}" + (f" ({phase})" if phase else ""))
        hooks.doing(label + (f" · {lens}" if lens else ""))
        try:
            turns.append(one(who, board, message, hooks=hooks, llm=llm, state=shared,
                             via_director=via_director, collector=collector))
        except (skills.SkillError, llm_mod.LLMError) as failed:
            # Logged and stepped over rather than raised. See the docstring: the rest of the
            # cast is still worth running, and the job log is where a director looks.
            hooks.say(f"[crew] {who} failed: {failed}")
            trace = collector if collector is not None else hooks.track()
            trace.note("agent_failed", agent=who, status="failed", summary=str(failed))
        board = board_mod.Board.load(board.slug)
    if not hooks.stopping():
        if phase == "inspect" and critique.failing(board):
            # The back-edge: standing failures are the asset-maker's work, so the gate moves
            # back to stills rather than declaring the stage finished over them. Gated only --
            # an ungated run keeps burning through, which is what ungated means.
            reopen_phase(board, "stills")
            hooks.say("[crew] inspect left standing failures; awaiting stills again")
        elif phase is not None:
            mark_phase_done(board, phase)
            hooks.say(f"[crew] phase {phase} done; awaiting {crew_record(board)['awaiting'] or 'nothing'}")
        else:
            mark_stage_done(board, name)
            hooks.say(f"[crew] stage {name} done ungated")
        hooks.changed()
    return turns


def run_phase(board: board_mod.Board, phase: str | None = None, *, note: str = "",
              hooks: runtime.Hooks = runtime.Hooks(),
              llm: llm_mod.LLM | None = None,
              state: dict | None = None,
              via_director: bool = False,
              collector: runtime.ActivityCollector | None = None) -> list[runtime.Turn]:
    """Run one phase and stop at its gate. Default phase is whatever `awaiting_phase` says."""
    target = phase or awaiting_phase(board)
    if target is None:
        hooks.say("[crew] nothing left that does not cost money to render")
        return []
    if target not in PHASE_STAGE:
        raise ValueError(f"no phase called {target!r}. Phases: {', '.join(PHASES)}")
    return stage(PHASE_STAGE[target], board, note=note, phase=target, hooks=hooks, llm=llm,
                 state=state, via_director=via_director, collector=collector)


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
        phase: str | None = None, ungated: bool = False,
        hooks: runtime.Hooks = runtime.Hooks(),
        llm: llm_mod.LLM | None = None) -> list[runtime.Turn]:
    """Walk the board forward.

    Default (gated): run the next awaiting phase and stop so the director can approve.
    `ungated=True`: run whole stages until money, optionally stopping after `stop_after`.
    `phase=...`: run exactly that phase, gated, regardless of awaiting.
    """
    if stop_after is not None and stop_after not in STAGES:
        raise ValueError(f"stop_after has to be one of {', '.join(STAGES)}")
    if phase is not None and phase not in PHASE_STAGE:
        raise ValueError(f"no phase called {phase!r}. Phases: {', '.join(PHASES)}")
    shared: dict = {}
    if not ungated:
        return run_phase(board, phase, note=note, hooks=hooks, llm=llm, state=shared)

    turns: list[runtime.Turn] = []
    done: str | None = None
    while True:
        if hooks.stopping():
            hooks.say("[crew] cancelled")
            break
        where = next_stage(board)
        if where is None:
            # An assets phase can still be awaiting after stills cleared assets_needed:
            # inspect after the stills landed, or stills again after inspect reopened it.
            # One phase, then stop -- looping here would let a checker that always fails
            # ping-pong stills and inspect until the budget starved the maker into a loop
            # of refusals, with nobody approving any of it.
            leftover = awaiting_phase(board)
            if leftover is not None and PHASE_STAGE[leftover] == "assets":
                turns += run_phase(board, leftover, note=note, hooks=hooks, llm=llm,
                                   state=shared)
                board = board_mod.Board.load(board.slug)
            else:
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


def _resolve(role: str, board: board_mod.Board | None) -> str:
    return style_artist(board) if role is STYLE or role == STYLE else role


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


def _brief(stage_name: str, role: str, lens: str | None, note: str,
           board: board_mod.Board | None = None) -> str:
    if lens:
        body = CHECK_BRIEF.format(lens=lens)
    else:
        body = BRIEF_FOR.get((stage_name, role), "Do what this board needs next.")
    if stage_name == "assets" and role == "asset-maker" and board is not None:
        # The checkers' feedback reaches the maker here, as quoted verdicts in the brief --
        # the board is still the message passing, this just reads it out loud. Only standing
        # failures (the latest verdict per beat and lens): a fail that was fixed and
        # re-inspected to a pass would send the maker un-fixing the fix.
        standing = critique.failing_report(board)
        if standing:
            body += (
                "\n\nThe inspectors failed these stills on their last pass. Each names a "
                "problem and a suggested fix. The re-render decision is yours: fix the prompt "
                "first, then render that beat once. A fix that needs the blocking or the "
                "story changed is not yours to make -- say so in your reply and leave it.\n"
                + standing
            )
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

    Free, and it is what `--where` prints in long form: the stages left, who works each, which
    of them are there to check, and how the cast is sliced into gated phases. `awaiting` is the
    next phase the UI should offer; `inspect` can still appear after `next_stage` is None.
    """
    where = next_stage(board)
    record = crew_record(board)
    awaiting = awaiting_phase(board)
    remaining: list[str] = []
    if where is not None:
        remaining = list(STAGES[STAGES.index(where):])
    elif awaiting is not None:
        # A phase can still be awaiting after `next_stage` answers None: inspect once stills
        # exist, and stills again when inspect reopened it over standing failures.
        remaining = [PHASE_STAGE[awaiting]]
    plan = []
    for name in remaining:
        phases = []
        for phase, roles in STAGE_PHASES[name]:
            agents = []
            for role in roles:
                who = _resolve(role, board)
                lens = CHECKERS.get(role) if _is_check(name, role) else None
                agents.append({"agent": who, "lens": lens})
            status = (
                "done" if phase in record["done"]
                else "awaiting" if phase == awaiting
                else "pending"
            )
            phases.append({"id": phase, "agents": agents, "status": status})
        plan.append({
            "stage": name,
            "cast": [
                {"agent": who,
                 "lens": CHECKERS.get(role) if _is_check(name, role) else None}
                for role, who in zip(STAGE_CAST[name], cast_for(name, board))
            ],
            "phases": phases,
        })
    return plan


def plan_summary(board: board_mod.Board | None) -> dict:
    """Plan plus the cursor fields the studio reads once per fetch."""
    return {
        "stage": next_stage(board),
        "awaiting": awaiting_phase(board),
        "done": crew_record(board)["done"],
        "plan": plan_of(board),
        "phases": list(PHASES),
    }


# Re-exported so a caller that wants to look at one still without an agent -- the studio, a
# script, a director at a REPL -- reaches the same three lenses the crew uses rather than a
# second copy of the question.
lenses = critique.lenses
