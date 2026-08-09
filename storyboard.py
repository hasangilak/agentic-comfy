# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow", "numpy", "scipy", "httpx", "imageio-ffmpeg"]
# ///
"""Concept -> script -> assets -> chained clips -> one stitched Reel.

Planning runs on a local model through Ollama and the stills on the local image server; both
are free. Only --render touches a GPU. The stages are separable so you can iterate for free
and pay once.

    uv run storyboard.py --concept "a paper pig finds a pond" --beats 4 --seconds 10
    uv run storyboard.py --script story.json          # your own script, no planner turn
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

from paperreel import board as board_mod
from paperreel import config, panels, papercut, pipeline, planner, qwen, script as script_mod
from paperreel import stills as stills_mod


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "reel"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--concept", help="what the reel is about; runs the planner")
    parser.add_argument("--script", type=Path,
                        help="a script you wrote yourself (JSON); skips the planner entirely")
    parser.add_argument("--beats", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=10.0, help="per beat")
    parser.add_argument("--panels", action="store_true",
                        help="write and draw the storyboard: one rough sketch per shot, plus a "
                             "contact sheet. The cheapest model, and nothing it makes is rendered "
                             "from")
    parser.add_argument("--assets", action="store_true", help="generate the still frames")
    parser.add_argument("--render", action="store_true", help="the paid stage")
    parser.add_argument("--all", action="store_true", help="plan + assets + render")
    parser.add_argument("--draft", action="store_true",
                        help=f"{config.DRAFT_SECONDS:.0f}s beats -- cheap approval pass")
    parser.add_argument("--chain", action="store_true", default=True,
                        help="honour each beat's own join, continuing from the previous clip "
                             "where the board does not say otherwise (default)")
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

    if args.concept and args.script:
        parser.error("--concept and --script both write the storyboard; pick one")

    do_plan = bool(args.concept)
    do_panels = args.panels
    do_assets = args.assets or args.all
    do_render = args.render or args.all
    # Deliberately not part of --all. A storyboard is a stage you stop at and look at, and folding
    # it into the one flag that also pays for a render would have it drawn on the way past.
    if not (do_plan or args.script or do_panels or do_assets or do_render):
        parser.error("give --concept or --script, and/or --panels / --assets / --render / --all")

    # Read and check the supplied script before anything makes a directory for it, so a
    # typo in the JSON does not leave an empty reel behind.
    adopted = None
    if args.script:
        try:
            adopted = script_mod.normalise(script_mod.parse(args.script.read_text()))
        except (OSError, script_mod.BadScript) as bad:
            raise SystemExit(f"[script] {bad}")

    name = args.name
    if name is None and args.concept:
        name = slugify(args.concept)
    if name is None and adopted is not None:
        # free_slug rather than slugify: an import must not land on a reel that may already
        # hold paid renders. Pass --name to write into an existing directory deliberately.
        name = script_mod.free_slug(slugify(adopted["title"] or adopted["concept"]))
    if name is None:
        parser.error("--render without --concept or --script needs --name to find the storyboard")
    workdir = config.ROOT / "reels" / name
    workdir.mkdir(parents=True, exist_ok=True)
    board_path = workdir / "storyboard.json"

    if adopted is not None:
        board = adopted
        board_path.write_text(json.dumps(board, indent=2))
        total = sum(beat["seconds"] for beat in board["beats"])
        print(f'[script] "{board["title"]}" -> {board_path} '
              f'({len(board["beats"])} beats, {total:.0f}s)')
        for beat in board["beats"]:
            print(f"       {beat['n']}. [{beat['source']}, {beat['seconds']:.0f}s] {beat['scene']}")
        for note in script_mod.notes(board):
            print(f"[script] {note}")
        lengths = {beat["seconds"] for beat in board["beats"]}
        if len(lengths) > 1:
            # --render applies one --seconds to the whole reel; only the studio renders a
            # board beat by beat at the length each one asks for. The joins ARE honoured.
            print("[script] this script mixes beat lengths, which --render flattens to one "
                  "--seconds. Open it in the studio to render it as written.")
    elif do_plan:
        print(f"[plan] {args.beats} beats x {args.seconds:.0f}s via {config.QWEN_MODEL}")
        plan = planner.plan(args.concept, args.beats, args.seconds)
        plan["seconds"] = args.seconds
        # Through the same normaliser an imported script takes, because both are now written
        # against the same brief: numbers compacted, lengths snapped, beat 1 forced onto its
        # own still, and a beat with no action refused rather than written to disk.
        board = script_mod.normalise(plan)
        board_path.write_text(json.dumps(board, indent=2))
        print(f'[plan] "{board["title"]}" -> {board_path}')
        for beat in board["beats"]:
            print(f"       {beat['n']}. [{beat['source']}, {beat['seconds']:.0f}s] {beat['scene']}")
        for note in script_mod.notes(board):
            print(f"[plan] {note}")
    else:
        if not board_path.exists():
            raise SystemExit(f"no storyboard at {board_path}; run with --concept first")
        board = json.loads(board_path.read_text())

    if do_panels:
        # Before the stills, because that is the order the stage exists for: the sequence is
        # judged as sketches, and only then is anything drawn that a paid render will use. Panels
        # are written for every beat that has none and drawn for every one that has text and no
        # sketch, so re-running is cheap and skips what is done -- same promise as the rest of this
        # script.
        live = board_mod.Board(slug=name, path=board_path, data=board)
        blank = [b["n"] for b in live.ordered_beats() if not str(b.get("panel") or "").strip()]
        try:
            if blank:
                panels.write(live, blank, log=print)
            else:
                print("[panel] every scene already has its shot written; drawing")
            panels.draw_all(live, log=print)
        except (panels.PanelsError, papercut.PapercutError, qwen.OllamaError) as gone:
            raise SystemExit(f"[panel] {gone}")
        sheet = live.sheet_path()
        print(f"[panel] {sheet}" if sheet.is_file() else "[panel] no panels drawn")

    if do_assets:
        # A board that names a source per beat -- one written in the studio, or an adopted
        # script -- has already said which beats are cuts, so generate exactly those stills
        # rather than one or all. Otherwise: chaining needs only the opening frame, which is
        # what keeps a reel inside the tight image quota, and scene mode needs one per beat.
        if any(beat.get("source") for beat in board["beats"]):
            # Every join but a plain continuation, which is the only one with nowhere to put a
            # still. A bridge lands on it rather than opening on it, and a reference beat takes
            # it as <Picture 1> rather than as a keyframe, but each is just as much a file that
            # has to exist first.
            wanted = [b for b in board["beats"]
                      if (b.get("source") or board_mod.SOURCE_CHAIN) != board_mod.SOURCE_CHAIN]
        else:
            wanted = board["beats"][:1] if args.chain else board["beats"]
        def missing() -> list[dict]:
            return [b for b in wanted if not (workdir / f"beat{b['n']}_asset.png").exists()]

        for beat in wanted:
            if (workdir / f"beat{beat['n']}_asset.png").exists():
                print(f"[asset] beat {beat['n']}: already present, skipping")

        # The Gemini renderer next door is the only generator, and it
        # renders straight onto the H3 grid so nothing is cropped on the way to the video
        # model. Every still it produces is then looked at by the same local model that wrote
        # the script -- see paperreel/stills.py. With the server down there is nothing to fall
        # back to, so the stills have to be supplied by hand.
        if missing():
            # Wraps the same dict, so a prompt the review pass rewrites lands in `board` here
            # as well as on disk.
            live = board_mod.Board(slug=name, path=board_path, data=board)
            try:
                stills_mod.generate(
                    live, stills_mod.wanted(live, [b["n"] for b in missing()]), log=print,
                )
            except (stills_mod.StillsError, papercut.PapercutError) as gone:
                raise SystemExit(f"[asset] {gone}")
        short = missing()
        if short:
            raise SystemExit(
                f"[asset] beat {', '.join(str(b['n']) for b in short)} still have no still. "
                f"Drop a beat<n>_asset.png into {workdir}, or open the reel in the studio."
            )
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
