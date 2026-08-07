"""HTTP layer for the studio: board CRUD, job submission, and one SSE stream.

Runs locally, not on Modal. Everything it orchestrates is on this machine: the language model
on Ollama, the image server next door, and the `modal` CLI with the credentials that let it
deploy a GPU. That also means the Modal proxy tokens never leave the server -- the browser
talks only to this process, which is the only arrangement that works anyway, since a browser
cannot attach auth headers to a WebSocket.
"""

from __future__ import annotations

import asyncio
import io
import json
import queue
import re
import time
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import (agent, board as board_mod, comfy, config, papercut, qwen, render, script,
               stills as stills_mod)
from .jobs import Job, Runner, runner

app = FastAPI(title="Paper Reel Studio")

# Past this a beat is well beyond anything that has ever completed on this card; the node
# already warns above PROVEN_MAX_FRAMES, and this is the hard stop behind it.
# 8 steps is the measured sweet spot and 20 costs ~70% more; past 30 nobody is trading
# quality for money on purpose.
MAX_STEPS = 30

# Generous for a still frame; small enough that a stray video file is rejected rather than
# read into memory.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# The Vite dev server runs on another port during development. The deployed case serves
# the built bundle from this same origin, where CORS is irrelevant.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def safe_slug(slug: str) -> str:
    """A slug names one directory under reels/ -- it is never a path.

    Unvalidated, `slug=".."` walks out of reels/ and both `load` and /media happily read
    whatever is there. Loopback-only binding makes that hard to reach, but it is still a
    traversal and the check costs nothing.
    """
    if not SAFE_SLUG.match(slug):
        raise HTTPException(404, f"no reel called {slug!r}")
    return slug


def load(slug: str) -> board_mod.Board:
    try:
        return board_mod.Board.load(safe_slug(slug))
    except FileNotFoundError:
        raise HTTPException(404, f"no reel called {slug!r}")


def rendering_now(slug: str) -> set[int]:
    """Beats the active job is mid-way through, so the canvas can show them spinning."""
    active = runner.active()
    if not active or active.slug != slug or active.kind != "render":
        return set()
    return {active.beat} if active.beat else set()


def require_structure_idle(slug: str) -> None:
    """Scene positions are job identifiers, so they cannot change under queued work."""
    busy = next(
        (
            job for job in runner.jobs.values()
            if job.slug == slug and job.state in ("queued", "running")
        ),
        None,
    )
    if busy:
        raise HTTPException(
            409,
            f"cannot add or remove scenes while the {busy.kind} job is {busy.state}",
        )


def board_json(board: board_mod.Board) -> dict:
    return board.to_json(rendering=rendering_now(board.slug))


async def store_upload(file: UploadFile, dest: Path) -> None:
    """Decode an uploaded image and write it to `dest`, or answer why it cannot be used.

    Stored at its original size: geometry is settled at render time by media.fit_frame,
    which cover-crops onto the generation grid.
    """
    from PIL import Image

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"image is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    try:
        # verify() consumes the file object, so the decode needs a second open.
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.convert("RGB").save(dest)
    except Exception:  # noqa: BLE001 - any decode failure is the same answer to the user
        raise HTTPException(
            422,
            f"{file.filename or 'that file'} is not a readable image. PNG, JPEG and WebP "
            "work; HEIC from an iPhone does not.",
        )


# ## Job handlers
#
# Registered on the single worker. Each one is a plain function that may block for minutes.


def handle_plan(job: Job, run: Runner) -> dict:
    detail = job.detail
    run.log(job, f'[plan] {detail["beats"]} beats x {detail["seconds"]:.0f}s via {config.QWEN_MODEL}')
    board = agent.create(detail["concept"], detail["beats"], detail["seconds"],
                         log=lambda line: run.log(job, line))
    job.slug = board.slug  # the slug only exists once the title does
    run.log(job, f'[plan] "{board.data.get("title")}" -> {board.slug}')
    return {"slug": board.slug}


