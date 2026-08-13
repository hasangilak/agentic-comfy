# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow", "httpx"]
# ///
"""A crew that walks a reel from a concept to stills on disk, and stops there.

Three stages, each worked by a cast rather than by one agent -- a writer and a style artist,
then the style artist, mise-en-scene extracting the roster, character-sheet and set-designer
drawing it, mise blocking, coherence, continuity and the storyboarder, then the asset maker
with three agents checking what it made through three different lenses. `paperreel/crew.py`
is the order and the reasons.

    uv run crew.py --concept "a clay pig finds a pond" --medium claymation
    uv run crew.py --name <slug>                      # next gated phase, then stop
    uv run crew.py --name <slug> --phase designs      # exactly that gate
    uv run crew.py --name <slug> --stage storyboard   # whole stage, ungated
    uv run crew.py --name <slug> --ungated            # burn through until money
    uv run crew.py --name <slug> --agent mise-en-scene --note "beat 3 feels empty"

    uv run crew.py --list                             # every agent; calls nothing
    uv run crew.py --name <slug> --where              # phases left and who works them
    uv run crew.py --name <slug> --dry-run            # every prompt of the next phase, unsent

This CLI cannot start a paid render, and the dependency list above is where that is visible:
no `imageio-ffmpeg`, so nothing here can even reach the video pipeline. `storyboard.py` keeps
--render and --all; the GPU stays a flag on that script and a button in the studio.
"""

from __future__ import annotations

import argparse
import sys

from paperreel import board as board_mod
from paperreel import config, crew, llm, runtime, skills


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--concept", help="what the reel is about; mints a new board")
    parser.add_argument("--name", help="an existing reel, by slug")
    parser.add_argument("--note", default="",
                        help="what to tell the agents this run, in your own words")
    parser.add_argument("--stage", choices=list(crew.STAGES),
                        help="run exactly this stage and stop (ungated unless --phase is set)")
    parser.add_argument("--through", choices=list(crew.STAGES),
                        help="run from wherever the board is, up to and including this stage")
    parser.add_argument("--phase", choices=list(crew.PHASES),
                        help="run one gated phase and stop for approval")
    parser.add_argument("--ungated", action="store_true",
                        help="burn through stages without pausing at gates")
    parser.add_argument("--agent", help="one agent by name, orchestrator bypassed")
    parser.add_argument("--beats", type=int, default=4,
                        help="how many shots, when starting from a concept")
    parser.add_argument("--seconds", type=float, default=config.BEAT_LENGTHS[-1],
                        help="how long each beat runs, when starting from a concept")
    parser.add_argument("--medium", choices=list(config.MEDIUMS),
                        help="what the film is made of; only meaningful with --concept")
    parser.add_argument("--where", action="store_true",
                        help="print which stage the board is waiting on, and exit")
    parser.add_argument("--list", action="store_true", dest="listing",
                        help="print every agent and exit; calls nothing")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the prompt and the tools that would be sent, and exit")
    args = parser.parse_args()

    if args.listing:
        return _list()

    if args.agent and args.stage:
        parser.error("--agent bypasses the orchestrator, so --stage means nothing with it")
    if args.stage and args.through:
        parser.error("--stage runs one stage; --through runs up to one. Pick one.")
    if args.phase and args.ungated:
        parser.error("--phase is a gated stop; --ungated skips gates. Pick one.")
    if args.phase and args.through:
        parser.error("--phase runs one gate; --through walks stages. Pick one.")
    if args.concept and args.name:
        parser.error("--concept mints a new reel and --name opens an existing one")

    board = None
    if args.name:
        try:
            board = board_mod.Board.load(args.name)
        except FileNotFoundError:
            print(f"no reel called {args.name!r} in {board_mod.reels_dir()}", file=sys.stderr)
            return 1

    if args.where:
        if board is None:
            parser.error("--where needs --name")
        summary = crew.plan_summary(board)
        if not summary["plan"] and not summary["awaiting"]:
            print("nothing (only the render is left)")
            return 0
        if summary["awaiting"]:
            print(f"awaiting     {summary['awaiting']}")
        if summary["done"]:
            print(f"done         {', '.join(summary['done'])}")
        for entry in summary["plan"]:
            for phase in entry.get("phases") or []:
                cast = ", ".join(
                    member["agent"] + (f" [{member['lens']}]" if member["lens"] else "")
                    for member in phase["agents"])
                print(f"{entry['stage']:<11} {phase['id']:<8} [{phase['status']:<8}] {cast}")
        return 0

    if args.dry_run:
        return _dry_run(args, board)

    if not args.concept and board is None:
        parser.error("say --concept for a new reel or --name for an existing one")

    hooks = runtime.Hooks(log=lambda line: print(line, flush=True))

    if args.agent:
        if board is None:
            parser.error("--agent needs --name; an agent works on a board that exists")
        message = args.note.strip() or _default_brief(args.agent, board)
        turn = crew.one(args.agent, board, message, hooks=hooks)
        print(f"\n{turn.agent}: {turn.reply}")
        return 0

    if args.concept:
        board = crew.start(args.concept, beats=args.beats, seconds=args.seconds,
                           medium=args.medium, hooks=hooks)
        print(f"\nreel: {board.slug}")
        if args.stage == "script" or args.phase == "script":
            return 0

    ungated = bool(args.ungated or ((args.stage or args.through) and not args.phase))
    turns = crew.run(board, note=args.note,
                     stop_after=args.stage or args.through if ungated else None,
                     phase=args.phase, ungated=ungated, hooks=hooks)
    for turn in turns:
        print(f"\n{turn.agent}: {turn.reply}")
    board = board_mod.Board.load(board.slug)
    left = crew.next_stage(board)
    awaiting = crew.awaiting_phase(board)
    print(f"\nwaiting on: {left or 'nothing -- the render is the only thing left'}")
    if awaiting:
        print(f"awaiting phase: {awaiting}")
    return 0


