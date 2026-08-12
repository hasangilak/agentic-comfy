"""HTTP layer for the studio: board CRUD, job submission, and one SSE stream.

Runs locally, not on Modal. It holds every credential: the Google API key the language
model and the image server both go through, and the Modal proxy tokens that deploy a GPU.
That is the point -- nothing secret leaves this process, the browser talks only to it, which
is the only arrangement that works anyway, since a browser cannot attach auth headers to a
WebSocket.
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

from . import (agent, board as board_mod, comfy, config, crew, critique, develop, gemini,
               panels,
               papercut, pictures, planner, render, runtime, script, skills,
               staging as staging_mod, stills as stills_mod)
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
    run.log(job, f'[plan] {detail["beats"]} beats x {detail["seconds"]:.0f}s via {config.TEXT_MODEL}')
    board = agent.create(detail["concept"], detail["beats"], detail["seconds"],
                         log=lambda line: run.log(job, line))
    job.slug = board.slug  # the slug only exists once the title does
    run.log(job, f'[plan] "{board.data.get("title")}" -> {board.slug}')
    return {"slug": board.slug}


def handle_develop(job: Job, run: Runner) -> dict:
    """One turn of the interview that becomes a script.

    Unlike `plan`, the board already exists when this runs -- the route created it so the
    browser could navigate to the conversation before the model had said anything. So there is
    no slug to discover here, and a failed turn leaves a board with a transcript on it rather
    than nothing at all.
    """
    board = load(job.slug)
    run.log(job, f'[develop] {job.detail["message"]}')
    result = develop.turn(
        board, job.detail["message"],
        log=lambda line: run.log(job, line),
        announce=lambda: run.update(job, phase="writing the script"),
    )
    run.publish_board(board.slug)
    return result


def handle_chat(job: Job, run: Runner) -> dict:
    """One director turn: board edits, delegation to specialists, synthesis for the user."""
    board = load(job.slug)
    run.log(job, f'[director] {job.detail["message"]}')
    hooks, collector = agent_hooks(job, run, board.slug)
    director = runtime.build("director")
    selection = job.detail.get("selection") or []
    focus = ""
    if selection:
        focus = (
            f"\nThe director currently has beat(s) {', '.join(map(str, selection))} selected. "
            "Unqualified references like 'this one' mean those beats.\n"
        )
    turn = runtime.run(
        director, job.detail["message"], board=board,
        prelude=crew.prelude(board) + focus, hooks=hooks, state={}, collector=collector,
    )
    board = turn.board or board
    runtime.remember(board, "director", job.detail["message"], turn)
    run.publish_board(board.slug)
    return {"reply": turn.reply, "ops": turn.ops, "activity": turn.activity}


def agent_hooks(job: Job, run: Runner, slug: str, *,
                picture_label: str = "still") -> tuple[runtime.Hooks, runtime.ActivityCollector]:
    """Hooks shared by the director, crew, and single-agent jobs."""

    def emit(event: dict) -> None:
        run.emit_activity(job, event)

    collector = runtime.ActivityCollector(emit)
    hooks = runtime.Hooks(
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run, label=picture_label),
        announce=lambda: run.publish_board(slug),
        cancelled=lambda: job.cancelling,
        phase=lambda what: run.update(job, phase=what),
        activity=emit,
    )
    return hooks, collector


def still_progress(job: Job, run: Runner, label: str = "still"):
    """The image server's 0..1 per-frame fraction, on the fields the UI already has.

    The studio's progress strip is built around ComfyUI's step counters, so this scales onto
    those rather than teaching the UI a second shape. `label` says which image is being made,
    because a beat now has ten of them and "still for beat 3" is wrong for nine.
    """
    return lambda n, fraction: run.update(
        job, phase=f"{label} for beat {n}", beat=n,
        step=round(fraction * config.PAPERCUT_STEPS), step_max=config.PAPERCUT_STEPS)


def handle_asset(job: Job, run: Runner) -> dict:
    board = load(job.slug)
    return {"beats": stills_mod.generate(
        board, job.detail["beats"],
        gemini_model=job.detail.get("gemini_model"),
        gemini_image_size=job.detail.get("gemini_image_size"),
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run),
        announce=lambda: run.publish_board(board.slug),
        cancelled=lambda: job.cancelling,
    )}


def handle_still_chat(job: Job, run: Runner) -> dict:
    """One turn of the conversation about a single still, its re-render included.

    Its own job kind rather than a variant of `chat`, because it is a different unit of work:
    it addresses one beat, it can end in an image render, and the canvas has to be able to tell
    which node is busy. Same queue as everything else, so it cannot overlap a batch of stills
    rewriting the same prompt.
    """
    board = load(job.slug)
    n = int(job.detail["beat"])
    run.log(job, f'[gemini] beat {n} still: {job.detail["message"]}')
    return stills_mod.converse(
        board, n, job.detail["message"],
        attached=int(job.detail.get("attached") or 0),
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run),
        announce=lambda: run.publish_board(board.slug),
        cancelled=lambda: job.cancelling,
    )


def handle_ref_draw(job: Job, run: Runner) -> dict:
    """Draw one of a beat's reference pictures.

    Its own kind rather than a variant of `asset`: it addresses one picture rather than a set of
    beats, it never runs the still review -- a reference picture is supposed to differ from the
    cast -- and the canvas has to be able to tell which slot is busy. Same queue as everything
    else, so it cannot overlap a still batch or a render reading the same files.
    """
    board = load(job.slug)
    n = int(job.detail["beat"])
    index = job.detail.get("index")
    slot = pictures.draw(
        board, n, None if index is None else int(index),
        prompt=job.detail.get("prompt"),
        gemini_model=job.detail.get("gemini_model"),
        gemini_image_size=job.detail.get("gemini_image_size"),
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run, label="picture"),
        announce=lambda: run.publish_board(board.slug),
        cancelled=lambda: job.cancelling,
    )
    run.publish_board(board.slug)
    return {"beat": n, "index": slot}


def handle_ref_chat(job: Job, run: Runner) -> dict:
    """One turn of the conversation about one reference picture, its redraw included."""
    board = load(job.slug)
    n = int(job.detail["beat"])
    index = int(job.detail["index"])
    run.log(job, f'[gemini] beat {n} picture {index}: {job.detail["message"]}')
    return pictures.converse(
        board, n, index, job.detail["message"],
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run, label="picture"),
        announce=lambda: run.publish_board(board.slug),
        cancelled=lambda: job.cancelling,
    )


def handle_stage_draw(job: Job, run: Runner) -> dict:
    """Draw one of the reel's design sheets.

    Its own kind rather than a variant of `ref_draw` for the same reason that one is not a
    variant of `asset`: it addresses a different thing -- a reel-level design rather than a
    picture on one beat -- and the canvas has to be able to tell which sheet is busy. Same queue
    as everything else, so a sheet cannot be redrawn while a render is reading it.
    """
    board = load(job.slug)
    entry_id = str(job.detail["id"])
    staging_mod.draw(
        board, entry_id,
        prompt=job.detail.get("prompt"),
        gemini_model=job.detail.get("gemini_model"),
        gemini_image_size=job.detail.get("gemini_image_size"),
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run, label="design"),
        announce=lambda: run.publish_board(board.slug),
        cancelled=lambda: job.cancelling,
    )
    run.publish_board(board.slug)
    return {"id": entry_id}


def handle_stage_chat(job: Job, run: Runner) -> dict:
    """One turn of the conversation about one design sheet, its redraw included."""
    board = load(job.slug)
    entry_id = str(job.detail["id"])
    run.log(job, f'[gemini] design {entry_id}: {job.detail["message"]}')
    return staging_mod.converse(
        board, entry_id, job.detail["message"],
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run, label="design"),
        announce=lambda: run.publish_board(board.slug),
        cancelled=lambda: job.cancelling,
    )


def handle_panel_write(job: Job, run: Runner) -> dict:
    """Write the shot grammar for the whole reel in one model turn.

    Its own kind rather than a variant of `revise`: that one rewrites a story field of one beat
    from a note, and this writes a field the story does not have, for every beat at once, because
    the shot sizes have to vary across the film. One text turn, no image.
    """
    board = load(job.slug)
    run.log(job, "[gemini] writing the storyboard panels")
    written = panels.write(
        board, job.detail.get("beats"),
        log=lambda line: run.log(job, line),
        announce=lambda: run.publish_board(board.slug),
    )
    run.publish_board(board.slug)
    return {"beats": written}


def handle_panel_draw(job: Job, run: Runner) -> dict:
    """Draw storyboard panels, then rebuild the contact sheet.

    Its own kind for the reason `stage_draw` is not a variant of `ref_draw`: it addresses a
    different thing. A panel is a sketch of the shot that reaches no renderer, so this handler runs
    no review and touches no fingerprint -- and the canvas needs to be able to say which node's
    panel is being drawn.
    """
    board = load(job.slug)
    made = panels.draw_all(
        board, job.detail.get("beats"),
        log=lambda line: run.log(job, line),
        progress=still_progress(job, run, label="panel"),
        announce=lambda: run.publish_board(board.slug),
        cancelled=lambda: job.cancelling,
    )
    run.publish_board(board.slug)
    return {"beats": made}


def handle_revise(job: Job, run: Runner) -> dict:
    """Rewrite one beat's scene or action from a note about it.

    A job rather than a synchronous endpoint even though it is one short model call: it is a
    board edit, and the queue is what stops it landing in the middle of a conversation turn or
    a batch of stills that is already rewriting the same beat.
    """
    board = load(job.slug)
    n = int(job.detail["beat"])
    field = str(job.detail["field"])
    run.log(job, f'[gemini] beat {n} {field}: {job.detail["message"]}')
    result = agent.revise(board, n, field, job.detail["message"],
                          log=lambda line: run.log(job, line))
    run.publish_board(board.slug)
    return result


def handle_caption(job: Job, run: Runner) -> dict:
    board = load(job.slug)
    run.log(job, "[gemini] writing the caption")
    return {"caption": agent.caption(board)}


def handle_render(job: Job, run: Runner) -> dict:
    board = load(job.slug)
    return render.render(board, job.detail["beats"], job, run,
                         seconds=job.detail.get("seconds"))


def crew_hooks(job: Job, run: Runner, slug: str) -> runtime.Hooks:
    """The four callbacks every other handler builds, as the one object the crew passes down.

    `still_progress` gets the label "picture" rather than "still" because a crew run makes
    three different kinds -- an opening still, a design sheet, a storyboard panel -- and the
    progress strip should not claim all three are stills.
    """
    hooks, _collector = agent_hooks(job, run, slug, picture_label="picture")
    return hooks


def handle_crew(job: Job, run: Runner) -> dict:
    """Run the next crew phase -- or a whole stage when `ungated` is set.

    Default is one gated phase so the director can approve sheets or seams before the next
    specialists run. `ungated` is the escape hatch that burns through stages the way this
    handler used to. Cancellation still lands between rounds and between members --
    `Hooks.cancelled` reads `job.cancelling`, which is the same cooperative flag every other
    long handler uses.
    """
    board = load(job.slug)
    hooks = crew_hooks(job, run, board.slug)
    ungated = bool(job.detail.get("ungated"))
    phase = job.detail.get("phase") or None
    stage = job.detail.get("stage") or None
    if phase is None and stage and not ungated:
        # A stage button without ungated means "the next gate on this stage", not the whole cast.
        awaiting = crew.awaiting_phase(board)
        if awaiting and crew.PHASE_STAGE.get(awaiting) == stage:
            phase = awaiting
        else:
            remaining = [name for name in crew.phases_for(stage)
                         if name not in crew.crew_record(board)["done"]]
            phase = remaining[0] if remaining else None
    turns = crew.run(board, note=job.detail.get("note") or "",
                     stop_after=stage if ungated else None,
                     phase=phase, ungated=ungated, hooks=hooks)
    board = load(job.slug)
    run.publish_board(board.slug)
    summary = crew.plan_summary(board)
    return {"turns": [{"agent": turn.agent, "reply": turn.reply, "stopped": turn.stopped,
                       "rounds": turn.rounds, "ops": turn.ops} for turn in turns],
            "stage": summary["stage"], "awaiting": summary["awaiting"], "done": summary["done"]}


def handle_agent(job: Job, run: Runner) -> dict:
    """One named agent, one message. Same hooks, same queue as everything else."""
    board = load(job.slug)
    turn = crew.one(job.detail["agent"], board, job.detail["message"],
                    hooks=crew_hooks(job, run, board.slug))
    run.publish_board(board.slug)
    return {"agent": turn.agent, "reply": turn.reply, "stopped": turn.stopped,
            "rounds": turn.rounds, "ops": turn.ops,
            "stage": crew.next_stage(load(job.slug))}


for kind, handler in (
    ("plan", handle_plan), ("develop", handle_develop),
    ("chat", handle_chat), ("asset", handle_asset),
    ("still_chat", handle_still_chat), ("revise", handle_revise),
    ("ref_draw", handle_ref_draw), ("ref_chat", handle_ref_chat),
    ("stage_draw", handle_stage_draw), ("stage_chat", handle_stage_chat),
    ("panel_write", handle_panel_write), ("panel_draw", handle_panel_draw),
    ("caption", handle_caption), ("render", handle_render),
    ("crew", handle_crew), ("agent", handle_agent),
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


@app.get("/api/brief")
def authoring_brief() -> dict:
    """The script-authoring prompt itself, so the studio can show what it is interviewing about.

    Costs nothing and calls nothing. It exists because the alternative -- paraphrasing the rules
    in the UI -- is the second copy of the specification this codebase keeps refusing to have.
    """
    try:
        return {"markdown": planner.template()}
    except planner.NoTemplate as missing:
        raise HTTPException(404, str(missing))


# Declared before the `/api/reels/{slug}` routes, exactly as `import` is: FastAPI matches in
# declaration order, so "develop" would otherwise be read as the name of a reel.
@app.post("/api/reels/develop")
def develop_reel(body: dict = Body(...)) -> dict:
    """Begin a film by talking about it.

    Answers synchronously with a board that has no beats yet, so the browser can navigate to
    the conversation before the model has said a word -- and so the transcript is durable from
    the first message rather than living in a tab until it is worth keeping.
    """
    try:
        board = develop.start(str(body.get("message") or ""))
    except develop.DevelopError as refused:
        raise HTTPException(refused.status, str(refused))
    job = runner.submit("develop", board.slug, {"message": str(body.get("message") or "")})
    runner.publish_board(board.slug)
    return {"slug": board.slug, "board": board_json(board), "job": job.to_json()}


@app.post("/api/reels/{slug}/develop")
def develop_turn(slug: str, body: dict = Body(...)) -> dict:
    """One more turn of the interview, on a board that already carries it."""
    board = load(slug)
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(422, "say something")
    try:
        develop.developable(board)
    except develop.DevelopError as refused:
        raise HTTPException(refused.status, str(refused))
    return {"job": runner.submit("develop", board.slug, {"message": message}).to_json()}


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
    if "medium" in body:
        # Validated rather than stored as typed: the medium reaches nine places in a render, and
        # a typo would silently fall back to paper while the board said something else.
        wanted = str(body["medium"] or "").strip()
        if wanted not in config.MEDIUMS:
            raise HTTPException(422, f"medium must be one of {', '.join(config.MEDIUMS)}")
        # The default is stored by being ABSENT, never by being written. `Board.medium` reads a
        # missing key as paper cutout and `medium_digest` hashes it to nothing, so one
        # representation is what keeps "a board that never named a medium" and "a board set back
        # to the default" the same board -- and keeps a document clean of a key that says nothing.
        if wanted == config.DEFAULT_MEDIUM:
            board.data.pop("medium", None)
        else:
            board.data["medium"] = wanted
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
    # `panel` is in here with the story fields even though it is not one: it is the shot grammar
    # the storyboard sketch is drawn from, hand-editable exactly as the others are. Editing it
    # changes no fingerprint, because a panel reaches no renderer.
    # `blocking` is in here with them and, unlike `panel`, editing it DOES move the beat's
    # fingerprint -- it is in the video prompt. That is handled in `own_fingerprint`; nothing
    # extra is needed here, but it is the difference between the two neighbours on this line.
    for key in ("scene", "action", "asset_prompt", "panel", "blocking"):
        if key in body:
            beat[key] = str(body[key])
    if "gemini_model" in body:
        model = str(body["gemini_model"] or "")
        if model not in config.GEMINI_IMAGE_MODELS:
            raise HTTPException(422, "unsupported Gemini image model")
        beat["gemini_model"] = model
    if "gemini_image_size" in body:
        image_size = str(body["gemini_image_size"] or "")
        if image_size not in config.GEMINI_IMAGE_SIZES:
            raise HTTPException(422, "unsupported Gemini image size")
        beat["gemini_image_size"] = image_size
    if (beat.get("gemini_model") == "gemini-3.1-flash-lite-image"
            and beat.get("gemini_image_size") not in (None, "1K")):
        raise HTTPException(422, "Nano Banana 2 Lite only supports 1K output")
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


def gemini_options(body: dict) -> tuple[str | None, str | None]:
    """Validate optional per-request Gemini overrides from the canvas."""
    model = body.get("model") or body.get("gemini_model")
    image_size = body.get("imageSize") or body.get("gemini_image_size")
    if model is not None and model not in config.GEMINI_IMAGE_MODELS:
        raise HTTPException(422, "unsupported Gemini image model")
    if image_size is not None and image_size not in config.GEMINI_IMAGE_SIZES:
        raise HTTPException(422, "unsupported Gemini image size")
    if model == "gemini-3.1-flash-lite-image" and image_size not in (None, "1K"):
        raise HTTPException(422, "Nano Banana 2 Lite only supports 1K output")
    return (str(model) if model is not None else None,
            str(image_size) if image_size is not None else None)


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
    model, image_size = gemini_options(body)
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

    job = runner.submit("asset", slug, {
        "beats": beats,
        "gemini_model": model,
        "gemini_image_size": image_size,
    })
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/beats/{n}/asset/chat")
async def still_chat(slug: str, n: int, message: str = Form(""),
                     files: list[UploadFile] = File(default=[])) -> dict:
    """Say what is wrong with one still -- optionally showing it a picture -- and have it redrawn.

    The board conversation edits the story; this one edits a picture. Both run on the same
    model with the same vision head -- the difference is what is in the prompt: this turn
    is shown the still itself, everything the still is drawn from, and everything already said
    about that one image, and what it writes back is the beat's `asset_prompt`.

    Multipart rather than JSON because a note can arrive with pictures attached, and those go
    through `store_refs`: the same place the node's ⤒ add picture sends them, so they become
    part of the beat rather than context for one turn. That is the only way an attachment
    reaches the renderer at all -- `Board.still_pictures` reads the beat, so a transient image
    would steer the model's words and nothing else. It carries the same consequence as that
    button, and it is the reason the modal says so: the beat moves onto the reference join.

    Refused for the same reasons a generation is, from the same place (`stills.discussable`),
    checked BEFORE anything is stored and here rather than only in the job, so a stale tab gets
    a 409 instead of a job that fails a minute later.
    """
    board = load(slug)
    message = (message or "").strip()
    sent = [file for file in files or [] if file is not None and file.filename]
    if not message and not sent:
        raise HTTPException(422, "empty message")
    try:
        stills_mod.discussable(board, n)
    except stills_mod.StillsError as refused:
        raise HTTPException(refused.status, str(refused))
    attached = await store_refs(board, n, sent)
    if attached:
        runner.publish_board(slug)
    job = runner.submit("still_chat", slug,
                        {"beat": n, "message": message, "attached": attached})
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/beats/{n}/text")
def revise_beat(slug: str, n: int, body: dict = Body(...)) -> dict:
    """Rewrite this beat's scene or action from a note about it, rather than typing it.

    The chat panel can already do this -- it is one `set_beat` call -- but only after working
    out from the sentence which beat and which field were meant, which is the part that goes
    wrong on a board where every beat says something similar. Here both are in the URL, so the
    turn is spent on the writing.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    field = str(body.get("field") or "")
    if field not in agent.REVISE_FIELDS:
        raise HTTPException(
            422, f"field must be one of {', '.join(agent.REVISE_FIELDS)}")
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(422, "say what should be different about it")
    job = runner.submit("revise", slug, {"beat": n, "field": field, "message": message})
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/beats/{n}/asset")
async def upload_asset(slug: str, n: int, file: UploadFile = File(...),
                       source: str = Form(board_mod.SOURCE_REFERENCE)) -> dict:
    """Use your own image as a beat's still.

    Also the answer when the local image server is not running, or cannot run at all: nothing
    else generates stills, so an upload is the only other way a beat gets its opening frame.

    `source` says what the picture is FOR, because a supplied image answers three different
    questions:

      * "reference" (the default) -- it is the composition this beat OPENS on, handed to ref2va
        as <Picture 1> alongside the reel's cast reference. This is the ordinary cut.
      * "asset" -- the same thing, but as an fl2va keyframe, so the opening frame is exact
        rather than conditioned towards. Pick it when the frame itself has to land.
      * "bridge" -- it is where this beat ENDS, and the beat still opens on the previous
        clip's final frame. Use this when the shot has to carry straight on from the beat
        before AND arrive at a composition you drew.

    All three store the still in the same place; only the join changes, and with it whether the
    render hands it over as a keyframe or as a reference.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    if source not in (board_mod.SOURCE_REFERENCE, board_mod.SOURCE_ASSET,
                      board_mod.SOURCE_BRIDGE):
        raise HTTPException(
            422, "an uploaded still is this beat's opening composition ('reference'), its exact "
                 "opening keyframe ('asset'), or the frame it lands on ('bridge')",
        )
    if source == board_mod.SOURCE_BRIDGE and board.upstream(n) is None:
        raise HTTPException(422, "the first beat has nothing to continue from, so its still "
                                 "can only be its opening frame")
    if source == board_mod.SOURCE_REFERENCE and board.carries_motion(board.beat(n)):
        raise HTTPException(422, "this scene opens on the tail of the clip before it, so a "
                                 "still of its own would never be used. Turn off carrying "
                                 "first, or upload this as a reference picture instead.")
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

    Pictures of the cast, the set, a prop -- the things that must look the same in this shot as
    everywhere else in the film. They ride alongside whatever the beat already has: on the
    ordinary cut that is its own still as <Picture 1> and the reel's cast reference as
    <Picture 2>, so these land from <Picture 3> on. The prompt names them by position in the
    order they were added, which is why appending is deliberate -- a new picture goes after the
    ones already there rather than shuffling the numbering under the notes describing them.

    The first few also condition the beat's STILL, which takes far fewer pictures than the video
    model (`config.MAX_STILL_REFS`, the cast reference included) -- see `Board.still_pictures`.
    That is deliberate: the still is what the clip's opening frames are anchored to, so a picture
    the clip is held to and the frame it opens on was not is a disagreement about the same puppet.

    Uploads cost nothing to place, exactly like an uploaded still. What they cost is render
    time: reference tokens ride through every sampling step, so nine pictures is a slower
    clip than one, and the still they also steer takes longer per picture too.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    if not files:
        raise HTTPException(422, "no images sent")
    stored = await store_refs(board, n, files)
    runner.publish_board(slug)
    return {"board": board_json(board), "stored": stored}


async def store_refs(board: board_mod.Board, n: int, files: list[UploadFile]) -> int:
    """Store reference pictures on a beat and put it on the reference join. Returns how many.

    Shared with the still conversation, which can carry attachments: there is one way a picture
    becomes part of a beat, so a note with an image behind it and the ⤒ add picture button
    cannot end up meaning different things.

    Saves the board, and does not publish -- the two callers announce at different moments.
    """
    if not files:
        # Before the join is touched, because an empty attachment list must not quietly turn a
        # continuation into a cut on its way past.
        return 0

    # The join moves FIRST, before a single file is stored, because the budget depends on it:
    # two of the model's nine slots fill themselves on a reference beat, and `next_ref_index`
    # reads the join to know that. Set afterwards, a chained beat would be offered all nine,
    # and the last two uploads would land on disk only to be truncated out of the render the
    # moment the switch caught up. It also means a partial upload that hits the cap below still
    # leaves the beat on the join its stored pictures belong to.
    board.beat(n)["source"] = board_mod.SOURCE_REFERENCE

    stored = 0
    for file in files:
        index = board.next_ref_index(n)
        if index is None:
            board.save()
            # Partial success is the honest answer: the images that fitted are on disk, and
            # saying so beats either silently dropping the rest or rejecting the whole batch.
            automatic = config.MAX_REF_IMAGES - board.ref_budget(n)
            raise HTTPException(
                409,
                f"beat {n} is full: {config.MAX_REF_IMAGES} pictures is the model's limit"
                + (f", and {automatic} of them are this scene's own still and the reel's cast "
                   "reference" if automatic else "")
                + f". {stored} of this upload were stored; remove one to add another.",
            )
        await store_upload(file, board.ref_path(n, index))
        stored += 1

    board.save()
    return stored


# Declared BEFORE the `/refs/{index}` routes below, because FastAPI matches in declaration
# order and would otherwise try to parse "draw" as an int and answer 422.
@app.post("/api/reels/{slug}/beats/{n}/refs/draw")
def draw_new_ref(slug: str, n: int, body: dict = Body(...)) -> dict:
    """Draw a NEW reference picture for this beat, from a prompt alone.

    The join moves here rather than in the job, for the same reason `store_refs` moves it before
    storing a file: `next_ref_index` reads the join to know that two of the model's nine slots
    fill themselves on a reference beat, so a chained beat asked first would be told it has all
    nine. The canvas warns about the move before sending.

    The asymmetry with `store_refs` is worth naming: there, a partial upload leaves files behind
    that justify the moved join; here a draw that fails leaves the beat on `reference` with
    nothing new on it. That is visible on the node and one click to undo, and moving the join
    only on success would make the budget check above it a lie.

    No empty slot is ever created. `Board.ref_paths` is file-existence based, so a picture with a
    prompt and no file is not a thing this board can represent -- the job renders first and the
    slot exists because the file does.
    """
    board = load(slug)
    model, image_size = gemini_options(body)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    prompt = " ".join(str(body.get("prompt") or "").split()).strip()
    if not prompt:
        raise HTTPException(422, "say what the picture should be")
    board.beat(n)["source"] = board_mod.SOURCE_REFERENCE
    board.save()
    try:
        pictures.drawable(board, n, None)
    except pictures.PicturesError as refused:
        raise HTTPException(refused.status, str(refused))
    runner.publish_board(slug)
    job = runner.submit("ref_draw", slug, {
        "beat": n,
        "index": None,
        "prompt": prompt,
        "gemini_model": model,
        "gemini_image_size": image_size,
    })
    return {"job": job.to_json()}


@app.patch("/api/reels/{slug}/beats/{n}/refs/{index}")
def describe_ref(slug: str, n: int, index: int, body: dict = Body(...)) -> dict:
    """Say what one reference picture is FOR, and what it should be drawn as.

    `prompt` is the fix for the two-of-the-same-character problem: shown a picture of the cast
    standing in the finished set, ref2va reproduces it AND acts the beat out with a second
    copy of the same puppet. Told "<Picture 1> is the same single Moth that performs the
    action, not an extra one", it collapses them back into one.

    `draw` is the other half and a different register: what Gemini is asked for when this picture
    is drawn again. Two fields rather than one because a good draw prompt -- "a close-up of an
    iron-grey club on flat black" -- is a terrible end to the sentence "<Picture 3> is ...".

    One route for both, because it already meant "say things about picture `index`" and `draw`
    is one more thing to say. Only the keys present are written, so the two fields can be edited
    independently by two different controls.

    `prompt` marks the beat stale, because those words go into the render. `draw` does not, and
    deliberately: it produces a picture, and the picture's own content hash is already in the
    fingerprint -- exactly as `asset_prompt` is left out because the still it made is hashed.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    try:
        if "prompt" in body:
            board.set_ref_prompt(n, index, str(body.get("prompt") or ""))
        if "draw" in body:
            board.set_ref_draw(n, index, str(body.get("draw") or ""))
    except IndexError:
        raise HTTPException(404, f"beat {n} has no reference picture {index}")
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.post("/api/reels/{slug}/beats/{n}/refs/{index}/draw")
def redraw_ref(slug: str, n: int, index: int, body: dict = Body(default={})) -> dict:
    """Draw an existing reference picture again, from the prompt already stored on it.

    The prompt may be sent with the draw request, which lets an uploaded picture be edited in
    one action. A stored prompt remains the fallback for pictures that have already been drawn.
    """
    board = load(slug)
    model, image_size = gemini_options(body)
    draw_prompt = " ".join(str(body.get("prompt") or "").split()).strip() or None
    try:
        pictures.drawable(board, n, index, draw_prompt)
    except pictures.PicturesError as refused:
        raise HTTPException(refused.status, str(refused))
    job = runner.submit("ref_draw", slug, {
        "beat": n,
        "index": index,
        "prompt": draw_prompt,
        "gemini_model": model,
        "gemini_image_size": image_size,
    })
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/beats/{n}/refs/{index}/chat")
def chat_ref(slug: str, n: int, index: int, body: dict = Body(...)) -> dict:
    """Say what should be different about one reference picture.

    JSON, not multipart, unlike the still's conversation. There an attachment means "here is
    what I mean" and is stored, because `Board.still_pictures` reads the beat. Here the picture
    IS the subject, and a file sent with the note would become a tenth reference nobody asked
    for -- adding a picture is what the tray is for.
    """
    board = load(slug)
    message = " ".join(str(body.get("message") or "").split()).strip()
    if not message:
        raise HTTPException(422, "say what should be different about the picture")
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    if not board.ref_path(n, index).is_file():
        raise HTTPException(404, f"beat {n} has no reference picture {index}")
    job = runner.submit("ref_chat", slug, {"beat": n, "index": index, "message": message})
    return {"job": job.to_json()}