def handle_chat(job: Job, run: Runner) -> dict:
    """One conversational turn, tools and all.

    A turn can reach the image server, so it can take minutes rather than seconds -- the
    hooks below are the same ones the still job uses, which is what puts a `generate_stills`
    tool call in the job log and on the canvas as it happens rather than after the turn ends.
    """
    board = load(job.slug)
    run.log(job, f'[qwen] {job.detail["message"]}')
    result = agent.turn(
        board, job.detail["message"], selection=job.detail.get("selection"),
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run),
        announce=lambda: run.publish_board(board.slug),
        cancelled=lambda: job.cancelling,
    )
    return result


def still_progress(job: Job, run: Runner):
    """The image server's 0..1 per-frame fraction, on the fields the UI already has.

    The studio's progress strip is built around ComfyUI's step counters, so this scales onto
    those rather than teaching the UI a second shape.
    """
    return lambda n, fraction: run.update(
        job, phase=f"still for beat {n}", beat=n,
        step=round(fraction * config.PAPERCUT_STEPS), step_max=config.PAPERCUT_STEPS)


def handle_asset(job: Job, run: Runner) -> dict:
    board = load(job.slug)
    return {"beats": stills_mod.generate(
        board, job.detail["beats"],
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run),
        announce=lambda: run.publish_board(board.slug),
        cancelled=lambda: job.cancelling,
    )}


def handle_caption(job: Job, run: Runner) -> dict:
    board = load(job.slug)
    run.log(job, "[qwen] writing the caption")
    return {"caption": agent.caption(board)}


def handle_render(job: Job, run: Runner) -> dict:
    board = load(job.slug)
    return render.render(board, job.detail["beats"], job, run,
                         seconds=job.detail.get("seconds"))


for kind, handler in (
    ("plan", handle_plan), ("chat", handle_chat), ("asset", handle_asset),
    ("caption", handle_caption), ("render", handle_render),
):
    runner.register(kind, handler)


# ## Reels


@app.get("/api/reels")
def list_reels() -> dict:
    return {"reels": list(board_mod.summaries())}


@app.post("/api/reels")
def create_reel(body: dict = Body(...)) -> dict:
    concept = (body.get("concept") or "").strip()
    if not concept:
        raise HTTPException(422, "give a concept")
    beats = max(1, min(int(body.get("beats") or 4), 8))
    seconds = config.snap_seconds(body.get("seconds") or config.BEAT_LENGTHS[-1])
    job = runner.submit("plan", board_mod.slugify(concept),
                        {"concept": concept, "beats": beats, "seconds": seconds})
    return {"job": job.to_json()}


@app.post("/api/reels/import")
def import_reel(body: dict = Body(...)) -> dict:
    """Adopt a script the user wrote themselves, instead of asking the model to write one.

    Synchronous, unlike POST /api/reels: nothing here calls the model or a GPU, so there is no
    job worth watching. The reel exists by the time this answers and the client can open it.

    `notes` is what is thin about the script -- a missing style bible, a cut with no prompt.
    Advice, not errors: every one of them is fixable for free on the canvas.

    `manual_stills` adopts the script with image generation switched off, for the case where
    the opening frames are the author's own work as well.
    """
    raw = body.get("script")
    try:
        if isinstance(raw, str):
            raw = script.parse(raw)
        if not isinstance(raw, dict):
            raise script.BadScript("send the script as JSON text, or as a JSON object")
        board = script.adopt(raw, manual_stills=bool(body.get("manual_stills")))
    except script.BadScript as bad:
        raise HTTPException(422, str(bad))
    # So a second open tab's rail picks the new reel up rather than waiting for a reload.
    runner.publish_board(board.slug)
    return {"slug": board.slug, "board": board_json(board), "notes": script.notes(board.data)}


@app.get("/api/reels/{slug}")
def get_reel(slug: str) -> dict:
    board = load(slug)
    return {
        "board": board_json(board),
        "chat": board.data.get("chat", []),
        "last_render": board.data.get("last_render"),
    }


