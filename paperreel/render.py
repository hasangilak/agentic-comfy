"""Rendering a board, with the telemetry the studio needs.

Differs from pipeline.render_beats in three ways that only matter to a UI:

  * phase transitions are reported (deploy, boot, per beat, stitch, stop), because a single
    progress bar across stages of wildly different length would be a lie;
  * each clip is downloaded and announced the moment its beat finishes, so beat 1 is
    watchable while beat 4 is still sampling;
  * every completed beat records what it was rendered from, which is what lets the canvas
    mark downstream beats stale later.

Cancellation and teardown are the load-bearing parts. The container is stopped in a
`finally`, and a cancel interrupts ComfyUI before the app goes away so no partial file lands.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from . import board as board_mod
from . import comfy, config, media, pipeline
from .jobs import Job, Runner


def render(board: board_mod.Board, beats: list[int], job: Job, runner: Runner,
           *, seconds: float | None = None) -> dict:
    """Render exactly these beats on one warm container.

    `seconds` overrides every beat's length for this run only -- that is what a draft pass
    is. The board document is never touched, and each beat records the length it was
    actually rendered at, so a draft correctly leaves the final still pending.
    """
    ordered = [n for n in board.cascade(beats)]
    if not ordered:
        return {"beats": [], "cost": 0.0}

    steps = board.steps()
    frames = {
        n: config.frame_count(seconds if seconds is not None else board.seconds_for(board.beat(n)))
        for n in ordered
    }
    over = [n for n, count in frames.items() if count > config.PROVEN_MAX_FRAMES]
    if over:
        runner.log(job, f"[warn] beats {over} exceed the proven {config.PROVEN_MAX_FRAMES} "
                        "frames; a 362-frame render has failed on this card before")

    # Validate the whole chain up front. Discovering a missing first frame three beats in
    # means having paid for three beats to learn it. The source of each beat is captured
    # here and used for the rest of the batch, so a still uploaded mid-render cannot
    # silently turn a chained beat into a cut halfway through.
    sources = {n: board.source_for(board.beat(n)) for n in ordered}
    for n in ordered:
        beat = board.beat(n)
        if sources[n] == board_mod.SOURCE_ASSET:
            if not board.asset_path(n).exists():
                raise FileNotFoundError(f"beat {n} needs its own still ({board.asset_path(n).name})")
        else:
            upstream = board.upstream(n)
            if upstream is None:
                raise ValueError(f"beat {n} is set to continue from the previous beat, but "
                                 "it is the first beat; give it its own still instead")
            if upstream["n"] not in ordered and not board.video_path(upstream["n"]).exists():
                raise FileNotFoundError(
                    f"beat {n} continues from beat {upstream['n']}, which has not been "
                    "rendered yet; include it in the render"
                )

    # The websocket carries per-step sampling progress. `current` lets the callback label
    # each tick with the beat being sampled without threading state through comfy.py.
    current = {"beat": None}
    closers: list = []
    stop_listening = threading.Event()
    rendered: list[int] = []

    def on_progress(value: int, maximum: int) -> None:
        job.step, job.step_max = value, maximum
        runner.publish({"type": "progress", "job_id": job.id, "beat": current["beat"],
                        "step": value, "step_max": maximum})

    listener = threading.Thread(
        target=comfy.progress_listener,
        args=(on_progress,),
        kwargs={"log": lambda line: runner.log(job, line), "closers": closers,
                "stop_event": stop_listening},
        daemon=True,
    )

    # Nothing may fail between starting the container meter and entering the try, or the
    # clock runs forever and the header shows money accruing on a container that does not
    # exist. Setup is all done above; this is the last statement before the guard.
    runner.update(job, beat_total=len(ordered), phase="deploying")
    runner.container.mark("deploying")
    runner.publish_container()
    boot_started = time.monotonic()

    try:
        with pipeline.gpu_app(True, log=lambda line: runner.log(job, line)):
            runner.update(job, phase="booting")
            with comfy.client(timeout=900.0) as http:
                comfy.wake(http, log=lambda line: runner.log(job, line))
                runner.container.mark("warm")
                runner.publish_container()
                listener.start()
                runner.log(job, f"[app] warm after {time.monotonic() - boot_started:.0f}s "
                                "(boot + model load, paid once for the whole batch)")

                for index, n in enumerate(ordered, start=1):
                    if job.cancelling:
                        break
                    beat = board.beat(n)
                    current["beat"] = n
                    runner.update(
                        job, phase="rendering", beat=n, beat_index=index,
                        step=0, step_max=steps, beat_started_at=time.time(),
                    )

                    frame, frame_id = _first_frame(board, n, sources[n])
                    runner.log(job, f"[render] beat {n}: {frames[n]} frames, {steps} steps, "
                                    f"first frame from {sources[n]}")
                    started = time.monotonic()
                    outputs = comfy.run_graph(
                        http,
                        comfy.build_graph(
                            first_frame=comfy.upload_image(http, frame),
                            prompt=config.build_prompt(
                                beat.get("action", ""), mute=bool(board.data.get("mute"))
                            ),
                            length=frames[n], steps=steps, seed=board.seed_for(beat),
                        ),
                        poll=2.0,
                        log=lambda line: runner.log(job, line),
                        should_stop=lambda: job.cancelling,
                    )
                    elapsed = time.monotonic() - started

                    comfy.download(http, comfy.only_video(outputs), board.video_path(n))
                    _record(board, n, elapsed, frames[n], steps,
                            draft=seconds is not None, frame_id=frame_id)
                    rendered.append(n)
                    # Announce immediately: the browser attaches this clip to its node and
                    # the user can watch it while the rest of the batch renders.
                    runner.publish_board(board.slug)
                    runner.log(job, f"[render] beat {n} done in {elapsed:.0f}s "
                                    f"(~${config.estimate_cost(elapsed):.2f})")

                if job.cancelling:
                    comfy.interrupt(http)
                    runner.log(job, "[cancel] stopping the container")
    finally:
        stop_listening.set()
        for close in closers:
            close()
        runner.container.mark("cold")
        runner.publish_container()
        # Billed from deploy to teardown, which is wider than the sum of per-beat render
        # times: it also covers the model load, the frame handoff between beats, and the
        # stop itself. Summing the beats alone reads about 10% low.
        container_seconds = time.monotonic() - boot_started
        # Recorded here rather than after the block, because a render that fails fifteen
        # minutes in has still spent the money and must still show up in the total.
        board.data["last_render"] = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "beats": rendered,
            "container_seconds": round(container_seconds, 1),
            "cost": round(config.estimate_cost(container_seconds), 4),
        }
        board.data["spend_seconds"] = round(
            float(board.data.get("spend_seconds") or 0.0) + container_seconds, 1
        )
        board.save()

    reel = None
    if rendered and not job.cancelling:
        runner.update(job, phase="stitching")
        reel = _stitch(board, runner, job)

    runner.log(job, f"[done] {len(rendered)} beats, {container_seconds:.0f} container-seconds "
                    f"~= ${config.estimate_cost(container_seconds):.2f}")
    return {
        "beats": rendered,
        "container_seconds": round(container_seconds, 1),
        "cost": round(config.estimate_cost(container_seconds), 4),
        "reel": str(reel) if reel else None,
    }


def _first_frame(board: board_mod.Board, n: int, source: str):
    """Produce the exact opening frame this beat renders from, and identify it.

    This is where the wire on the canvas becomes real: "asset" fits the beat's own still
    onto the generation grid, "chain" pulls the last frame out of the previous clip.

    `source` is passed in rather than read from the board, because uploading a still flips
    a beat from chained to its own image -- and a batch must render what was queued, not
    change shape halfway through because somebody dropped a file on a later node.

    Returns (frame, frame_id) where frame_id is the still's content hash taken here, at the
    moment of use, so the recorded fingerprint names the image really rendered.
    """
    target = board.frame_path(n)
    if source == board_mod.SOURCE_ASSET:
        return media.fit_frame(board.asset_path(n), target), board_mod.file_hash(
            board.asset_path(n)
        )
    upstream = board.upstream(n)
    video = board.video_path(upstream["n"])
    return media.last_frame(video, target), board_mod.file_hash(video)


def _record(board: board_mod.Board, n: int, elapsed: float, frames: int, steps: int,
            *, draft: bool = False, frame_id: str | None = None) -> None:
    """Stamp a beat with what it was made from, so staleness can be detected later."""
    beat = board.beat(n)
    beat["render"] = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frames": frames,
        "steps": steps,
        "seed": board.seed_for(beat),
        "seconds": round(frames / config.FPS, 2),
        "render_seconds": round(elapsed, 1),
        "cost": round(config.estimate_cost(elapsed), 4),
        "draft": draft,
        # Fingerprinted on the frame count ACTUALLY rendered and computed after the file
        # lands, so a chained beat hashes the clip it truly follows and a draft does not
        # masquerade as the finished article.
        "fingerprint": board.render_fingerprint(beat, frames=frames, frame_id=frame_id),
        "own": board.own_fingerprint(beat, frames=frames, frame_id=frame_id),
    }
    board.save()


def _stitch(board: board_mod.Board, runner: Runner, job: Job):
    """Assemble the deliverable, but only when every beat actually has a clip."""
    clips = [board.video_path(b["n"]) for b in board.ordered_beats()]
    missing = [p.name for p in clips if not p.exists()]
    if missing:
        runner.log(job, f"[stitch] skipped: still missing {', '.join(missing)}")
        return None
    reel = media.stitch(clips, board.reel_path, mute=bool(board.data.get("mute")))
    runner.log(job, f"[stitch] {len(clips)} clips -> {reel.name}")
    return reel
