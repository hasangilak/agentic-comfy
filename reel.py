# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow", "numpy", "scipy", "httpx", "imageio-ffmpeg"]
# ///
"""Render ONE clip. For a multi-beat reel use storyboard.py.

    uv run reel.py --preview                          # compose the frame, no GPU
    uv run reel.py --prompt "the pig walks right"     # render it
    uv run reel.py --first-frame shot.png --seconds 10

Composes a 9:16 opening frame from a background plus a character, renders it through
MiniMax-H3 on Modal, and delivers 1080x1920 H.264/AAC.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from paperreel import comfy, config, media, pipeline

DEFAULT_BACKGROUND = config.ROOT / "characters" / "Gemini_Generated_Image_37jkbi37jkbi37jk.png"
DEFAULT_CHARACTER = config.ROOT / "characters" / "Gemini_Generated_Image_f17bznf17bznf17b.png"
DEFAULT_ACTION = (
    "the character walks smoothly from the centre toward the right across the foreground "
    "meadow, a believable four-beat walk cycle with alternating legs, gentle body rise and "
    "fall, small ear bounce and curled-tail sway"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--prompt", default=DEFAULT_ACTION, help="what moves in the shot")
    parser.add_argument("--background", type=Path, default=DEFAULT_BACKGROUND)
    parser.add_argument("--character", type=Path, default=DEFAULT_CHARACTER)
    parser.add_argument("--first-frame", type=Path, help="skip compositing, use this image")
    parser.add_argument("--seconds", type=float, default=5.0,
                        help=f"clip length; >{config.PROVEN_MAX_FRAMES / config.FPS:.0f}s is unproven")
    parser.add_argument("--steps", type=int, default=config.DEFAULT_STEPS)
    parser.add_argument("--seed", type=int, default=1101)
    parser.add_argument("--name", default="reel")
    parser.add_argument("--out", type=Path, default=config.ROOT / "out")
    parser.add_argument("--mute", action="store_true", help="drop H3's generated audio")
    parser.add_argument("--preview", action="store_true", help="compose only, no GPU")
    parser.add_argument("--keep-app", action="store_true", help="leave the app deployed")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    if args.first_frame:
        frame = media.fit_frame(args.first_frame, args.out / f"{args.name}_frame.png")
        print(f"[1/3] fitted first frame -> {frame}")
    else:
        frame = media.compose(args.background, args.character,
                              args.out / f"{args.name}_frame.png")
        print(f"[1/3] composed {config.GEN_WIDTH}x{config.GEN_HEIGHT} frame -> {frame}")
    if args.preview:
        print("      --preview set, stopping before the GPU stage")
        return

    length = config.frame_count(args.seconds)
    print(f"[2/3] rendering {length} frames ({length / config.FPS:.1f}s), {args.steps} steps")
    with pipeline.gpu_app(not args.keep_app):
        with comfy.client() as http:
            comfy.wake(http)
            uploaded = comfy.upload_image(http, frame)
            outputs = comfy.run_graph(http, comfy.build_graph(
                first_frame=uploaded,
                prompt=config.build_prompt(args.prompt, mute=args.mute),
                length=length, steps=args.steps, seed=args.seed,
            ))
            raw = comfy.download(http, comfy.only_video(outputs),
                                 args.out / f"{args.name}_raw.mp4")

    final = media.finish(
        raw, args.out / f"{args.name}_{config.REEL_WIDTH}x{config.REEL_HEIGHT}.mp4",
        mute=args.mute,
    )
    print(f"[3/3] Reels-ready -> {final}")


if __name__ == "__main__":
    main()