@app.delete("/api/reels/{slug}/beats/{n}/refs/{index}")
def remove_ref(slug: str, n: int, index: int) -> dict:
    """Drop one reference picture, by the number the prompt calls it.

    The survivors are renumbered to close the gap, because the prompt numbers them by
    position: leaving a hole would have the model told about a <Picture 2> that is really the
    third image, or a picture with no tag at all. Their descriptions, their draw prompts and
    their conversations move with them, and any @-mention of the departed one is rewritten to
    what it was for.

    Refused while a picture job for this beat is in flight: that job captured its index when it
    was queued, and the renumber would have it drawing into whatever slid up into the slot.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    # Narrowed to this beat's picture jobs rather than reusing `require_structure_idle`, which
    # blocks on anything at all: removing a picture from scene 3 while scene 5 renders is fine.
    busy = next(
        (
            job for job in runner.jobs.values()
            if job.slug == slug and job.kind in ("ref_draw", "ref_chat")
            and job.state in ("queued", "running")
            and int(job.detail.get("beat") or 0) == n
        ),
        None,
    )
    if busy:
        raise HTTPException(
            409,
            f"a picture on scene {n} is being drawn. Removing one now would renumber the rest "
            "underneath it -- wait for the job to finish.",
        )
    try:
        board.remove_ref(n, index)
    except IndexError:
        raise HTTPException(404, f"beat {n} has no reference picture {index}")
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


# ## Staging
#
# The reel's cast and sets: named, written down, drawn once, bound to the beats that contain
# them. Reel-scoped, which is the whole difference from the per-beat pictures above -- these
# routes carry no beat number and the binding is the one that does.


SAFE_STAGE_ID = re.compile(r"^[0-9a-f]{4,12}$")


def stage_id(entry_id: str) -> str:
    """An id names one file in the reel directory, so it is checked before it is used as one."""
    if not SAFE_STAGE_ID.match(entry_id):
        raise HTTPException(404, f"no design called {entry_id!r}")
    return entry_id


def stage_busy(slug: str, entry_id: str) -> None:
    """Refuse while a job for this sheet is queued or running.

    Narrowed to this one design rather than reusing `require_structure_idle`, which blocks on
    anything at all: removing a design while an unrelated scene renders is fine. What is not fine
    is deleting the file a queued draw is about to write into, or redrawing one twice at once.
    """
    busy = next(
        (
            job for job in runner.jobs.values()
            if job.slug == slug and job.kind in ("stage_draw", "stage_chat")
            and job.state in ("queued", "running")
            and str(job.detail.get("id") or "") == entry_id
        ),
        None,
    )
    if busy:
        raise HTTPException(
            409, f"that design is already being {'drawn' if busy.kind == 'stage_draw' else 'discussed'}. Wait for the job to finish.",
        )


@app.post("/api/reels/{slug}/staging")
def add_staging(slug: str, body: dict = Body(...)) -> dict:
    """Add one thing to the reel's design bible -- a character, a set, or a prop.

    Synchronous and free: this mints an entry, it does not draw one. A sheet appears when it is
    drawn or uploaded, which is the same rule a beat's reference pictures follow -- an entry with
    a placeholder image would be a picture `staging_pictures` picks up and a render pays
    reference tokens for.
    """
    board = load(slug)
    name = " ".join(str(body.get("name") or "").split())
    if not name:
        raise HTTPException(422, "give the design a name -- it is what the prompts call it")
    try:
        entry = board.add_stage(
            kind=str(body.get("kind") or config.STAGE_CHARACTER),
            name=name,
            note=str(body.get("note") or ""),
            draw=str(body.get("draw") or ""),
        )
    except ValueError as refused:
        raise HTTPException(422, str(refused))
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board), "id": entry["id"]}


@app.patch("/api/reels/{slug}/staging/{entry_id}")
def describe_staging(slug: str, entry_id: str, body: dict = Body(...)) -> dict:
    """Rename a design, say what it IS, or say what it should be drawn as.

    `name` and `note` both reach the render -- together they are `Board.stage_role`, the sentence
    every prompt is told about this design -- so editing either marks every beat that binds it
    stale. `draw` does not, deliberately: it produces a sheet, and the sheet's own content hash is
    already in `staging_digest`, exactly as `asset_prompt` is left out because the still it made
    is hashed.

    Only the keys present are written, so four independent controls can edit four fields without
    each one having to send the other three back.
    """
    board = load(slug)
    try:
        entry = board.stage_entry(stage_id(entry_id))
    except KeyError:
        raise HTTPException(404, f"no design called {entry_id!r} on this reel")
    if "kind" in body:
        if body["kind"] not in config.STAGE_KINDS:
            raise HTTPException(422, f"kind must be one of {', '.join(config.STAGE_KINDS)}")
        entry["kind"] = body["kind"]
    for key in ("name", "note", "draw"):
        if key in body:
            entry[key] = " ".join(str(body[key] or "").split())
    if not board.stage_field(entry, "name"):
        raise HTTPException(422, "a design needs a name -- it is what the prompts call it")
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.post("/api/reels/{slug}/staging/{entry_id}/sheet")
async def upload_staging_sheet(slug: str, entry_id: str, file: UploadFile = File(...)) -> dict:
    """Use your own image as a design sheet.

    Also the answer when the image server is not running: nothing else draws one, so an upload is
    the only other way a design gets a picture. Unlike a beat's still there is no join to move --
    a bound design reaches every join, as pictures where there are picture slots and as words
    everywhere else -- so this changes nothing about the story.
    """
    board = load(slug)
    entry_id = stage_id(entry_id)
    try:
        board.stage_entry(entry_id)
    except KeyError:
        raise HTTPException(404, f"no design called {entry_id!r} on this reel")
    stage_busy(slug, entry_id)
    await store_upload(file, board.stage_path(entry_id))
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.post("/api/reels/{slug}/staging/{entry_id}/draw")
def draw_staging(slug: str, entry_id: str, body: dict = Body(default={})) -> dict:
    """Draw or redraw one design sheet.

    The prompt may be sent with the request, which lets an uploaded sheet be edited in one
    action; a stored prompt is the fallback for one that has already been drawn.
    """
    board = load(slug)
    entry_id = stage_id(entry_id)
    model, image_size = gemini_options(body)
    prompt = " ".join(str(body.get("prompt") or "").split()).strip() or None
    stage_busy(slug, entry_id)
    try:
        staging_mod.drawable(board, entry_id, prompt)
    except staging_mod.StagingError as refused:
        raise HTTPException(refused.status, str(refused))
    job = runner.submit("stage_draw", slug, {
        "id": entry_id,
        "prompt": prompt,
        "gemini_model": model,
        "gemini_image_size": image_size,
    })
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/staging/{entry_id}/chat")
def chat_staging(slug: str, entry_id: str, body: dict = Body(...)) -> dict:
    """Say what should be different about one design sheet.

    JSON, not multipart. In a still's conversation an attachment means "here is what I mean" and
    is stored, because `Board.still_pictures` reads the beat; here the sheet IS the subject, and a
    file sent with the note would have to become a second design nobody asked for.
    """
    board = load(slug)
    entry_id = stage_id(entry_id)
    message = " ".join(str(body.get("message") or "").split()).strip()
    if not message:
        raise HTTPException(422, "say what should be different about the design")
    try:
        board.stage_entry(entry_id)
    except KeyError:
        raise HTTPException(404, f"no design called {entry_id!r} on this reel")
    if not board.stage_path(entry_id).is_file():
        raise HTTPException(422, "draw or upload the sheet first, then say what to change")
    stage_busy(slug, entry_id)
    job = runner.submit("stage_chat", slug, {"id": entry_id, "message": message})
    return {"job": job.to_json()}


@app.delete("/api/reels/{slug}/staging/{entry_id}")
def remove_staging(slug: str, entry_id: str) -> dict:
    """Drop one design, its sheet, every binding to it, and every mention of it.

    All four move together or the board starts lying -- a beat still numbering a sheet that is
    gone, or a sentence naming a design the expander would silently drop at render time. The
    token is rewritten into what the design WAS, which keeps the sentence readable.
    """
    board = load(slug)
    entry_id = stage_id(entry_id)
    stage_busy(slug, entry_id)
    try:
        board.remove_stage(entry_id)
    except KeyError:
        raise HTTPException(404, f"no design called {entry_id!r} on this reel")
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board)}


@app.put("/api/reels/{slug}/beats/{n}/staging")
def bind_staging(slug: str, n: int, body: dict = Body(...)) -> dict:
    """Say which of the reel's designs appear in this scene, in the order they are numbered.

    Replaces rather than appends: the control is a set of toggles, and "which of these does this
    shot contain" is one answer rather than a series of additions.

    Unlike adding a picture, this never moves the join and so carries no warning. A picture only
    reaches a render through the reference join; a bound design reaches every join -- as
    <Picture i> where there are picture slots, and as a sentence everywhere else.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise HTTPException(422, "send the designs this scene uses as `ids`")
    bound = board.bind_stage(n, [str(i) for i in ids])
    board.save()
    runner.publish_board(slug)
    return {"board": board_json(board), "staging": bound}


