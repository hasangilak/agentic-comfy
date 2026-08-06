"""HTTP layer for the studio: board CRUD, job submission, and one SSE stream.

Runs locally, not on Modal, because it shells out to `agy` and `modal` and both depend on
credentials already sitting on this machine. That also means the Modal proxy tokens never
leave the server -- the browser talks only to this process, which is the only arrangement
that works anyway, since a browser cannot attach auth headers to a WebSocket.
"""

from __future__ import annotations

import asyncio
import io
import json
import queue
import re
import time
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agent, board as board_mod, comfy, config, planner, render
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
    run.log(job, f'[plan] {detail["beats"]} beats x {detail["seconds"]:.0f}s via {config.PLANNER_MODEL}')
    board = agent.create(detail["concept"], detail["beats"], detail["seconds"])
    job.slug = board.slug  # the slug only exists once the title does
    run.log(job, f'[plan] "{board.data.get("title")}" -> {board.slug}')
    return {"slug": board.slug}


def handle_chat(job: Job, run: Runner) -> dict:
    board = load(job.slug)
    run.log(job, f'[agy] {job.detail["message"]}')
    result = agent.turn(board, job.detail["message"], selection=job.detail.get("selection"))
    for op in result["ops"]:
        run.log(job, f'[agy] {op["summary"]}')
    return result


def handle_asset(job: Job, run: Runner) -> dict:
    board = load(job.slug)
    made: list[int] = []
    for n in job.detail["beats"]:
        if job.cancelling:
            break
        beat = board.beat(n)
        run.update(job, phase=f"asset for beat {n}", beat=n)

        reference = board.reference_path()
        # Regenerating the very still that IS the reference has to be free to redesign, or
        # the cast could never be changed again once the first image existed.
        if reference is not None and reference == board.asset_path(n):
            reference = None
        run.log(job, f"[asset] beat {n}: generating"
                     + (f", characters locked to {reference.name}" if reference
                        else " (nothing to match yet -- this defines the look)"))
        try:
            planner.generate_asset(beat, board.data.get("style_bible", ""),
                                   board.asset_path(n), board.workdir,
                                   reference=reference)
        except planner.QuotaExhausted as exhausted:
            run.log(job, f"[asset] {exhausted}")
            # Not an error: the remaining beats simply have to wait for the window, and
            # anything already generated stays.
            break
        made.append(n)
        run.publish_board(board.slug)
    return {"beats": made}


def handle_caption(job: Job, run: Runner) -> dict:
    board = load(job.slug)
    run.log(job, "[agy] writing the caption")
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
        if body["source"] not in (board_mod.SOURCE_ASSET, board_mod.SOURCE_CHAIN):
            raise HTTPException(422, "source must be 'asset' or 'chain'")
        if body["source"] == board_mod.SOURCE_CHAIN and board.upstream(n) is None:
            raise HTTPException(422, "the first beat has nothing to continue from")
        beat["source"] = body["source"]
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
    board = load(slug)
    requested = body.get("beats")
    beats = board.to_json()["assets_needed"] if requested is None else requested
    unknown = [n for n in beats if not any(b["n"] == n for b in board.beats)]
    if unknown:
        raise HTTPException(404, f"no such beats: {unknown}")
    if not beats:
        raise HTTPException(422, "no beat needs a still")

    # An explicit per-node request means "prepare this scene with its own image", even if
    # it currently continues from the previous clip. Record that choice immediately so the
    # canvas and render queue agree about the scene boundary while generation is queued.
    if requested is not None:
        for n in beats:
            board.beat(n)["source"] = board_mod.SOURCE_ASSET
        board.save()
        runner.publish_board(slug)

    job = runner.submit("asset", slug, {"beats": beats})
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/beats/{n}/asset")
async def upload_asset(slug: str, n: int, file: UploadFile = File(...)) -> dict:
    """Use your own image as a beat's opening still.

    The way around the image quota, which allows roughly five generations per five hours.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    await store_upload(file, board.asset_path(n))

    # Supplying a still means this beat opens on it rather than continuing from the one
    # before -- which is a real story change, so the wire on the canvas changes to match.
    board.beat(n)["source"] = board_mod.SOURCE_ASSET
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.post("/api/reels/{slug}/reference")
async def upload_reference(slug: str, file: UploadFile = File(...)) -> dict:
    """Pin what the characters look like, for every still generated from here on.

    Costs nothing and spends no quota, but it changes every future cut: each new scene's
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
