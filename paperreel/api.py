"""HTTP layer for the studio: board CRUD, job submission, and one SSE stream.

Runs locally, not on Modal, because it shells out to `agy` and `modal` and both depend on
credentials already sitting on this machine. That also means the Modal proxy tokens never
leave the server -- the browser talks only to this process, which is the only arrangement
that works anyway, since a browser cannot attach auth headers to a WebSocket.
"""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import agent, board as board_mod, comfy, config, planner, render
from .jobs import Job, Runner, runner

app = FastAPI(title="Paper Reel Studio")

# The Vite dev server runs on another port during development. The deployed case serves
# the built bundle from this same origin, where CORS is irrelevant.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def load(slug: str) -> board_mod.Board:
    try:
        return board_mod.Board.load(slug)
    except FileNotFoundError:
        raise HTTPException(404, f"no reel called {slug!r}")


def rendering_now(slug: str) -> set[int]:
    """Beats the active job is mid-way through, so the canvas can show them spinning."""
    active = runner.active()
    if not active or active.slug != slug or active.kind != "render":
        return set()
    return {active.beat} if active.beat else set()


def board_json(board: board_mod.Board) -> dict:
    return board.to_json(rendering=rendering_now(board.slug))


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
        run.log(job, f"[asset] beat {n}: generating")
        try:
            planner.generate_asset(beat, board.data.get("style_bible", ""),
                                   board.asset_path(n), board.workdir)
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
    return render.render(board, job.detail["beats"], job, run)


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
    seconds = float(body.get("seconds") or 10.0)
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
    for key in ("title", "style_bible", "caption", "seconds", "steps", "seed", "mute", "canvas"):
        if key in body:
            board.data[key] = body[key]
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
    for key in ("scene", "action", "asset_prompt", "seconds"):
        if key in body:
            beat[key] = body[key]
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
    agent.apply_ops(board, [{"op": "add_beat", **body}])
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.delete("/api/reels/{slug}/beats/{n}")
def remove_beat(slug: str, n: int) -> dict:
    board = load(slug)
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
    return board.cost_of(board.cascade(beats))


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
    beats = body.get("beats") or board.to_json()["assets_needed"]
    if not beats:
        raise HTTPException(422, "no beat needs a still")
    job = runner.submit("asset", slug, {"beats": beats})
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/caption")
def write_caption(slug: str) -> dict:
    load(slug)
    return {"job": runner.submit("caption", slug, {}).to_json()}


@app.post("/api/reels/{slug}/render")
def start_render(slug: str, body: dict = Body(default={})) -> dict:
    board = load(slug)
    beats = board.cascade(body.get("beats") or board.pending())
    if not beats:
        raise HTTPException(422, "nothing to render")
    if body.get("draft"):
        # A cheap approval pass: shorten every beat, keep everything else.
        board.data["seconds"] = config.DRAFT_SECONDS
        for beat in board.beats:
            beat.pop("seconds", None)
        board.save()
    job = runner.submit("render", slug, {"beats": beats, "draft": bool(body.get("draft"))})
    return {"job": job.to_json(), "estimate": board.cost_of(beats)}


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


@app.get("/api/events")
def events() -> StreamingResponse:
    def stream():
        channel = runner.subscribe()
        try:
            yield sse({"type": "hello", "container": runner.container.to_json(),
                       "jobs": runner.recent()})
            while True:
                try:
                    yield sse(channel.get(timeout=2.0))
                except queue.Empty:
                    yield sse({"type": "tick", "container": runner.container.to_json()})
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
    root = board_mod.reels_dir() / slug
    path = (root / name).resolve()
    if not str(path).startswith(str(root.resolve())) or not path.is_file():
        raise HTTPException(404, "no such file")
    return FileResponse(path)


# ## The built frontend
#
# Mounted last so it cannot shadow the API. Absent during development, when Vite serves it.

DIST = config.ROOT / "studio" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="studio")