# ## Storyboard panels
#
# The one set of routes here that spends nothing the board can lose. A panel reaches no renderer,
# so none of these can make a rendered beat stale, and `gemini_options` is deliberately NOT wired
# to any of them: the whole point of the pass is the cheapest model, and letting the canvas pass
# Pro through would quietly undo it. See `panels.py`.


@app.post("/api/reels/{slug}/panels/text")
def write_panels(slug: str, body: dict = Body(default={})) -> dict:
    """Have the model write the shot grammar for every beat, or for the ones named.

    One text turn, no image. The default is the whole reel rather than the beats with no
    panel yet, because the shot sizes are judged against each other -- a pass over beat 4 alone
    cannot see that beats 1 to 3 are already three wide shots.
    """
    load(slug)  # 404 before a job is queued
    beats = body.get("beats")
    if beats is not None and not isinstance(beats, list):
        raise HTTPException(422, "send the scenes to write as `beats`")
    job = runner.submit("panel_write", safe_slug(slug), {
        "beats": None if beats is None else [int(n) for n in beats],
    })
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/panels")
def draw_panels(slug: str, body: dict = Body(default={})) -> dict:
    """Draw the panels: the named beats, or every beat with text and no panel drawn yet."""
    board = load(slug)
    beats = body.get("beats")
    if beats is not None and not isinstance(beats, list):
        raise HTTPException(422, "send the scenes to draw as `beats`")
    wanted = None if beats is None else [int(n) for n in beats]
    if wanted is not None:
        for n in wanted:
            try:
                panels.drawable(board, n)
            except panels.PanelsError as refused:
                raise HTTPException(refused.status, str(refused))
    job = runner.submit("panel_draw", safe_slug(slug), {"beats": wanted})
    return {"job": job.to_json()}