@app.patch("/api/reels/{slug}")
def patch_reel(slug: str, body: dict = Body(...)) -> dict:
    board = load(slug)
    for key in ("title", "style_bible", "caption", "canvas"):
        if key in body:
            board.data[key] = body[key]
    if "seconds" in body:
        board.data["seconds"] = config.snap_seconds(body["seconds"])
    if "steps" in body:
        board.data["steps"] = max(1, min(int(body["steps"]), MAX_STEPS))
    if "seed" in body:
        board.data["seed"] = int(body["seed"])
    if "mute" in body:
        board.data["mute"] = bool(body["mute"])
    if "manual_stills" in body:
        board.data["manual_stills"] = bool(body["manual_stills"])
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.delete("/api/reels/{slug}")
def delete_reel(slug: str) -> dict:
    board = load(slug)
    # Move rather than delete: renders cost real money and a mis-click should not be final.
    trash = board_mod.reels_dir() / ".trash" / f"{slug}-{int(time.time())}"
    trash.parent.mkdir(parents=True, exist_ok=True)
    board.workdir.rename(trash)
    return {"trashed": str(trash)}


# ## Beats


@app.patch("/api/reels/{slug}/beats/{n}")
def patch_beat(slug: str, n: int, body: dict = Body(...)) -> dict:
    board = load(slug)
    try:
        beat = board.beat(n)
    except KeyError:
        raise HTTPException(404, f"beat {n} not in {slug}")
    for key in ("scene", "action", "asset_prompt"):
        if key in body:
            beat[key] = str(body[key])
    if "seconds" in body:
        beat["seconds"] = config.snap_seconds(body["seconds"])
    if "source" in body:
        if body["source"] not in board_mod.SOURCES:
            raise HTTPException(422, f"source must be one of {', '.join(board_mod.SOURCES)}")
        if board_mod.chains(body["source"]) and board.upstream(n) is None:
            raise HTTPException(422, "the first beat has nothing to continue from")
        beat["source"] = body["source"]
    if "carry" in body:
        # The reference join's answer to continuity: the tail of the previous clip goes in as
        # a reference VIDEO, since ref2va has no keyframe slot to hand a frame to.
        if not board_mod.uses_refs(board.source_for(beat)):
            raise HTTPException(
                422,
                "only a reference scene can carry the previous clip; the keyframe joins "
                "already take its last frame directly",
            )
        if body["carry"] and board.upstream(n) is None:
            raise HTTPException(422, "the first beat has nothing to carry")
        if body["carry"]:
            beat["ref_video"] = board_mod.CARRY_UPSTREAM
        else:
            beat.pop("ref_video", None)
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.post("/api/reels/{slug}/beats")
def add_beat(slug: str, body: dict = Body(...)) -> dict:
    board = load(slug)
    require_structure_idle(slug)
    agent.apply_ops(board, [{"op": "add_beat", **body}])
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.delete("/api/reels/{slug}/beats/{n}")
def remove_beat(slug: str, n: int) -> dict:
    board = load(slug)
    require_structure_idle(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    agent.apply_ops(board, [{"op": "remove_beat", "n": n}])
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


# ## Costing
#
# Called on every edit, so the render button's price is never out of date.


@app.post("/api/reels/{slug}/estimate")
def estimate(slug: str, body: dict = Body(default={})) -> dict:
    board = load(slug)
    beats = body.get("beats") or board.pending()
    beats = board.cascade(beats)
    if body.get("draft"):
        return board.cost_of_at(beats, config.DRAFT_SECONDS)
    return board.cost_of(beats)


# ## Jobs


@app.post("/api/reels/{slug}/chat")
def chat(slug: str, body: dict = Body(...)) -> dict:
    load(slug)
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(422, "empty message")
    job = runner.submit("chat", slug,
                        {"message": message, "selection": body.get("selection") or []})
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/assets")
def assets(slug: str, body: dict = Body(default={})) -> dict:
    """Queue the opening stills for these beats, or for every beat that needs one.

    Which beats may get a still is decided in `stills.wanted`, not here. There are three ways
    in now -- this endpoint, a conversation asking for stills, and the CLI -- and every one of
    them has to refuse a board whose stills are the user's own work and a reference beat that
    would silently be turned into a cut. The refusal carries the status code to answer with,
    so moving the rule did not cost the API its precision.
    """
    board = load(slug)
    requested = body.get("beats")
    try:
        beats = stills_mod.wanted(board, requested)
        # An explicit per-node request means "prepare this scene with its own image", even if
        # it currently continues from the previous clip; `claim` records that before anything
        # is generated. A request for everything that needs one changes no joins at all.
        if requested is not None:
            stills_mod.claim(board, beats)
            runner.publish_board(slug)
    except stills_mod.StillsError as refused:
        raise HTTPException(refused.status, str(refused))

    job = runner.submit("asset", slug, {"beats": beats})
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/beats/{n}/asset")
async def upload_asset(slug: str, n: int, file: UploadFile = File(...),
                       source: str = Form(board_mod.SOURCE_ASSET)) -> dict:
    """Use your own image as a beat's still.

    Also the answer when the local image server is not running, or cannot run at all: nothing
    else generates stills, so an upload is the only other way a beat gets its opening frame.

    `source` says which of H3's two keyframe slots the picture is for, because a supplied
    image answers two different questions:

      * "asset" (the default) -- it is where this beat STARTS, so the beat becomes a cut
      * "bridge" -- it is where this beat ENDS, and the beat still opens on the previous
        clip's final frame. Use this when the shot has to carry straight on from the beat
        before AND arrive at a composition you drew.

    Either way the still is stored in the same place; only the join changes, and with it
    which slot the render hands it to.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    if source not in (board_mod.SOURCE_ASSET, board_mod.SOURCE_BRIDGE):
        raise HTTPException(
            422, "an uploaded still is either this beat's opening frame ('asset') or the "
                 "frame it lands on ('bridge')",
        )
    if source == board_mod.SOURCE_BRIDGE and board.upstream(n) is None:
        raise HTTPException(422, "the first beat has nothing to continue from, so its still "
                                 "can only be its opening frame")
    await store_upload(file, board.asset_path(n))

    # Supplying a still is a real story change -- either this beat now opens on it instead of
    # continuing from the one before, or it now has a composition it must arrive at -- so the
    # wire on the canvas changes to match.
    board.beat(n)["source"] = source
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.post("/api/reels/{slug}/beats/{n}/refs")
async def upload_refs(slug: str, n: int, files: list[UploadFile] = File(...)) -> dict:
    """Add reference pictures to a beat, and put it on the reference join.

    This is the other way to condition a shot: instead of one keyframe the model is shown up
    to config.MAX_REF_IMAGES pictures of the cast and the set, and composes the opening frame
    itself. The prompt names them <Picture 1>..<Picture N> in the order they were added, so
    the order is meaningful and appending is deliberate -- new pictures land after the ones
    already there rather than shuffling the numbering under the prompt.

    Uploads cost nothing to place, exactly like an uploaded still. What they cost is render
    time: reference tokens ride through every sampling step, so nine pictures is a slower
    clip than one.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    if not files:
        raise HTTPException(422, "no images sent")

    stored = 0
    for file in files:
        index = board.next_ref_index(n)
        if index is None:
            # Partial success is the honest answer: the images that fitted are on disk, and
            # saying so beats either silently dropping the rest or rejecting the whole batch.
            raise HTTPException(
                409,
                f"beat {n} already has {config.MAX_REF_IMAGES} reference pictures, which is "
                f"the model's limit. {stored} of this upload were stored; remove one to add "
                "another.",
            )
        await store_upload(file, board.ref_path(n, index))
        stored += 1

    # Supplying references IS the join, the same way supplying a still makes a beat a cut:
    # the beat is now conditioned on pictures and has no keyframe at all.
    board.beat(n)["source"] = board_mod.SOURCE_REFERENCE
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board), "stored": stored}


