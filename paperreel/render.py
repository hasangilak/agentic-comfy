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

# What a beat's join is, in the log, in the words the canvas uses. A batch that prints
# "first frame from bridge" is naming an implementation detail at the one moment the user is
# watching money being spent.
JOIN_LOG = {
    board_mod.SOURCE_ASSET: "opening on its own still, exactly",
    board_mod.SOURCE_CHAIN: "continuing from the clip before",
    board_mod.SOURCE_BRIDGE: "continuing from the clip before, landing on its own still",
    board_mod.SOURCE_REFERENCE: "composed from its reference pictures",
}


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
        source = sources[n]
        # A bridge needs BOTH, so these are two independent checks rather than a branch.
        if board_mod.uses_asset(source) and not board.asset_path(n).exists():
            raise FileNotFoundError(f"beat {n} needs its own still ({board.asset_path(n).name})")
        # Empty `pictures_for` on a beat that is not carrying means there is genuinely nothing
        # to condition on -- no still of its own, and no uploads. ref2va with nothing connected
        # is text-to-video on the wrong weights, so it is worth the check before the container
        # is deployed rather than after it has been paid for.
        if (board_mod.uses_refs(source) and not board.pictures_for(n)
                and not board.carries_motion(board.beat(n))):
            raise FileNotFoundError(
                f"beat {n} is conditioned on references but has none; generate its opening "
                f"still, upload a picture (up to {config.MAX_REF_IMAGES}), or have it carry "
                "the previous clip"
            )
        if board.follows_upstream(board.beat(n)):
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

                    frame, end_frame, pictures, carry, frame_ids = _frames(board, n, sources[n])
                    # Whether <Picture 1> is this beat's own still, which is what turns the
                    # scaffold from "compose the opening yourself" into "begin on this frame".
                    # Read off the list the batch actually queued rather than from the board, so
                    # a still dropped on a later node mid-render cannot change what this beat was
                    # told about the pictures it was given.
                    opens_on = bool(pictures) and pictures[0][0] == board.asset_path(n)
                    join = JOIN_LOG[sources[n]]
                    if pictures or carry:
                        detail = [f"{len(pictures)} of {config.MAX_REF_IMAGES} pictures"]
                        if opens_on:
                            detail.append("opening on its own still")
                        if carry:
                            detail.append(
                                f"carrying the last {config.REF_VIDEO_SECONDS:.0f}s of beat "
                                f"{board.upstream(n)['n']}"
                            )
                        join += f" ({', '.join(detail)})"
                    runner.log(job, f"[render] beat {n}: {frames[n]} frames, {steps} steps, "
                                    f"{join}")
                    started = time.monotonic()
                    outputs = comfy.run_graph(
                        http,
                        comfy.build_graph(
                            # None on a reference beat: ref2va has no keyframe input at all,
                            # and the pictures below take its place.
                            first_frame=comfy.upload_image(http, frame) if frame else None,
                            # Only a bridge has one. The node reads its absence as
                            # "no destination", which is the i2v behaviour every other join wants.
                            last_frame=(comfy.upload_image(http, end_frame)
                                        if end_frame else None),
                            # Uploaded in <Picture i> order and passed as one list, because
                            # position is meaning: the prompt names them by it.
                            ref_images=[comfy.upload_image(http, path)
                                        for path, _ in pictures],
                            # One clip, the previous beat's tail. The node takes up to
                            # config.MAX_REF_VIDEOS; nothing on the canvas asks for more yet.
                            ref_videos=[comfy.upload_video(http, carry)] if carry else [],
                            prompt=config.build_prompt(
                                beat.get("action", ""),
                                # Where the shot is, not just what moves in it. Both are
                                # fingerprinted, so editing either marks the beat stale.
                                scene=beat.get("scene", ""),
                                mute=bool(board.data.get("mute")),
                                identity=board.identity(),
                                # From the source captured up front, not read fresh: the
                                # instruction has to describe the frames this batch is
                                # actually handing over.
                                continues=board_mod.chains(sources[n]),
                                lands=end_frame is not None,
                                # Switches the scaffold to the <Picture i> instructions and,
                                # in build_graph, the checkpoint to ref2va.
                                refs=len(pictures),
                                # What each picture is for, in the same order, straight out of
                                # the pairs -- so the automatic slots carry their own roles and
                                # a note can never end up describing the picture next to it.
                                ref_notes=[note for _, note in pictures] or None,
                                # Names <Picture 1> as this shot's opening composition rather
                                # than as one more design reference.
                                opens_on=opens_on,
                                # Swaps "compose the opening frame yourself" for "open on the
                                # moment <Video 1> ends and carry it on".
                                ref_videos=1 if carry else 0,
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
                            draft=seconds is not None, frame_ids=frame_ids)
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


def _frames(board: board_mod.Board, n: int, source: str):
    """Produce the exact keyframes this beat renders from, and identify them.

    This is where the wire on the canvas becomes real:

      * "asset"     -- the beat's own still, fitted onto the generation grid, as the first frame
      * "chain"     -- the previous clip's true last frame as the first frame, nothing after it
      * "bridge"    -- both: the previous clip's last frame to start from AND the beat's own
                       still as the frame the clip has to arrive at
      * "reference" -- no keyframe at all: up to nine pictures which the ref2va checkpoint
                       conditions on for the whole clip. On the default cut those are the
                       beat's own still and the reel's cast reference, plus any uploads; a beat
                       carrying motion takes the tail of the previous clip as a reference VIDEO
                       instead of wiring a still at all

    `source` is passed in rather than read from the board, because uploading a still can flip
    a beat's join -- and a batch must render what was queued, not change shape halfway
    through because somebody dropped a file on a later node.

    Returns (first, last, pictures, carry, frame_ids). `last` is None unless this is a bridge,
    `first` is None only on a reference beat, `pictures` is empty on every other join, and
    `carry` is the cut tail of the previous clip -- present only on a reference beat set to
    carry motion. The hashes are taken here, at the moment of use, so the recorded fingerprint
    names the images really rendered rather than whatever is on disk when the beat finishes.

    `pictures` is (path, role) pairs, the same shape `Board.pictures_for` returns and for the
    same reason: the prompt addresses each picture by position, so the file and the words
    describing it have to travel together rather than as two lists that can slip apart.
    """
    asset = board.asset_path(n)
    if source == board_mod.SOURCE_REFERENCE:
        # Handed over at their own size: the node scales each one itself, down only and
        # aspect-preserving, so cover-cropping them onto the 9:16 grid first would throw away
        # parts of the design for nothing. The beat's own still rides along under the same rule,
        # which is why nothing is written to beat<n>_frame.png on this path -- it is already on
        # the generation grid, and it is a reference here rather than a keyframe.
        carry, upstream_hash = None, ""
        if board.carries_motion(board.beat(n)):
            # Only the tail. The whole clip would be paid for through every sampling step,
            # for motion that stopped mattering seconds ago.
            source_video = board.video_path(board.upstream(n)["n"])
            carry = media.tail_clip(source_video, board.carry_path(n),
                                    config.REF_VIDEO_SECONDS,
                                    mute=bool(board.data.get("mute")))
            upstream_hash = board_mod.file_hash(source_video)
        else:
            # A beat that used to carry and no longer does must not leave the old tail on
            # disk, where the canvas would still show it as this beat's input.
            board.carry_path(n).unlink(missing_ok=True)
        # Read after the carry decision, not before: `pictures_for` asks `carries_motion` whether
        # this beat's own still is wired at all, so taking the list first would be taking it
        # against a different answer to that question than the render is about to use.
        pictures = board.pictures_for(n)
        # The up-front pass already refused a beat with nothing to condition on, so reaching
        # this with an empty list means the board moved underneath the batch -- someone changed
        # the join, or deleted the still, after the container was warm. Worth raising rather
        # than passing through: build_graph reads "no references and no keyframe" as plain
        # text-to-video on the wrong checkpoint, and would render something plausible and wrong
        # instead of failing.
        if not pictures and carry is None:
            raise FileNotFoundError(
                f"beat {n} had its reference pictures taken away mid-render; nothing is left to "
                "condition it on"
            )
        return (None, None, pictures, carry,
                board_mod.FrameIds(
                    refs=board_mod.fingerprint(*(
                        part for path, note in pictures
                        for part in (board_mod.file_hash(path), note)
                    )),
                    upstream=upstream_hash))

    if source == board_mod.SOURCE_ASSET:
        return (media.fit_frame(asset, board.frame_path(n)), None, [], None,
                board_mod.FrameIds(asset=board_mod.file_hash(asset)))

    video = board.video_path(board.upstream(n)["n"])
    first = media.last_frame(video, board.frame_path(n))
    if source == board_mod.SOURCE_CHAIN:
        return first, None, [], None, board_mod.FrameIds(upstream=board_mod.file_hash(video))
    return (
        first,
        media.fit_frame(asset, board.end_frame_path(n)),
        [],
        None,
        board_mod.FrameIds(asset=board_mod.file_hash(asset),
                           upstream=board_mod.file_hash(video)),
    )


def _record(board: board_mod.Board, n: int, elapsed: float, frames: int, steps: int,
            *, draft: bool = False, frame_ids: board_mod.FrameIds | None = None) -> None:
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
        "fingerprint": board.render_fingerprint(beat, frames=frames, frame_ids=frame_ids),
        "own": board.own_fingerprint(beat, frames=frames, frame_ids=frame_ids),
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
