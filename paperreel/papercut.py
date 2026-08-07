"""Opening stills from Papercut Studio -- the local mflux renderer in `image/`.

Why this exists: `agy`'s image tool allows roughly FIVE generations per five-hour window,
and that single number shaped most of this pipeline. It is why chaining is the default, why
a reel is designed to need one image rather than one per beat, and why the studio has a
switch for turning generation off entirely. Papercut Studio runs `flux2-klein-4b` through
mflux on this machine: no quota, no key, nothing leaves the laptop, and a still costs
seconds instead of a slot.

The seam is HTTP on loopback, not shared code. Papercut owns prompt composition, the render
lock and the progress scraping; this module owns the board and the joins. Neither imports
the other, and the reel pipeline runs unchanged when the image server is not up -- `handle`
falls back to agy.

    if papercut.available():
        made = papercut.generate(board, [2, 3, 5], log=print)

Both backends write the same file, `beat<n>_asset.png`, so nothing downstream can tell which
one produced a still.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Callable, Iterator

import httpx

from . import board as board_mod
from . import config

# Long enough to survive a machine busy with an mflux render, short enough that a studio
# with no image server does not stall on every asset click before falling back to agy.
PROBE_TIMEOUT = 2.0
# No read timeout on the event stream: a frame takes tens of seconds and the server's only
# traffic in between is a 15 s keep-alive ping. Connect and write stay bounded.
STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
# Fallback if /api/health is from an older build that did not report its limits.
DEFAULT_MAX_FRAMES = 9


class PapercutError(RuntimeError):
    """The image server was reachable but could not produce the stills."""


def health(url: str | None = None) -> dict | None:
    """What the image server says about itself, or None if it is not there.

    Deliberately swallows every transport error: "not running" is the ordinary case on a
    machine that only ever uses agy, not a fault to report.
    """
    try:
        response = httpx.get(f"{url or config.PAPERCUT_URL}/api/health", timeout=PROBE_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001 -- unreachable, wrong service, malformed reply
        return None
    return payload if payload.get("ok") else None


def available(url: str | None = None) -> bool:
    return health(url) is not None


def max_frames(reported: dict | None) -> int:
    """The image server's per-scene frame cap, which is what batches are chunked onto."""
    try:
        return max(1, int((reported or {}).get("limits", {}).get("maxFrames")))
    except (TypeError, ValueError):
        return DEFAULT_MAX_FRAMES


def style_for(board: board_mod.Board) -> str:
    """The scene-level suffix Papercut appends to every beat.

    The board's style bible plus the same medium clause the agy backend uses, so a reel
    whose stills came from both backends still looks like one production.
    """
    bible = " ".join((board.data.get("style_bible") or "").split()).strip()
    return f"{bible} {config.ASSET_STYLE_SUFFIX}".strip()


def _runs(board: board_mod.Board, beats: list[int], cap: int) -> Iterator[tuple[Path | None, list[int]]]:
    """Group beats into scenes that share one conditioning image.

    Two things force the grouping to be lazy rather than computed up front:

      * on a board with no still at all, every beat's reference reads as None -- so grouping
        first would put the whole reel in one unconditioned scene and redesign the cast once
        per beat, which is the exact failure the reference was introduced to fix. Rendering
        the first beat alone creates the reference the rest are then anchored to;
      * a scene caps at the server's frame count, so a long run has to be cut anyway.

    Yields (reference, beats). A None reference means "this beat defines the look" and is
    always yielded on its own.
    """
    remaining = list(beats)
    while remaining:
        n = remaining[0]
        reference = board.reference_for(n)
        if reference is None:
            yield None, [n]
            remaining.pop(0)
            continue
        run = [n]
        remaining.pop(0)
        while remaining and len(run) < cap and board.reference_for(remaining[0]) == reference:
            run.append(remaining.pop(0))
        yield reference, run


def _scene_body(board: board_mod.Board, beats: list[int], reference: Path | None) -> dict:
    """One Papercut scene describing a run of stills.

    Papercut composes `continuity clause (when conditioned) + frame beat + scene style`,
    which is the same order the agy backend asks for in prose. The beat text is this beat's
    own `asset_prompt`; everything shared lives in the style suffix.

    `duration` and the frame timings derived from it are Papercut's own unit of judgment and
    mean nothing here -- these frames are opening compositions for separate shots, not a
    sequence to play back. One second per beat keeps the numbers unremarkable.
    """
    body: dict = {
        "title": f"{board.data.get('title') or board.slug} · stills",
        "description": board.data.get("concept") or board.data.get("title") or board.slug,
        "duration": float(len(beats)),
        "frameCount": len(beats),
        "style": style_for(board),
        "negativePrompt": "",
        "aspectId": config.PAPERCUT_ASPECT,
        "steps": config.PAPERCUT_STEPS,
        "seed": int(board.data.get("seed") or 0),
        # Independent shots, so an identical seed across them buys nothing and costs
        # variety -- unlike a walk cycle, where holding the seed is the point.
        "varySeeds": True,
        # "anchor": every still is conditioned on the board's cast reference, poses stay
        # independent. Not "chain" -- these are hard cuts, and chaining each still off the
        # previous one drifts by frame three and renders strictly in order for no gain.
        "consistency": "anchor" if reference else "none",
        "beats": [(board.beat(n).get("asset_prompt") or board.beat(n).get("action") or "").strip()
                  for n in beats],
    }
    if reference is not None:
        body["referencePath"] = str(reference.resolve())
    return body