@app.patch("/api/reels/{slug}/beats/{n}/refs/{index}")
def describe_ref(slug: str, n: int, index: int, body: dict = Body(...)) -> dict:
    """Say what one reference picture is FOR, in the model's own words.

    This is the fix for the two-of-the-same-character problem: shown a picture of the cast
    standing in the finished set, ref2va reproduces it AND acts the beat out with a second
    copy of the same puppet. Told "<Picture 1> is the same single Moth that performs the
    action, not an extra one", it collapses them back into one.

    Free, and it marks the beat stale, because these words go into the render.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    try:
        board.set_ref_prompt(n, index, str(body.get("prompt") or ""))
    except IndexError:
        raise HTTPException(404, f"beat {n} has no reference picture {index}")
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.delete("/api/reels/{slug}/beats/{n}/refs/{index}")
def remove_ref(slug: str, n: int, index: int) -> dict:
    """Drop one reference picture, by the number the prompt calls it.

    The survivors are renumbered to close the gap, because the prompt numbers them by
    position: leaving a hole would have the model told about a <Picture 2> that is really the
    third image, or a picture with no tag at all. Their descriptions move with them.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    try:
        board.remove_ref(n, index)
    except IndexError:
        raise HTTPException(404, f"beat {n} has no reference picture {index}")
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.post("/api/reels/{slug}/reference")
async def upload_reference(slug: str, file: UploadFile = File(...)) -> dict:
    """Pin what the characters look like, for every still generated from here on.

    Costs nothing, but it changes every future cut: each new scene's
    still is generated conditioned on this image instead of on the style bible alone.
    Existing stills are left exactly as they are -- regenerate the ones you want matched.
    """
    board = load(slug)
    await store_upload(file, board.workdir / board_mod.REFERENCE_NAME)
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.delete("/api/reels/{slug}/reference")
def clear_reference(slug: str) -> dict:
    """Drop back to the default: beat 1's own still is the reference."""
    board = load(slug)
    (board.workdir / board_mod.REFERENCE_NAME).unlink(missing_ok=True)
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.delete("/api/reels/{slug}/beats/{n}/video")
def discard_clip(slug: str, n: int) -> dict:
    """Throw away one beat's rendered clip, when the take is simply no good.

    The beat drops back to `ready` and re-enters what the render button covers, and every
    beat chained below it reads as following a change -- which it is, since the frame those
    beats open on has just gone away.

    The file is moved into the reel's `.discarded/` rather than deleted. Renders are the only
    thing here that costs money, so "I didn't like it" should not be the same gesture as
    "destroy it".
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    # Pulling the file out from under a running render would leave the job downloading into a
    # path nobody is watching, and the beat would finish by writing the clip straight back.
    if n in rendering_now(slug):
        raise HTTPException(409, f"beat {n} is rendering right now; cancel it first")
    try:
        moved = board.discard_video(n)
    except FileNotFoundError:
        raise HTTPException(404, f"beat {n} has no rendered clip")
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board), "discarded": moved.name}


@app.post("/api/reels/{slug}/caption")
def write_caption(slug: str) -> dict:
    load(slug)
    return {"job": runner.submit("caption", slug, {}).to_json()}


@app.post("/api/reels/{slug}/render")
def start_render(slug: str, body: dict = Body(default={})) -> dict:
    board = load(slug)
    draft = bool(body.get("draft"))
    # A draft is a render-time override, never an edit. Writing DRAFT_SECONDS into the
    # document and deleting the per-beat durations -- as this used to -- silently threw away
    # the lengths the user had chosen, with no way back.
    if draft:
        beats = board.cascade(
            body.get("beats") or [b["n"] for b in board.ordered_beats() if b.get("action")]
        )
    else:
        beats = board.cascade(body.get("beats") or board.pending())
    if not beats:
        raise HTTPException(422, "nothing to render")
    job = runner.submit("render", slug, {
        "beats": beats,
        "draft": draft,
        "seconds": config.DRAFT_SECONDS if draft else None,
    })
    estimate = (
        board.cost_of_at(beats, config.DRAFT_SECONDS) if draft else board.cost_of(beats)
    )
    return {"job": job.to_json(), "estimate": estimate}


@app.get("/api/jobs")
def list_jobs() -> dict:
    active = runner.active()
    return {"jobs": runner.recent(), "active": active.id if active else None}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    if job_id not in runner.jobs:
        raise HTTPException(404, "no such job")
    return {"job": runner.jobs[job_id].to_json()}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    if job_id not in runner.jobs:
        raise HTTPException(404, "no such job")
    return {"job": runner.cancel(job_id).to_json()}


# ## Status and the panic button


@app.get("/api/status")
def status() -> dict:
    active = runner.active()
    return {
        "container": runner.container.to_json(),
        "active": active.to_json() if active else None,
        "queued": [runner.jobs[i].to_json() for i in runner.order
                   if runner.jobs[i].state == "queued"],
        "auth": bool(comfy.modal_auth_headers()) or config.PUBLIC_ENDPOINT,
        "backend": config.BACKEND_URL,
        "rate_per_second": config.RATE_PER_SEC,
        # Both local services, probed per request rather than cached, because each is a
        # separate process the user starts and stops -- a value read once at import would have
        # the studio still claiming the image server is down an hour after `make images`.
        #
        # Neither is fatal and neither costs anything, but they fail in different ways and the
        # UI has to be able to say which: with no model there is no script, no conversation and
        # no caption; with no image server the stills have to be uploads.
        "stills": {
            "backend": "papercut" if papercut.available() else "none",
            "papercut_url": config.PAPERCUT_URL,
        },
        "language": qwen.health() or {"url": config.OLLAMA_URL, "model": config.QWEN_MODEL,
                                      "ready": False, "models": []},
    }


@app.post("/api/app/stop")
def stop_app() -> dict:
    """Unconditional teardown. Reachable from every screen state on purpose."""
    active = runner.active()
    if active:
        runner.cancel(active.id)
    pipeline_stop()
    runner.container.mark("cold")
    runner.publish_container()
    return {"container": runner.container.to_json()}


def pipeline_stop() -> None:
    from . import pipeline
    pipeline.stop(log=lambda line: runner.publish({"type": "log", "job_id": None, "line": line}))


# ## Events
#
# One SSE stream carries everything: job transitions, log lines, per-step progress, board
# invalidations, and a 2-second heartbeat that keeps the container timer honest.


TICK_SECONDS = 2.0
POLL_SECONDS = 0.2  # how often the loop looks for new events and for a gone browser


@app.get("/api/events")
def events(request: Request) -> StreamingResponse:
    """The stream must be able to end, or the whole server becomes unusable.

    This used to be a sync generator blocking on `channel.get(timeout=2)` forever. A
    never-ending response is a trap: on shutdown uvicorn closes the listening socket and
    then waits for open connections to drain, so one open browser tab left the process
    alive with nothing listening -- SSE still flowing, so the studio looked healthy, while
    every fetch got ECONNREFUSED and buttons like ▶ render silently did nothing.

    Async and non-blocking on purpose: it notices a disconnected browser within
    POLL_SECONDS (which also stops leaking a subscriber queue per abandoned tab), and it
    holds no threadpool worker while it waits, so a handful of open tabs cannot starve the
    threadpool that every other (sync) endpoint here needs.
    """
    async def stream():
        channel = runner.subscribe()
        try:
            yield sse({"type": "hello", "container": runner.container.to_json(),
                       "jobs": runner.recent()})
            ticked = time.monotonic()
            while not await request.is_disconnected():
                drained = False
                while True:
                    try:
                        event = channel.get_nowait()
                    except queue.Empty:
                        break
                    drained = True
                    yield sse(event)
                if not drained and time.monotonic() - ticked >= TICK_SECONDS:
                    ticked = time.monotonic()
                    yield sse({"type": "tick", "container": runner.container.to_json()})
                await asyncio.sleep(POLL_SECONDS)
        finally:
            runner.unsubscribe(channel)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# ## Media
#
# Serves straight out of reels/, so a clip is watchable the instant it is downloaded.


@app.get("/media/{slug}/{name}")
def media_file(slug: str, name: str) -> FileResponse:
    root = (board_mod.reels_dir() / safe_slug(slug)).resolve()
    path = (root / name).resolve()
    # Must be a file sitting directly in this reel's directory -- a prefix check alone would
    # let `name` climb out with "../".
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "no such file")
    return FileResponse(path)


# ## The built frontend
#
# Mounted last so it cannot shadow the API. Absent during development, when Vite serves it.

DIST = config.ROOT / "studio" / "dist"
if DIST.is_dir():
    @app.get("/reels/{slug}", include_in_schema=False)
    def reel_page(slug: str) -> FileResponse:
        """Serve the SPA entry point for an addressable canvas board."""
        load(slug)  # Preserve a real 404 for malformed or missing board URLs.
        return FileResponse(DIST / "index.html")

    app.mount("/", StaticFiles(directory=DIST, html=True), name="studio")