def _default_brief(agent: str, board, phase: str | None = None) -> str:
    """What the orchestrator would say to this agent, for a director who said nothing.

    Found by asking the crew rather than by a table here: an agent appears in more than one
    cast now and is told something different in each, so "which stage is this agent's" has no
    single answer and the pair (stage, role) -- plus the phase, when mise runs three times -- is
    what carries the brief.
    """
    if phase:
        stage = crew.PHASE_STAGE[phase]
        for role in crew.roles_for_phase(phase):
            who = crew._resolve(role, board)
            if who == agent:
                return crew._brief(stage, role, crew.CHECKERS.get(role)
                                   if crew._is_check(stage, role) else None, "", board,
                                   phase=phase)
    for stage in crew.STAGES:
        for role, who in zip(crew.STAGE_CAST[stage], crew.cast_for(stage, board)):
            if who == agent:
                return crew._brief(stage, role, crew.CHECKERS.get(role)
                                   if crew._is_check(stage, role) else None, "", board)
    return "Do what this board needs next."


def _list() -> int:
    """Every agent, loaded and rendered. This is the cheapest check there is and it is total.

    Loading a skill resolves every placeholder -- including the whole authoring brief off disk
    -- and building the agent resolves every tool name against the toolbox and builds every
    declaration. A typo in frontmatter, an unknown placeholder, a tool that does not exist and
    an unresolvable schema path all fail here, for nothing.
    """
    rows = skills.catalogue()
    if not rows:
        print(f"no skills in {skills.directory()}", file=sys.stderr)
        return 1
    bad = 0
    for row in rows:
        if "error" in row:
            print(f"{row['name']}: BROKEN -- {row['error']}", file=sys.stderr)
            bad += 1
            continue
        try:
            agent = runtime.build(row["name"])
        except skills.SkillError as refused:
            print(f"{row['name']}: BROKEN -- {refused}", file=sys.stderr)
            bad += 1
            continue
        print(f"{agent.skill.name}")
        print(f"    {agent.skill.description}")
        print(f"    model {agent.skill.model or config.TEXT_MODEL}   "
              f"think {agent.skill.think}   temperature {agent.skill.temperature}   "
              f"max_rounds {agent.skill.max_rounds}")
        print(f"    tools: {', '.join(agent.tools)}")
        print(f"    prompt: {len(agent.skill.system)} characters   {agent.skill.path}")
    print(f"\nprovider: {config.LLM_PROVIDER} ({llm.provider().__name__})")
    return 1 if bad else 0


def _dry_run(args, board: board_mod.Board | None) -> int:
    """Exactly what would go to the model, printed. Calls nothing.

    This is the review artifact the money guard rests on: a prompt is read before a turn is
    ever paid for, and a skill edit can be checked without spending anything.
    """
    if args.phase:
        names = [args.agent] if args.agent else crew.cast_for_phase(args.phase, board)
        phase = args.phase
    elif args.stage or args.through:
        where = args.stage or args.through
        names = [args.agent] if args.agent else crew.cast_for(where, board)
        phase = None
    else:
        # Match a gated run: the next phase, not the whole stage. Otherwise mise's three
        # storyboard jobs would all print the blocking brief.
        phase = crew.awaiting_phase(board)
        if phase:
            names = [args.agent] if args.agent else crew.cast_for_phase(phase, board)
        else:
            names = [args.agent] if args.agent else crew.cast_for(
                crew.next_stage(board) or "script", board)
    for index, name in enumerate(names):
        agent = runtime.build(name)
        if index:
            print("\n" + "=" * 78 + "\n")
        pictures = (crew.critique.context_pictures(board, phase)
                    if name == "mise-en-scene" else [])
        legend = crew.critique.picture_legend(pictures)
        text = crew.prelude(board) + ((legend + "\n\n") if legend else "")
        print(runtime.preview(agent, args.note.strip() or _default_brief(name, board, phase),
                              text, pictures=pictures or None))
        if name == "mise-en-scene" and phase == "inspect" and board is not None:
            _dry_inspect(board)
    return 0


def _dry_inspect(board) -> None:
    """The blocking vision prompt inspect_still would send, for one still that exists."""
    for beat in board.ordered_beats():
        n = beat["n"]
        if not board.asset_path(n).is_file():
            continue
        try:
            parts, paths = crew.critique.look_parts(board, n, "blocking")
        except crew.critique.InspectError:
            continue
        print("\n===== BLOCKING VISION (inspect_still, beat "
              f"{n}) =====")
        print("\n\n".join(parts))
        print("\n----- images -----")
        for index, path in enumerate(paths, start=1):
            print(f"  {index}. {path.name}")
        return


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (llm.LLMError, skills.SkillError) as refused:
        # Both already carry a sentence written for a director -- a missing API key, an
        # unregistered provider name, a skill file that will not parse. A traceback over one of
        # those tells the reader nothing the message did not.
        print(refused, file=sys.stderr)
        raise SystemExit(1) from None