@app.post("/api/reels/{slug}/beats/{n}/panel")
def draw_panel(slug: str, n: int, body: dict = Body(default={})) -> dict:
    """Draw or redraw one beat's panel.

    Refuses up front when nothing has been written for it, rather than queueing a job that will
    only be able to say the same thing a minute later.
    """
    board = load(slug)
    if not any(b["n"] == n for b in board.beats):
        raise HTTPException(404, f"beat {n} not in {slug}")
    if "panel" in body:
        board.beat(n)["panel"] = str(body["panel"])
        board.save()
        runner.publish_board(slug)
    try:
        panels.drawable(board, n)
    except panels.PanelsError as refused:
        raise HTTPException(refused.status, str(refused))
    job = runner.submit("panel_draw", safe_slug(slug), {"beats": [n]})
    return {"job": job.to_json()}


@app.delete("/api/reels/{slug}/beats/{n}/panel")
def remove_panel(slug: str, n: int) -> dict:
    """Throw one panel away and rebuild the sheet without it.

    Synchronous, and with none of the guards `DELETE /beats/{n}/refs/{index}` carries: there is no
    position to renumber (`panel_path` is keyed by the beat number) and nothing downstream is
    conditioned on the file, so the worst a queued draw can do is put the panel back.
    """
    board = load(slug)
    try:
        panels.remove(board, n)
    except panels.PanelsError as refused:
        raise HTTPException(refused.status, str(refused))
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


