"""Opening stills from Papercut Studio -- the local mflux renderer in `image/`.

The transport, and only the transport. `stills.py` owns the judgement around it: which beats
may get a still at all, and what happens to one that comes back looking like a different
production. This module creates a scene, starts it, follows its event stream and puts the
finished frames on disk as `beat<n>_asset.png`.

The seam is HTTP on loopback, not shared code. Papercut owns prompt composition, the render
lock and the progress scraping; this side owns the board and the joins. Neither imports the
other.

What a still is drawn from is `Board.still_pictures`: the reel's locked cast reference, then the
director's uploads on that beat -- the same pictures the video model is shown, so the puppet in
the clip and the puppet in the frame it opens on are held to one set of images. They go over as
`referencePaths`, capped by whatever the image server reports in `limits.maxReferences`.

    if papercut.available():
        made = papercut.generate(board, [2, 3, 5], log=print)

It is the only generator of stills. The old fallback was the Antigravity CLI's image tool,
whose ~five-images-per-five-hour window is the reason chaining is the default and a reel is
designed to need one image rather than one per beat -- both of which are still good design,
and neither of which is forced any more. With the image server down, a beat's still is an
upload; that is what `manual_stills` on a board is for.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Callable, Iterator

import httpx

from . import board as board_mod
from . import config

# Long enough to survive a machine busy with an mflux render, short enough that a studio with
# no image server answers "not running" promptly instead of stalling on every asset click.
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

    Deliberately swallows every transport error: "not running" is an ordinary state on a
    machine that supplies its own stills, or one where mflux cannot run at all, not a fault to
    report. `stills.py` turns it into a refusal with words when a still is actually wanted.
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


def max_references(reported: dict | None) -> int:
    """How many conditioning images the image server will accept for one frame.

    The smaller of what it reports and `config.MAX_STILL_REFS`, so whichever side is more
    conservative wins. A build that does not report the number is assumed to be the older
    single-reference one: sending it four paths would leave it conditioning on the first alone
    and quietly dropping the director's uploads, which looks like they were ignored.
    """
    try:
        return max(1, min(config.MAX_STILL_REFS,
                          int((reported or {}).get("limits", {}).get("maxReferences"))))
    except (TypeError, ValueError):
        return 1


def style_for(board: board_mod.Board) -> str:
    """The scene-level suffix Papercut appends to every beat.

    The board's style bible plus `config.ASSET_STYLE_SUFFIX`, which is also what the vision
    review judges a still against -- so the words that ask for the medium and the words that
    check for it cannot drift apart.
    """
    bible = " ".join((board.data.get("style_bible") or "").split()).strip()
    return f"{bible} {config.ASSET_STYLE_SUFFIX}".strip()


Pictures = list[tuple[Path, str]]


def _runs(board: board_mod.Board, beats: list[int], cap: int,
          refs_cap: int) -> Iterator[tuple[Pictures, list[int]]]:
    """Group beats into scenes that share the same conditioning images.

    Two things force the grouping to be lazy rather than computed up front:

      * on a board with no still at all, every beat's cast reference reads as None -- so
        grouping first would put the whole reel in one unconditioned scene and redesign the cast
        once per beat, which is the exact failure the reference was introduced to fix. Rendering
        the first beat alone creates the reference the rest are then anchored to;
      * a scene caps at the server's frame count, so a long run has to be cut anyway.

    Yields (pictures, beats). A beat with no cast reference is always yielded on its own, even
    when the director has uploaded pictures to it: it is the beat that defines the look, and
    `stills.generate` reviews it before anything else starts for the same reason.

    In practice only beats conditioned on the cast reference alone ever share a scene, because an
    upload lives at `beat<n>_ref<i>.png` -- per beat by construction, so two beats carrying
    uploads can never present the same list.
    """
    remaining = list(beats)
    while remaining:
        n = remaining[0]
        pictures = board.still_pictures(n, refs_cap)
        if board.reference_for(n) is None:
            yield pictures, [n]
            remaining.pop(0)
            continue
        run = [n]
        remaining.pop(0)
        while (remaining and len(run) < cap
               and board.still_pictures(remaining[0], refs_cap) == pictures):
            run.append(remaining.pop(0))
        yield pictures, run


def _beat_text(board: board_mod.Board, n: int, pictures: Pictures) -> str:
    """The frame text for one beat: its asset prompt, plus what its extra pictures are.

    Any @-token in either half is expanded in PROSE mode, because nothing in this prompt carries
    a `<Picture i>` tag -- the still model is handed images and a paragraph, and the paragraph
    has never named them. A beat with no tokens composes byte-identical text to what it always
    did, which is the same promise the notes clause below makes.

    The notes are the same ones the video model is given, and they are here for the same reason
    they exist there: shown a picture with no explanation, a model reads the picture as the scene.
    A reference of the cast standing in the finished set comes back as the finished set, whatever
    the beat asked for -- and on a still that is worse than on a clip, because the still is what
    the clip's opening frames are then anchored to.

    Appended after the prompt rather than woven into it, so a beat whose pictures were never
    described composes the byte-identical text it did before there was a second picture at all.
    The cast reference contributes no note: it is not the director's, and Papercut's own
    continuity clause already says what it is for.
    """
    beat = board.beat(n)
    mentions = board.mentions(n, pictures)
    prompt = config.expand_mentions(
        (beat.get("asset_prompt") or beat.get("action") or "").strip(), mentions, prose=True
    )
    notes = [
        " ".join(config.expand_mentions(note, mentions, prose=True).split()).rstrip(".")
        for _path, note in pictures if note.strip()
    ]
    notes = [note for note in notes if note]
    if not notes:
        return prompt
    return f"{prompt} The reference images show: {'; '.join(notes)}.".strip()


def _scene_body(board: board_mod.Board, beats: list[int], pictures: Pictures,
                seed: int | None = None, texts: list[str] | None = None,
                aspect: str | None = None, consistency: str | None = None,
                style: str | None = None) -> dict:
    """One Papercut scene describing a run of stills.

    Papercut composes `continuity clause (when conditioned) + frame beat + scene style`. The
    beat text is this beat's own `asset_prompt` plus its pictures' notes; everything shared
    across the reel lives in the style suffix, which is what keeps one board's stills reading as
    one production.

    `duration` and the frame timings derived from it are Papercut's own unit of judgment and
    mean nothing here -- these frames are opening compositions for separate shots, not a
    sequence to play back. One second per beat keeps the numbers unremarkable.

    `seed` overrides the board's, and it exists for exactly one caller: a re-render asked for
    with the prompt unchanged. Papercut derives a frame's seed as `scene.seed + index`, so
    rendering the same beat twice off the same board seed comes back byte-identical -- which
    reads as a button that did nothing. Everything else leaves it alone, because a still that
    was regenerated after an edit should differ where the words differ and nowhere else.

    `texts`, `aspect`, `consistency` and `style` exist for `draw`, which renders something that
    is not a beat's still: a reference picture, from its own prompt, at its own shape, held
    rather than re-posed, and NOT made of the reel's cast. They default to what a still has
    always used, so a scene composed for `generate` is byte-identical to before they existed.

    `style` is the one to watch. Papercut composes every frame as `continuity clause + frame beat
    + scene style`, so the default here -- the board's style bible -- reaches the model on every
    frame whatever the beat text says. That is right for a still, which is supposed to contain
    the cast; it is how a prop sheet asked for "a single iron-grey club" came back as the fox the
    bible describes.
    """
    body: dict = {
        "title": f"{board.data.get('title') or board.slug} · stills",
        "description": board.data.get("concept") or board.data.get("title") or board.slug,
        "duration": float(len(beats)),
        "frameCount": len(beats),
        "style": style_for(board) if style is None else style,
        "negativePrompt": "",
        "aspectId": aspect or config.PAPERCUT_ASPECT,
        "steps": config.PAPERCUT_STEPS,
        "seed": int(board.data.get("seed") or 0) if seed is None else int(seed),
        # Independent shots, so an identical seed across them buys nothing and costs
        # variety -- unlike a walk cycle, where holding the seed is the point.
        "varySeeds": True,
        # "anchor": every still is conditioned on the pictures below, poses stay independent.
        # Not "chain" -- these are hard cuts, and chaining each still off the previous one
        # drifts by frame three and renders strictly in order for no gain. Chain would also
        # throw the uploads away: Papercut conditions a chained frame on the frame before it
        # alone.
        "consistency": (consistency or "anchor") if pictures else "none",
        "beats": texts if texts is not None else [_beat_text(board, n, pictures) for n in beats],
    }
    if pictures:
        body["referencePaths"] = [str(path.resolve()) for path, _note in pictures]
        # And the first of them in the single-image field as well, which is what an image server
        # from before `referencePaths` existed reads. Losing the director's uploads against an
        # older build is a disappointment; losing the cast reference too would be a regression,
        # and every still on the board would silently stop matching the rest.
        body["referencePath"] = body["referencePaths"][0]
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
    somewhere else. Re-encoding through PIL normalises the file, which is a no-op on mflux's
    real PNGs and cheap enough not to special-case an uploaded one.
    """
    from PIL import Image

    response = client.get(url, timeout=60.0)
    response.raise_for_status()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(response.content)) as raw:
        raw.convert("RGB").save(out_path)
    return out_path