def _events(client: httpx.Client, scene_id: str) -> Iterator[dict]:
    """Papercut's per-scene SSE stream, as decoded payloads.

    Malformed frames are skipped rather than raised on: the stream also carries keep-alive
    comments, and one unparseable line is not a reason to abandon a render that is already
    burning wall clock.
    """
    with client.stream("GET", f"/api/scenes/{scene_id}/events", timeout=STREAM_TIMEOUT) as stream:
        stream.raise_for_status()
        for line in stream.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                yield json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue


def _download(client: httpx.Client, url: str, out_path: Path) -> Path:
    """Fetch a rendered frame over HTTP rather than reading it off disk.

    The image server's output directory is relative to whatever directory it was started
    in, so guessing at `image/out/...` from here would break the moment someone runs it from
    somewhere else. Re-encoding through PIL normalises the file the same way the agy backend
    does, which is a no-op on mflux's real PNGs and cheap enough not to special-case.
    """
    from PIL import Image

    response = client.get(url, timeout=60.0)
    response.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(response.content)) as raw:
        raw.convert("RGB").save(out_path)
    return out_path


def _render_scene(client: httpx.Client, board: board_mod.Board, beats: list[int],
                  reference: Path | None, *, log: Callable[[str], None],
                  progress: Callable[[int, float], None] | None,
                  on_still: Callable[[int], None] | None,
                  cancelled: Callable[[], bool] | None) -> list[int]:
    """Create, render and collect one scene. Returns the beats whose stills landed."""
    created = client.post("/api/scenes", json=_scene_body(board, beats, reference))
    created.raise_for_status()
    scene_id = created.json()["id"]
    # Explicit frame indices, because Papercut clamps a scene to a minimum of two frames --
    # a single-beat run would otherwise render a phantom second still and charge wall clock
    # for a file nothing reads.
    started = client.post(f"/api/scenes/{scene_id}/render",
                          json={"frames": list(range(len(beats)))})
    started.raise_for_status()

    made: list[int] = []
    seen: set[int] = set()
    for event in _events(client, scene_id):
        if cancelled is not None and cancelled():
            # Papercut's cancel is a SIGTERM to mflux plus a flag, so the frame in flight is
            # lost and the ones already on disk are kept. Collect those before leaving.
            client.post(f"/api/scenes/{scene_id}/cancel")
            log("[stills] cancelled")
            break
        if event.get("type") != "scene":
            continue
        state = event.get("scene") or {}
        for index, frame in enumerate(state.get("frames", [])[:len(beats)]):
            n = beats[index]
            if progress is not None and frame.get("status") == "running":
                progress(n, float(frame.get("progress") or 0.0))
            if frame.get("status") != "done" or index in seen:
                continue
            seen.add(index)
            _download(client, frame["url"], board.asset_path(n))
            made.append(n)
            log(f"[stills] beat {n}: done in {frame.get('elapsed', 0)}s")
            # Announced per still, not per batch: a nine-beat run is minutes long, and the
            # canvas showing beat 2's picture while beat 3 renders is the whole reason the
            # progress is streamed at all.
            if on_still is not None:
                on_still(n)
        if state.get("status") in ("done", "error", "cancelled"):
            failed = [beats[f["index"]] for f in state.get("frames", [])[:len(beats)]
                      if f.get("status") == "error"]
            if failed:
                # Not fatal: a run of stills where one frame failed still leaves the others
                # on disk, and the board shows exactly which beats are still short.
                log(f"[stills] beats {failed} failed: "
                    f"{next((f.get('error') for f in state['frames'] if f.get('error')), 'unknown')}")
            break
    return made


def generate(board: board_mod.Board, beats: list[int], *,
             log: Callable[[str], None] = print,
             progress: Callable[[int, float], None] | None = None,
             on_still: Callable[[int], None] | None = None,
             cancelled: Callable[[], bool] | None = None,
             url: str | None = None) -> list[int]:
    """Render the opening stills for these beats, locally. Returns the ones that landed.

    Beats are rendered in the order given, in runs that share a conditioning image. A beat
    with no reference yet is rendered on its own first, because its still becomes the
    reference every later one is anchored to -- the same order `storyboard.py --assets` uses.
    """
    reported = health(url)
    if reported is None:
        raise PapercutError(
            f"no image server at {url or config.PAPERCUT_URL}. Start it with "
            "`make images`, or set PAPERREEL_ASSET_BACKEND=agy to use Antigravity instead."
        )
    cap = max_frames(reported)
    made: list[int] = []
    with httpx.Client(base_url=url or config.PAPERCUT_URL, timeout=30.0) as client:
        for reference, run in _runs(board, beats, cap):
            if cancelled is not None and cancelled():
                break
            log(f"[stills] beats {run}: rendering locally"
                + (f", cast locked to {reference.name}" if reference
                   else " (nothing to match yet -- this defines the look)"))
            made.extend(_render_scene(client, board, run, reference, log=log,
                                      progress=progress, on_still=on_still,
                                      cancelled=cancelled))
    return made