# ## The crew
#
# The agents a reel needs, in stages, and the rule that says which is next (`crew.py`). A stage
# is a cast rather than one agent, and there is deliberately no fourth stage: there
# is no agent for the render stage and no route here that could start one, which is the same
# boundary `crew.STAGES` draws and `tools.py` enforces by not importing the render modules.


@app.get("/api/agents")
def list_agents() -> dict:
    """Every agent, with its settings and its tools. Synchronous and free -- loads no model."""
    return {"agents": crew.catalogue(), "stages": list(crew.STAGES),
            # The cast per stage, with the style role already resolved -- against no board, so
            # this is the default medium's artist. A slug's own casts are in `plan_of` below.
            "cast": {name: crew.cast_for(name, None) for name in crew.STAGES},
            "phases": {
                stage: [{"id": phase, "agents": crew.cast_for_phase(phase, None)}
                        for phase, _roles in crew.STAGE_PHASES[stage]]
                for stage in crew.STAGES
            },
            "lenses": crew.lenses(),
            "mediums": [{"key": entry.key, "name": entry.name}
                        for entry in config.MEDIUMS.values()]}


@app.post("/api/reels/{slug}/crew")
def run_crew(slug: str, body: dict = Body(default={})) -> dict:
    """Run the next gated crew phase, or a whole stage when `ungated` is set.

    `require_structure_idle` first, and not only out of politeness: the script agent adds and
    removes beats, and beat positions are how every queued job names its work.
    """
    board = load(slug)
    require_structure_idle(slug)
    stage = body.get("stage")
    phase = body.get("phase")
    ungated = bool(body.get("ungated"))
    if stage is not None and stage not in crew.STAGES:
        raise HTTPException(422, f"stage has to be one of {', '.join(crew.STAGES)}")
    if phase is not None and phase not in crew.PHASE_STAGE:
        raise HTTPException(422, f"phase has to be one of {', '.join(crew.PHASES)}")
    if phase is not None and stage is not None and crew.PHASE_STAGE[phase] != stage:
        raise HTTPException(422, f"phase {phase!r} belongs to {crew.PHASE_STAGE[phase]}, not {stage}")
    summary = crew.plan_summary(board)
    job = runner.submit("crew", slug,
                        {"note": str(body.get("note") or ""), "stage": stage,
                         "phase": phase, "ungated": ungated,
                         # Resolved once, at submit, so the strip can show the whole cast
                         # before the first agent has said anything -- and so what it shows is
                         # what this run actually decided rather than a re-read that could
                         # answer differently once the board starts moving.
                         "plan": summary["plan"]})
    return {"job": job.to_json(), **summary, "medium": board.medium(), "lenses": crew.lenses()}


