# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow", "numpy", "scipy", "httpx", "imageio-ffmpeg"]
# ///
"""Concept -> script -> assets -> chained clips -> one stitched Reel.

Planning and asset generation go through the Antigravity CLI and cost no money; only
--render touches a GPU. The stages are separable so you can iterate for free and pay once.

    uv run storyboard.py --concept "a paper pig finds a pond" --beats 4 --seconds 10
    uv run storyboard.py --name <slug> --assets
    uv run storyboard.py --name <slug> --render --chain

Everything lands in reels/<slug>/, including a storyboard.json you can hand-edit between
stages. Re-running skips work that is already done.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from paperreel import config, pipeline, planner


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "reel"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--concept", help="what the reel is about; runs the planner")
    parser.add_argument("--beats", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=10.0, help="per beat")
    parser.add_argument("--assets", action="store_true", help="generate the still frames")
    parser.add_argument("--render", action="store_true", help="the paid stage")
    parser.add_argument("--all", action="store_true", help="plan + assets + render")
    parser.add_argument("--draft", action="store_true",
                        help=f"{config.DRAFT_SECONDS:.0f}s beats -- cheap approval pass")
    parser.add_argument("--chain", action="store_true", default=True,
                        help="continue each beat from the previous clip (default)")
    parser.add_argument("--scenes", dest="chain", action="store_false",
                        help="independent shots; needs one asset per beat")
    parser.add_argument("--steps", type=int, default=config.DEFAULT_STEPS)
    parser.add_argument("--seed", type=int, default=1101)
    parser.add_argument("--mute", action="store_true")
    parser.add_argument("--name", help="reuse an existing reels/<name> directory")
    parser.add_argument("--keep-app", action="store_true", help="leave the app deployed")
    args = parser.parse_args()

    if args.draft:
        args.seconds = min(args.seconds, config.DRAFT_SECONDS)

    do_plan = bool(args.concept)
    do_assets = args.assets or args.all
    do_render = args.render or args.all
    if not (do_plan or do_assets or do_render):
        parser.error("give --concept, and/or --assets / --render / --all")

    name = args.name or (slugify(args.concept) if args.concept else None)
    if name is None:
        parser.error("--render without --concept needs --name to find the storyboard")
    workdir = config.ROOT / "reels" / name
    workdir.mkdir(parents=True, exist_ok=True)
    board_path = workdir / "storyboard.json"

    if do_plan:
        print(f"[plan] {args.beats} beats x {args.seconds:.0f}s via {config.PLANNER_MODEL}")
        board = planner.plan(args.concept, args.beats, args.seconds, workdir)
        board_path.write_text(json.dumps(board, indent=2))
        print(f'[plan] "{board["title"]}" -> {board_path}')
        for beat in board["beats"]:
            print(f"       {beat['n']}. {beat['scene']}")
    else:
        if not board_path.exists():
            raise SystemExit(f"no storyboard at {board_path}; run with --concept first")
        board = json.loads(board_path.read_text())

    if do_assets:
        # Chaining needs only the opening frame, which is what keeps a reel inside the
        # tight image quota. Scene mode needs one asset per beat.
        wanted = board["beats"][:1] if args.chain else board["beats"]
        for beat in wanted:
            out = workdir / f"beat{beat['n']}_asset.png"
            if out.exists():
                print(f"[asset] beat {beat['n']}: already present, skipping")
                continue
            print(f"[asset] beat {beat['n']}: generating")
            try:
                planner.generate_asset(beat, board["style_bible"], out, workdir)
            except planner.QuotaExhausted as exhausted:
                raise SystemExit(f"[asset] {exhausted}")
        print(f"[asset] assets in {workdir}")

    if do_render:
        suffix = "draft" if args.draft else "final"
        result = pipeline.render_reel(
            board, workdir,
            seconds=args.seconds, steps=args.steps, seed=args.seed,
            chain=args.chain, mute=args.mute, manage_app=not args.keep_app,
            out_name=f"{name}_{suffix}",
        )
        print(f"[done] {result.seconds_of_video:.0f}s of video for ${result.cost:.2f}")


if __name__ == "__main__":
    main()