def _render_scene(client: httpx.Client, board: board_mod.Board, beats: list[int],
                  pictures: Pictures, *, log: Callable[[str], None],
                  progress: Callable[[int, float], None] | None,
                  on_still: Callable[[int], None] | None,
                  cancelled: Callable[[], bool] | None,
                  seed: int | None = None, texts: list[str] | None = None,
                  out_paths: list[Path] | None = None,
                  aspect: str | None = None,
                  consistency: str | None = None,
                  style: str | None = None) -> list[int]:
    """Create, render and collect one scene. Returns the beats whose frames landed.

    `out_paths` says where each frame goes, positionally. Without it every frame lands on its
    beat's still, which is what this module was written to do and what `generate` still wants;
    with it, `draw` puts a frame on a reference picture instead.
    """
    created = client.post("/api/scenes", json=_scene_body(
        board, beats, pictures, seed, texts=texts, aspect=aspect, consistency=consistency,
        style=style))
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
            _download(client, frame["url"],
                      out_paths[index] if out_paths else board.asset_path(n))
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
             url: str | None = None,
             seed: int | None = None) -> list[int]:
    """Render the opening stills for these beats, locally. Returns the ones that landed.

    Beats are rendered in the order given, in runs that share their conditioning images. A beat
    with no cast reference yet is rendered on its own first, because its still becomes the
    reference every later one is anchored to -- the same order `storyboard.py --assets` uses.

    `seed` replaces the board's for this run only; see `_scene_body` for the one case that
    needs it.
    """
    reported = health(url)
    if reported is None:
        raise PapercutError(
            f"no image server at {url or config.PAPERCUT_URL}. Start it with `make images` "
            "(or `make run`, which starts all three), or upload the stills by hand."
        )
    cap = max_frames(reported)
    refs_cap = max_references(reported)
    made: list[int] = []
    with httpx.Client(base_url=url or config.PAPERCUT_URL, timeout=30.0) as client:
        for pictures, run in _runs(board, beats, cap, refs_cap):
            if cancelled is not None and cancelled():
                break
            log(f"[stills] beats {run}: rendering locally"
                + (f", drawn from {', '.join(path.name for path, _note in pictures)}"
                   if pictures else " (nothing to match yet -- this defines the look)"))
            made.extend(_render_scene(client, board, run, pictures, log=log,
                                      progress=progress, on_still=on_still,
                                      cancelled=cancelled, seed=seed))
    return made


