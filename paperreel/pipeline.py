"""Orchestration: app lifecycle, batch rendering, and the results a caller gets back.

Designed to be driven either by the CLIs in this repo or by a UI. Progress is reported
through an injectable `log` callback rather than printed, so a frontend can stream it.
"""

from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import comfy, config, media


@dataclass
class Beat:
    """One rendered shot."""

    n: int
    video: Path
    first_frame: Path
    seconds: float
    render_seconds: float


@dataclass
class BatchResult:
    beats: list[Beat] = field(default_factory=list)
    reel: Path | None = None
    container_seconds: float = 0.0

    @property
    def cost(self) -> float:
        """GPU + CPU + memory. Excludes cold start and the scale-down tail."""
        return config.estimate_cost(self.container_seconds)

    @property
    def seconds_of_video(self) -> float:
        return sum(b.seconds for b in self.beats)


# ## App lifecycle
#
# `modal app stop` genuinely stops the app -- the URL goes dead until the next deploy.
# Deploying costs nothing and takes ~2.5s because the image layers are cached, and no
# GPU is billed until a request actually arrives. Stopping afterwards is belt-and-braces:
# a deployed app with zero containers is free, but nothing can then ping the URL and
# respawn a GPU behind your back.


def deploy(log=print) -> None:
    log("[app] deploying")
    subprocess.run(["uvx", "modal", "deploy", str(config.APP_FILE)],
                   check=True, capture_output=True, text=True)


def stop(log=print) -> None:
    log("[app] stopping")
    subprocess.run(["uvx", "modal", "app", "stop", config.APP_NAME, "--yes"],
                   check=False, capture_output=True, text=True)


@contextmanager
def gpu_app(manage: bool = True, log=print):
    """Deploy for the duration of a batch, then tear down even on failure."""
    if manage:
        deploy(log=log)
    try:
        yield
    finally:
        if manage:
            stop(log=log)


# ## Rendering


def render_beats(
    shots: list[tuple[int, str, str]],
    workdir: Path,
    *,
    opening_frame: Path | None,
    seconds: float,
    steps: int = config.DEFAULT_STEPS,
    seed: int = 1101,
    chain: bool = True,
    mute: bool = False,
    identity: str = "",
    manage_app: bool = True,
    log=print,
) -> BatchResult:
    """Render every beat on ONE warm container.

    `shots` is [(beat number, what moves, where it happens)] -- the beat's action and its
    scene line, both of which go into the instruction. When `chain` is set, beat N starts
    from beat N-1's final frame, so only `opening_frame` is needed -- which is what makes
    a multi-beat reel cost a single image against the scarce image quota. Chaining is
    inherently serial; there is nothing to parallelise, and parallelising across
    containers would cost more anyway, since each container repays the model load.
    """
    if not shots:
        raise ValueError("no beats to render")
    if opening_frame is None and chain is False:
        raise ValueError("non-chained rendering needs a first frame per beat")

    length = config.frame_count(seconds)
    if length > config.PROVEN_MAX_FRAMES:
        log(f"[warn] {length} frames exceeds the proven {config.PROVEN_MAX_FRAMES}; "
            "a 362-frame render has failed on this card before")

    result = BatchResult()
    workdir.mkdir(parents=True, exist_ok=True)

    with gpu_app(manage_app, log=log):
        with comfy.client() as http:
            comfy.wake(http, log=log)
            for index, (n, action, scene) in enumerate(shots):
                frame = workdir / f"beat{n}_frame.png"
                # Whether this beat opens mid-motion decides how the prompt has to describe
                # its first frame, so it is read off the same branch that chooses the frame.
                continues = False
                if chain and result.beats:
                    media.last_frame(result.beats[-1].video, frame)
                    continues = True
                    log(f"[render] beat {n}: continuing from beat {shots[index - 1][0]}")
                elif opening_frame is not None:
                    media.fit_frame(
                        opening_frame if index == 0 or chain
                        else workdir / f"beat{n}_asset.png",
                        frame,
                    )
                else:
                    frame = None  # text-to-video

                log(f"[render] beat {n}/{len(shots)}: {length} frames, {steps} steps")
                started = time.monotonic()
                uploaded = comfy.upload_image(http, frame) if frame else None
                outputs = comfy.run_graph(
                    http,
                    comfy.build_graph(
                        first_frame=uploaded,
                        prompt=config.build_prompt(action, scene=scene, mute=mute,
                                                   identity=identity, continues=continues),
                        length=length, steps=steps, seed=seed + n,
                    ),
                    log=log,
                )
                elapsed = time.monotonic() - started
                result.container_seconds += elapsed
                video = comfy.download(http, comfy.only_video(outputs), workdir / f"beat{n}.mp4")
                result.beats.append(
                    Beat(n=n, video=video, first_frame=frame or workdir / f"beat{n}_frame.png",
                         seconds=length / config.FPS, render_seconds=elapsed)
                )

    log(f"[render] {result.container_seconds:.0f} container-seconds "
        f"~= ${result.cost:.2f} (GPU+CPU+memory; excludes cold start and scale-down tail)")
    return result


def render_reel(
    board: dict,
    workdir: Path,
    *,
    seconds: float,
    steps: int = config.DEFAULT_STEPS,
    seed: int = 1101,
    chain: bool = True,
    mute: bool = False,
    manage_app: bool = True,
    out_name: str = "reel",
    log=print,
) -> BatchResult:
    """Render a whole storyboard and stitch it into one deliverable."""
    beats = board["beats"]
    opening = workdir / f"beat{beats[0]['n']}_asset.png"
    if not opening.exists():
        raise FileNotFoundError(
            f"missing opening asset {opening}. Generate it with the asset stage, or drop "
            "your own PNG there."
        )
    if not chain:
        absent = [b["n"] for b in beats if not (workdir / f"beat{b['n']}_asset.png").exists()]
        if absent:
            raise FileNotFoundError(f"scene mode needs an asset per beat; missing {absent}")

    result = render_beats(
        [(b["n"], b["action"], b.get("scene", "")) for b in beats],
        workdir,
        opening_frame=opening,
        seconds=seconds, steps=steps, seed=seed,
        chain=chain, mute=mute, identity=board.get("style_bible", ""),
        manage_app=manage_app, log=log,
    )
    result.reel = media.stitch(
        [b.video for b in result.beats],
        workdir / f"{out_name}_{config.REEL_WIDTH}x{config.REEL_HEIGHT}.mp4",
        mute=mute,
    )
    log(f"[done] {len(result.beats)} beats, {result.seconds_of_video:.0f}s -> {result.reel}")
    return result