@app.post("/api/reels/{slug}/agents/{name}")
def run_agent(slug: str, name: str, body: dict = Body(...)) -> dict:
    """One agent, one message.

    The skill is loaded here rather than in the handler so an unknown name is a 404 with a
    sentence, instead of a job that sits in the queue and then fails a minute later -- the same
    reason `draw_panel` calls `panels.drawable` before submitting.
    """
    load(slug)
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(422, "say something for the agent to do")
    try:
        skills.load(name)
    except skills.SkillError as refused:
        raise HTTPException(refused.status, str(refused))
    job = runner.submit("agent", slug, {"agent": name, "message": message})
    return {"job": job.to_json()}


@app.get("/api/reels/{slug}/crew")
def crew_plan(slug: str) -> dict:
    """What the crew would do to this board next, and who would do it. Free, calls nothing.

    Resolved against THIS board, so the style role has already become the artist its medium
    calls for. Includes gated phases and the awaiting cursor. `GET /api/agents` answers the
    same question with no board, which is the default medium's answer and not necessarily
    this reel's.
    """
    board = load(slug)
    return {**crew.plan_summary(board), "medium": board.medium(), "lenses": crew.lenses()}


@app.post("/api/reels/{slug}/beats/{n}/inspect")
def inspect_still(slug: str, n: int, body: dict = Body(default={})) -> dict:
    """Look at one still through one lens and file the verdict. Synchronous, one vision call.

    Not a job: it is a single structured call with a bounded cost, and the answer is what the
    caller asked for rather than something to watch happen. The three lenses of a crew run go
    through the same function -- this route is for a director who wants one of them on one
    still without running a stage.
    """
    board = load(slug)
    lens = str(body.get("lens") or "").strip()
    try:
        verdict = critique.look(board, n, lens)
    except critique.InspectError as refused:
        raise HTTPException(refused.status, str(refused))
    except gemini.GeminiError as failed:
        raise HTTPException(502, str(failed))
    critique.record(board, n, verdict)
    board.save()
    runner.publish_board(slug)
    return {"verdict": verdict, "board": board_json(board)}


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
        # Neither is fatal, but they fail in different ways and the UI has to be able to say
        # which: with no model there is no script, no conversation and no caption; with no
        # image server the stills have to be uploads. The language probe is itself cached in
        # `gemini.health`, so polling this route does not mean a request per poll.
        "stills": {
            "backend": "papercut" if papercut.available() else "none",
            "papercut_url": config.PAPERCUT_URL,
        },
        "language": gemini.health() or {"url": config.GEMINI_API_URL, "model": config.TEXT_MODEL,
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
    @app.get("/reels/{slug}/{stage}", include_in_schema=False)
    def reel_page(slug: str, stage: str = "") -> FileResponse:
        """Serve the SPA entry point for an addressable board, at any of its four stages.

        The second route is not optional decoration. Vite's dev server falls back to index.html
        for any unmatched path, so `/reels/x/storyboard` works in development whatever this
        says -- and 404s in the built app, which is the shape a missing SPA route always takes.
        """
        load(slug)  # Preserve a real 404 for malformed or missing board URLs.
        return FileResponse(DIST / "index.html")

    app.mount("/", StaticFiles(directory=DIST, html=True), name="studio")
