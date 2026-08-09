# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow", "httpx"]
# ///
"""Three agents that walk a reel from a concept to stills on disk, and stop there.

    uv run crew.py --concept "a paper pig finds a pond"
    uv run crew.py --name <slug>                      # carry on from wherever it is
    uv run crew.py --name <slug> --stage storyboard   # exactly that stage, then stop
    uv run crew.py --name <slug> --agent asset-maker --note "beat 3 is too dark"

    uv run crew.py --list                             # every agent; calls nothing
    uv run crew.py --name <slug> --where              # which stage it is waiting on; free
    uv run crew.py --name <slug> --dry-run --agent storyboarder    # the prompt, unsent

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
                        help="run exactly this stage and stop")
    parser.add_argument("--through", choices=list(crew.STAGES),
                        help="run from wherever the board is, up to and including this stage")
    parser.add_argument("--agent", help="one agent by name, orchestrator bypassed")
    parser.add_argument("--beats", type=int, default=4,
                        help="how many shots, when starting from a concept")
    parser.add_argument("--seconds", type=float, default=config.BEAT_LENGTHS[-1],
                        help="how long each beat runs, when starting from a concept")
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
        print(crew.next_stage(board) or "nothing (only the render is left)")
        return 0

    if args.dry_run:
        return _dry_run(args, board)

    if not args.concept and board is None:
        parser.error("say --concept for a new reel or --name for an existing one")

    hooks = runtime.Hooks(log=lambda line: print(line, flush=True))

    if args.agent:
        if board is None:
            parser.error("--agent needs --name; an agent works on a board that exists")
        message = args.note.strip() or crew.BRIEF_FOR.get(
            _stage_of(args.agent) or "", "Do what this board needs next.")
        turn = crew.one(args.agent, board, message, hooks=hooks)
        print(f"\n{turn.agent}: {turn.reply}")
        return 0

    if args.concept:
        board = crew.start(args.concept, beats=args.beats, seconds=args.seconds, hooks=hooks)
        print(f"\nreel: {board.slug}")
        if args.stage == "script":
            return 0

    turns = crew.run(board, note=args.note, stop_after=args.stage or args.through, hooks=hooks)
    for turn in turns:
        print(f"\n{turn.agent}: {turn.reply}")
    left = crew.next_stage(board_mod.Board.load(board.slug))
    print(f"\nwaiting on: {left or 'nothing -- the render is the only thing left'}")
    return 0


def _stage_of(agent: str) -> str | None:
    for stage, name in crew.AGENT_FOR.items():
        if name == agent:
            return stage
    return None


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
    stage = args.stage or args.through or crew.next_stage(board)
    name = args.agent or crew.AGENT_FOR.get(stage or "script", "script-writer")
    agent = runtime.build(name)
    message = args.note.strip() or crew.BRIEF_FOR.get(stage or "script", "")
    print(runtime.preview(agent, message, crew.prelude(board)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (llm.LLMError, skills.SkillError) as refused:
        # Both already carry a sentence written for a director -- a missing API key, an
        # unregistered provider name, a skill file that will not parse. A traceback over one of
        # those tells the reader nothing the message did not.
        print(refused, file=sys.stderr)
        raise SystemExit(1) from None