def edits(reported: dict | None) -> bool:
    """Does this image server know the `edit` consistency mode?

    Handed a mode it does not recognise, an older build stores the string and then matches no
    arm of its own `referenceFor` -- falling through to chain's backward walk, which on a
    one-frame scene finds nothing and renders from the text alone. The picture would be
    silently dropped, which is the worst of the three outcomes. So a build that does not
    advertise it gets `anchor`: that keeps the picture and loses only the "change nothing else"
    half, which is the better half to lose.
    """
    return "edit" in ((reported or {}).get("modes") or [])


def draw(board: board_mod.Board, n: int, *, pictures: Pictures, text: str, out_path: Path,
         editing: bool,
         log: Callable[[str], None] = print,
         progress: Callable[[int, float], None] | None = None,
         cancelled: Callable[[], bool] | None = None,
         seed: int | None = None, url: str | None = None) -> bool:
    """Render one picture that is not a beat's still, straight into `out_path`.

    Deliberately not routed through `generate`: `_runs` groups beats by `still_pictures` and
    `_beat_text` reads `asset_prompt`, and neither is what a reference picture is drawn from or
    drawn to. What is worth sharing is everything below the scene body -- the health probe, the
    event stream, the per-frame progress, the cancel path -- and that is what this reuses.

    `pictures` is conditioning, passed in rather than derived: the caller knows whether this
    slot already has a file, and `board.py` has no method for "the picture being redrawn plus
    the cast" because nothing else wants that list.

    `editing` says whether `pictures[0]` is this same file, being changed. It gets
    `consistency="edit"`, the mode that omits the image server's continuity clause -- that clause
    ends "but move the subject into a clearly different pose and position", which is right when
    the reference is the previous frame of a moving sequence and exactly wrong when the reference
    IS the thing being changed and the note said "make the club longer".

    A first draw has no conditioning at all (see `pictures.conditioning` for why the cast
    reference is deliberately absent) and falls through to `_scene_body`'s "none", which is pure
    text-to-image. The medium travels in the words instead.

    `out_path` is also `referencePaths[0]` when editing, and that is safe: Papercut resolves its
    conditioning at render start and `_download` only fires once the frame reports done.
    """
    reported = health(url)
    if reported is None:
        raise PapercutError(
            f"no image server at {url or config.PAPERCUT_URL}. Start it with `make images` "
            "(or `make run`, which starts all three), or upload the picture by hand."
        )
    mode = "edit" if editing and edits(reported) else "anchor"
    if editing and mode != "edit":
        log("[picture] this image server predates the `edit` mode, so the redraw is anchored "
            "instead -- it keeps the reference and may re-pose the subject")
    refs = pictures[:max_references(reported)]
    log(f"[picture] beat {n}: drawing {out_path.name}"
        + (f", from {', '.join(path.name for path, _note in refs)}" if refs
           else " (from the words alone)"))
    with httpx.Client(base_url=url or config.PAPERCUT_URL, timeout=30.0) as client:
        made = _render_scene(
            client, board, [n], refs, log=log, progress=progress, on_still=None,
            cancelled=cancelled, seed=seed, texts=[text], out_paths=[out_path],
            aspect=config.PAPERCUT_REF_ASPECT, consistency=mode,
            # The medium, and NOT the board's style bible -- see `_scene_body`. A prop sheet
            # shares the paper with the film, not the cast.
            style=config.REF_DRAW_STYLE_SUFFIX,
        )
    return bool(made)
