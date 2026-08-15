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

    `pictures` is the other conditioning mode: up to config.MAX_REF_IMAGES images, in
    <Picture i> order, which the ref2va checkpoint uses INSTEAD of a keyframe. Filled on the
    reference join, and on an asset cut that binds character sheets (that cut has no fl2va
    socket for them, so it renders on ref2va with the still as Picture 1). Empty on chain
    and bridge.

    Each entry is a (path, role) pair rather than the two lists this used to carry, because the
    prompt addresses every picture by position: a path list and a note list that can slip by one
    would describe the cast reference with the words written for the beat's own still. Pairs come
    straight off `Board.pictures_for`, which is what decides the order.

    `opens_on` says the first of those pictures is this beat's own still, so the clip begins on a
    composition drawn for it rather than one the model invents from the scene line. That is what
    a cut is on this checkpoint. A carried clip used to make this false -- two answers to where
    the shot opens. HOLD_VIDEO is a third job for the same socket, so the still (or the pose
    sequence) still names the opening and the previous clip can sit next to it.

    `carry` means wire that previous clip as <Video 1>. `hold_video` is the prompt half: identity
    rather than continuation. The two are independent of `opens_on`.
    """

    n: int
    action: str
    scene: str = ""
    source: str = board_mod.SOURCE_CHAIN
    asset: Path | None = None
    pictures: list[tuple[Path, str]] = field(default_factory=list)
    opens_on: bool = False
    # How to resolve any @-token the director typed into the action or the scene line -- token
    # body to (position in `pictures`, what that picture is for). Carried on the shot rather
    # than looked up at render time for the same reason `pictures` is: this dataclass is the
    # whole description of what one beat was handed, and a board read halfway through a batch
    # could answer differently than the one the batch was planned from.
    mentions: dict[str, tuple[int | None, str]] = field(default_factory=dict)
    # What the reel's bound design sheets say, for the ones this shot was not handed as pictures.
    # Carried rather than looked up for the same reason `mentions` is, and computed against
    # `pictures` so a sheet is never both a numbered picture and a sentence about a second one.
    staging: str = ""
    # Reference beats only: send the tail of the previous clip as a reference video, which is
    # how this join gets continuity without a keyframe. True for the carry checkbox AND for a
    # pose sequence, which holds the same clip as identity.
    carry: bool = False
    # Prompt flag: the video is identity (HOLD_VIDEO), not the opening (CARRY_VIDEO).
    hold_video: bool = False
    # How many of `pictures` are this shot's own stop-motion poses, from <Picture 1>.
    poses: int = 0
    # Where things stand in this frame, and which medium's words wrap the whole prompt. Carried
    # on the shot rather than read at render time for the reason `staging` and `mentions` are:
    # this dataclass is the whole description of what one beat was handed, and a board read
    # halfway through a batch could answer differently than the one the batch was planned from.
    blocking: str = ""
    medium_key: str | None = None
    # Locked-off angle for this take. None means eye / straight-on, which is what
    # `build_prompt` already composed before this field existed.
    camera: str | None = None


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
    temperature: float = config.DEFAULT_TEMPERATURE,
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
                    if board_mod.uses_refs(shot.source) and not shot.carry
                    and not [pair for pair in shot.pictures if pair[0].exists()]]
    if unreferenced:
        raise FileNotFoundError(
            f"beats {unreferenced} are conditioned on reference pictures but have none; "
            f"generate their opening stills, or supply up to {config.MAX_REF_IMAGES} images each"
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
                pictures: list[tuple[Path, str]] = []
                carry: Path | None = None
                # Whether this beat opens mid-motion decides how the prompt has to describe
                # its first frame, so it is read off the same branch that chooses the frame.
                continues = board_mod.chains(shot.source)
                if shot.pictures or board_mod.uses_refs(shot.source):
                    # No keyframe on this path at all -- the pictures are the conditioning,
                    # and they go to the model at their own size. An asset cut lands here when
                    # it binds character sheets: fl2va cannot take them, so the still is
                    # Picture 1 on ref2va instead.
                    pictures = [pair for pair in shot.pictures if pair[0].exists()]
                    frame = None
                    continues = False
                    if shot.carry:
                        prev_video = result.beats[-1].video if result.beats else None
                        if prev_video is None and index > 0:
                            candidate = workdir / f"beat{shots[index - 1].n}.mp4"
                            if candidate.is_file():
                                prev_video = candidate
                        if prev_video is not None:
                            carry = media.tail_clip(prev_video,
                                                    workdir / f"beat{n}_carry.mp4",
                                                    config.REF_VIDEO_SECONDS, mute=mute)
                    log(f"[render] beat {n}: {len(pictures)} reference pictures"
                        + (f", {shot.poses} poses" if shot.poses > 1 else "")
                        + (", opening on its own still" if shot.opens_on else "")
                        + (f" + the last {config.REF_VIDEO_SECONDS:.0f}s of beat "
                           f"{shots[index - 1].n}"
                           + (" as identity" if shot.hold_video else "")
                           if carry else ""))
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

                log(f"[render] beat {n}/{len(shots)}: {length} frames, {steps} steps, "
                    f"temperature {config.clamp_temperature(temperature)}")
                started = time.monotonic()
                uploaded = comfy.upload_image(http, frame) if frame else None
                outputs = comfy.run_graph(
                    http,
                    comfy.build_graph(
                        first_frame=uploaded,
                        last_frame=(comfy.upload_image(http, end_frame)
                                    if end_frame else None),
                        ref_images=[comfy.upload_image(http, path) for path, _ in pictures],
                        ref_videos=[comfy.upload_video(http, carry)] if carry else [],
                        prompt=config.build_prompt(shot.action, scene=shot.scene, mute=mute,
                                                   identity=identity, continues=continues,
                                                   lands=end_frame is not None,
                                                   refs=len(pictures),
                                                   ref_notes=[note for _, note in pictures],
                                                   # A dropped picture cannot leave this true:
                                                   # the still is always first, so if it went
                                                   # missing the whole list shifted under it.
                                                   opens_on=(shot.opens_on and bool(pictures)
                                                             and pictures[0][0] == shot.asset),
                                                   ref_videos=1 if carry else 0,
                                                   poses=shot.poses,
                                                   hold_video=shot.hold_video,
                                                   staging=shot.staging,
                                                   blocking=shot.blocking,
                                                   medium_key=shot.medium_key,
                                                   camera=shot.camera,
                                                   mentions=shot.mentions or None),
                        length=length, steps=steps, seed=seed + n,
                        temperature=temperature,
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
    temperature: float = config.DEFAULT_TEMPERATURE,
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

    A beat that opens a shot without saying which join it wants gets `reference`, the same
    default the studio applies: its own still as <Picture 1> and the reel's cast reference as
    <Picture 2>, on ref2va. An explicit `"source": "asset"` still renders as the exact keyframe
    cut, which is what every board written before this default moved carries.
    """
    beats = board["beats"]
    resolved: list[str] = []
    for index, beat in enumerate(beats):
        named = beat.get("source")
        if named in (board_mod.SOURCE_REFERENCE, board_mod.SOURCE_ASSET):
            # Survives both overrides. A reference beat takes nothing from the beat before it, so
            # neither "this is the first beat" nor --scenes has anything to fix; and an explicit
            # `asset` is a request for the exact keyframe, which those overrides must not undo.
            resolved.append(named)
        elif index == 0 or not chain:
            resolved.append(board_mod.SOURCE_REFERENCE)
        else:
            resolved.append(named if named in board_mod.SOURCES else board_mod.SOURCE_CHAIN)

    # A read-only Board over this render's joins, so the picture order and the role attached to
    # each one are decided in `board.py` and not a second time here. There used to be a
    # hand-rolled ref glob in the loop below; the moment two of the nine slots started filling
    # themselves, that became a second copy of derived state free to disagree with the canvas.
    #
    # Over a COPY of the document, with the resolved joins written in. The copy is the point
    # twice over: `view.source_for` has to see what this render decided or --scenes would compose
    # pictures for a beat it is rendering as a keyframe cut, and the caller's dict is the one
    # `storyboard.py` writes back to disk -- so resolving a join into it would persist derived
    # state, which is the one thing the board document never holds.
    view = board_mod.Board(
        slug=workdir.name,
        path=workdir / "storyboard.json",
        data={**board, "beats": [{**beat, "source": source}
                                 for beat, source in zip(beats, resolved)]},
    )
    shots = [
        Shot(
            n=beat["n"], action=beat["action"], scene=beat.get("scene", ""), source=source,
            asset=workdir / f"beat{beat['n']}_asset.png",
            pictures=view.pictures_for(beat["n"]),
            opens_on=view.opens_on_still(view.beat(beat["n"])),
            # Resolved against the same read-only view the pictures came from, so the numbering
            # a token expands to and the list it is numbering are one decision.
            mentions=view.mentions(beat["n"], view.pictures_for(beat["n"])),
            # Against the same list, for the same reason: a sheet already numbered as a picture
            # must not also arrive as prose about a second one of it.
            staging=view.staging_text(beat["n"], view.pictures_for(beat["n"])),
            carry=(
                source == board_mod.SOURCE_REFERENCE
                and index > 0
                and (beat.get("ref_video") == board_mod.CARRY_UPSTREAM
                     or len(view.pose_paths(beat["n"])) > 1)
            ),
            hold_video=(
                source == board_mod.SOURCE_REFERENCE
                and len(view.pose_paths(beat["n"])) > 1
                and beat.get("ref_video") != board_mod.CARRY_UPSTREAM
            ),
            poses=len(view.pose_paths(beat["n"])[:view.sequence_count(beat["n"])]),
            blocking=beat.get("blocking", ""),
            medium_key=view.medium(),
            camera=view.camera_for(beat),
        )
        for index, (beat, source) in enumerate(zip(beats, resolved))
    ]

    result = render_beats(
        shots, workdir,
        seconds=seconds, steps=steps, seed=seed,
        temperature=temperature,
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
