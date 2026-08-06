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

from . import board as board_mod
from . import comfy, config, media


@dataclass
class Shot:
    """One beat as the renderer needs it: what moves, where, and where its frames come from.

    `source` is one of the four joins in board.py. `asset` is the still this beat owns, and
    which of the two keyframe slots it lands in depends on the join: the first frame for a
    cut, the last frame for a bridge. Unused by a plain continuation, which has no still.

    `refs` is the other conditioning mode: up to config.MAX_REF_IMAGES pictures of the cast
    and the set, in <Picture i> order, which the ref2va checkpoint uses INSTEAD of a keyframe.
    Only read when the join is "reference", and empty on every other one. `ref_notes` says
    what each of those pictures is for, by position -- without it the model decides for
    itself, and it decides "this is the scene".
    """

    n: int
    action: str
    scene: str = ""
    source: str = board_mod.SOURCE_CHAIN
    asset: Path | None = None
    refs: list[Path] = field(default_factory=list)
    ref_notes: list[str] = field(default_factory=list)


@dataclass
class Beat:
    """One rendered shot."""

    n: int
    video: Path
    first_frame: Path
    seconds: float
    render_seconds: float
    last_frame: Path | None = None


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
    shots: list[Shot],
    workdir: Path,
    *,
    seconds: float,
    steps: int = config.DEFAULT_STEPS,
    seed: int = 1101,
    mute: bool = False,
    identity: str = "",
    manage_app: bool = True,
    log=print,
) -> BatchResult:
    """Render every beat on ONE warm container.

    Each `Shot` carries its own join, so one batch can mix all three: a cut opens on its own
    still, a continuation opens on the previous clip's final frame, and a bridge does both --
    it opens on the previous clip and is given its own still as the frame it must arrive at.
    Continuations are what make a multi-beat reel cost a single image against the scarce image
    quota; bridges spend one image to put the drift back where you designed it.

    Chaining is inherently serial; there is nothing to parallelise, and parallelising across
    containers would cost more anyway, since each container repays the model load.
    """
    if not shots:
        raise ValueError("no beats to render")
    # Checked before the container is deployed. Discovering a missing still three beats in
    # means having paid for three beats to learn it.
    if board_mod.chains(shots[0].source):
        raise ValueError(f"beat {shots[0].n} is first in this batch and has nothing to "
                         "continue from; give it its own still")
    absent = [shot.n for shot in shots
              if board_mod.uses_asset(shot.source)
              and not (shot.asset and shot.asset.exists())]
    if absent:
        raise FileNotFoundError(f"beats {absent} need their own still; generate the assets "
                                "or drop your own PNGs in place")
    unreferenced = [shot.n for shot in shots
                    if board_mod.uses_refs(shot.source)
                    and not [p for p in shot.refs if p.exists()]]
    if unreferenced:
        raise FileNotFoundError(
            f"beats {unreferenced} are conditioned on reference pictures but have none; "
            f"supply between 1 and {config.MAX_REF_IMAGES} images each"
        )

    length = config.frame_count(seconds)
    if length > config.PROVEN_MAX_FRAMES:
        log(f"[warn] {length} frames exceeds the proven {config.PROVEN_MAX_FRAMES}; "
            "a 362-frame render has failed on this card before")

    result = BatchResult()
    workdir.mkdir(parents=True, exist_ok=True)

    with gpu_app(manage_app, log=log):
        with comfy.client() as http:
            comfy.wake(http, log=log)
            for index, shot in enumerate(shots):
                n = shot.n
                frame = workdir / f"beat{n}_frame.png"
                end_frame = None
                refs: list[Path] = []
                # Whether this beat opens mid-motion decides how the prompt has to describe
                # its first frame, so it is read off the same branch that chooses the frame.
                continues = board_mod.chains(shot.source)
                if board_mod.uses_refs(shot.source):
                    # No keyframe on this path at all -- the pictures are the conditioning,
                    # and they go to the model at their own size.
                    refs = [p for p in shot.refs if p.exists()]
                    frame = None
                    continues = False
                    log(f"[render] beat {n}: {len(refs)} reference pictures")
                elif continues:
                    media.last_frame(result.beats[-1].video, frame)
                    log(f"[render] beat {n}: continuing from beat {shots[index - 1].n}")
                    if shot.source == board_mod.SOURCE_BRIDGE:
                        end_frame = media.fit_frame(shot.asset, workdir / f"beat{n}_end.png")
                        log(f"[render] beat {n}: landing on {shot.asset.name}")
                elif shot.asset is not None:
                    media.fit_frame(shot.asset, frame)
                else:
                    frame = None  # text-to-video

                log(f"[render] beat {n}/{len(shots)}: {length} frames, {steps} steps")
                started = time.monotonic()
                uploaded = comfy.upload_image(http, frame) if frame else None
                outputs = comfy.run_graph(
                    http,
                    comfy.build_graph(
                        first_frame=uploaded,
                        last_frame=(comfy.upload_image(http, end_frame)
                                    if end_frame else None),
                        ref_images=[comfy.upload_image(http, path) for path in refs],
                        prompt=config.build_prompt(shot.action, scene=shot.scene, mute=mute,
                                                   identity=identity, continues=continues,
                                                   lands=end_frame is not None,
                                                   refs=len(refs),
                                                   ref_notes=shot.ref_notes),
                        length=length, steps=steps, seed=seed + n,
                    ),
                    log=log,
                )
                elapsed = time.monotonic() - started
                result.container_seconds += elapsed
                video = comfy.download(http, comfy.only_video(outputs), workdir / f"beat{n}.mp4")
                result.beats.append(
                    Beat(n=n, video=video, first_frame=frame or workdir / f"beat{n}_frame.png",
                         seconds=length / config.FPS, render_seconds=elapsed,
                         last_frame=end_frame)
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
    """Render a whole storyboard and stitch it into one deliverable.

    `chain` is the CLI's global default for boards that never named a join. Where a beat DOES
    name one -- an imported script, or a board built in the studio -- that wins, so a script
    that mixes cuts, continuations and bridges renders as written. `--scenes` (chain=False) is
    the deliberate override: every beat opens on its own still, whatever the document says.
    """
    beats = board["beats"]
    shots = []
    for index, beat in enumerate(beats):
        named = beat.get("source")
        if named == board_mod.SOURCE_REFERENCE:
            # Survives both overrides: a reference beat takes nothing from the beat before
            # it, so neither "this is the first beat" nor --scenes has anything to fix.
            source = board_mod.SOURCE_REFERENCE
        elif index == 0 or not chain:
            source = board_mod.SOURCE_ASSET
        else:
            source = named if named in board_mod.SOURCES else board_mod.SOURCE_CHAIN
        shots.append(Shot(
            n=beat["n"], action=beat["action"], scene=beat.get("scene", ""), source=source,
            asset=workdir / f"beat{beat['n']}_asset.png",
            refs=[
                path for path in (
                    workdir / f"beat{beat['n']}_ref{i}.png"
                    for i in range(1, config.MAX_REF_IMAGES + 1)
                ) if path.exists()
            ],
            # Straight off the document, since a CLI render has no Board object to ask.
            ref_notes=[str(note) for note in (beat.get("ref_prompts") or [])],
        ))

    result = render_beats(
        shots, workdir,
        seconds=seconds, steps=steps, seed=seed,
        mute=mute, identity=board.get("style_bible", ""),
        manage_app=manage_app, log=log,
    )
    result.reel = media.stitch(
        [b.video for b in result.beats],
        workdir / f"{out_name}_{config.REEL_WIDTH}x{config.REEL_HEIGHT}.mp4",
        mute=mute,
    )
    log(f"[done] {len(result.beats)} beats, {result.seconds_of_video:.0f}s -> {result.reel}")
    return result
