"""Opening stills from Papercut Studio -- the Gemini renderer in `image/`.

The transport, and only the transport. `stills.py` owns the judgement around it: which beats
may get a still at all, and what happens to one that comes back looking like a different
production. This module creates a scene, starts it, follows its event stream and puts the
finished frames on disk as `beat<n>_asset.png` -- and, on a reference beat, as
the stop-motion poses `beat<n>_pose2.png` upward that fill H3's remaining image
sockets.

The seam is HTTP on loopback, not shared code. Papercut owns prompt composition, the render
lock and the progress scraping; this side owns the board and the joins. Neither imports the
other.

What a still is drawn from is `Board.still_pictures`: identity sheets (or the reel's locked
cast still when those are missing), then this beat's storyboard panel when it fits, then the
set / previous pose / director uploads on a reference join. They go over as `referencePaths`,
capped by whatever the image server reports in `limits.maxReferences`.

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

# Long enough to survive a Gemini request, short enough that a studio with
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
    machine that supplies its own stills, or one where the Gemini server cannot run at all, not a fault to
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

    The board's style bible plus its medium's still suffix, which is also what the vision
    review judges a still against -- so the words that ask for the medium and the words that
    check for it cannot drift apart. Both sides read `board.look()`, which is the single place
    that agreement is now enforced.
    """
    bible = " ".join((board.data.get("style_bible") or "").split()).strip()
    return f"{bible} {board.look().still}".strip()


Pictures = list[tuple[Path, str]]


# The frame key for something this board renders that is not one of its beats: a reel-level
# staging sheet. Everything below keys frames by beat number -- for grouping, for progress, for
# the log line -- and a design sheet has no beat to be numbered by. Zero rather than None so the
# lists stay `list[int]` and the two lookups that read a beat can simply not find one.
NO_BEAT = 0


def _gemini_settings(board: board_mod.Board, n: int) -> tuple[str | None, str | None]:
    """The image settings stored on one beat, used to keep unlike frames separate.

    Answers "nothing stored" for a frame that is not a beat (see `NO_BEAT`), which is what lets
    one scene body serve a still, a reference picture and a staging sheet. Raising instead would
    make the caller thread a second flag through five functions to say the same thing.
    """
    try:
        beat = board.beat(n)
    except KeyError:
        return None, None
    return beat.get("gemini_model"), beat.get("gemini_image_size")


def _still_sources(board: board_mod.Board, n: int, refs_cap: int,
                   include_current: bool) -> Pictures:
    """The still's context, optionally led by the image currently on the beat.

    A first draw has no current image. A regeneration does: putting that image first makes the
    request an edit, while the cast and beat references remain available to explain what should
    stay consistent. The current image is not part of ``Board.still_pictures`` because that
    method is also used for the first draw, so the distinction belongs at this seam.
    """
    pictures = board.still_pictures(n, refs_cap)
    current = board.asset_path(n)
    if not include_current or not current.is_file():
        return pictures
    rest = [picture for picture in pictures if picture[0] != current]
    # The current still takes slot 0 so this is an edit. Identity then panels then the rest
    # already sit in `still_pictures` in that order, so a tail-truncate drops an upload or a
    # set, never a character sheet, and never the first panel while identity still fits.
    return ([(current, "")] + rest)[:refs_cap]


def _runs(board: board_mod.Board, beats: list[int], cap: int,
          refs_cap: int, include_current: bool) -> Iterator[tuple[Pictures, list[int]]]:
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
        pictures = _still_sources(board, n, refs_cap, include_current)
        if board.reference_for(n) is None:
            yield pictures, [n]
            remaining.pop(0)
            continue
        run = [n]
        settings = _gemini_settings(board, n)
        remaining.pop(0)
        while (remaining and len(run) < cap
               and _still_sources(board, remaining[0], refs_cap, include_current) == pictures
               and _gemini_settings(board, remaining[0]) == settings):
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
    # The locked-off angle, in words, so "the camera angle described above" later in this
    # function has something to describe even when no panel PNG is on disk.
    prompt = (config.camera_still(board.camera_for(beat)) + prompt).strip()
    notes = [
        " ".join(config.expand_mentions(note, mentions, prose=True).split()).rstrip(".")
        for _path, note in pictures if note.strip()
    ]
    notes = [note for note in notes if note]
    if notes:
        prompt = f"{prompt} The reference images show: {'; '.join(notes)}.".strip()
    # Cast reference is almost always beat 1's still -- often a wide. Without this sentence the
    # image server's continuity clause used to lock camera angle to that wide, and every later
    # "medium" / "close-up" still came back as the same establishing two-shot. Framing lives in
    # the beat text above, and in the storyboard panel when one is among the pictures; the other
    # images only lock who is in the film and what they are made of.
    if pictures:
        shown_panels = [path for path, _ in pictures if path in set(board.panel_paths(n))]
        if shown_panels:
            if len(shown_panels) == 1:
                prompt = (
                    f"{prompt} Match the storyboard panel's composition -- shot size, camera "
                    f"angle and who stands where. Do not copy the pencil medium. Any other "
                    f"reference images lock character design, materials and palette only; do "
                    f"not copy their framing."
                ).strip()
            else:
                prompt = (
                    f"{prompt} Match the storyboard panels' composition in sequence -- opening, "
                    f"then through the action, then the landing -- shot size, camera angle and "
                    f"who stands where. Do not copy the pencil medium. Any other reference "
                    f"images lock character design, materials and palette only; do not copy "
                    f"their framing."
                ).strip()
        else:
            prompt = (
                f"{prompt} The reference images lock character design, materials and palette "
                f"only -- compose this frame at the shot scale and camera angle described above; "
                f"do not copy a reference's framing."
            ).strip()
    if config.is_travel(beat.get("action") or ""):
        prompt = f"{prompt} {config.TRAVEL_STILL_NOTE}".strip()
    # And the bound design sheets this still was NOT handed, as words. Nine slots now hold a
    # cast, several graphite panels and a previous pose; a set that still does not fit arrives
    # as a sentence. Computed against the very list being conditioned on, so a sheet is never
    # both an unnamed reference image and a description of a second one of it.
    staged = " ".join(config.expand_mentions(
        board.staging_text(n, pictures), mentions, prose=True
    ).split()).strip().rstrip(".")
    if staged:
        prompt = f"{prompt} {config.STAGING_PREFIX}{staged}.".strip()
    return prompt


def _scene_body(board: board_mod.Board, beats: list[int], pictures: Pictures,
                seed: int | None = None, texts: list[str] | None = None,
                aspect: str | None = None, consistency: str | None = None,
                style: str | None = None, gemini_model: str | None = None,
                gemini_image_size: str | None = None,
                vary_seeds: bool | None = None,
                duration: float | None = None,
                negative: str | None = None,
                slide_background: bool = False) -> dict:
    """One Papercut scene describing a run of stills.

    Papercut composes `continuity clause (when conditioned) + frame beat + scene style`. The
    beat text is this beat's own `asset_prompt` plus its pictures' notes; everything shared
    across the reel lives in the style suffix, which is what keeps one board's stills reading as
    one production.

    `duration` and the frame timings derived from it are Papercut's own unit of judgment.
    Opening stills of separate shots pass one second per beat, which keeps the numbers
    unremarkable. A stop-motion sequence passes the beat's own length so the poses spread
    across the action they will be interpolated through.

    `vary_seeds` defaults on: independent shots, so an identical seed across them buys
    nothing and costs variety. A stop-motion sequence holds the seed, which is the walk-cycle
    case the comment below used to name as the exception.

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

    `negative` is the same `Medium.avoid` string the video prompt closes with. Gemini has no
    negative-prompt field, so the image server appends `Avoid: …` to the composed prompt
    (`image/server/gemini.ts`). Defaulted to the board's medium so a still, a sheet and a
    picture all refuse the same neighbouring genres; an explicit empty string is how a
    storyboard panel opts out -- those negatives name "cartoon without paper texture", and a
    graphite sketch is exactly that on purpose.
    """
    body: dict = {
        "title": f"{board.data.get('title') or board.slug} · stills",
        "description": board.data.get("concept") or board.data.get("title") or board.slug,
        "duration": float(len(beats)) if duration is None else float(duration),
        "frameCount": len(beats),
        "style": style_for(board) if style is None else style,
        "negativePrompt": board.look().avoid if negative is None else negative,
        "aspectId": aspect or config.PAPERCUT_ASPECT,
        "steps": config.PAPERCUT_STEPS,
        "seed": int(board.data.get("seed") or 0) if seed is None else int(seed),
        # Independent shots, so an identical seed across them buys nothing and costs
        # variety -- unlike a walk cycle, where holding the seed is the point.
        "varySeeds": True if vary_seeds is None else bool(vary_seeds),
        # "anchor": every still is conditioned on the pictures below, poses stay independent.
        # "chain": each frame is the next pose of one take, conditioned on the frame before.
        # Not the default -- chaining independent opening stills drifts by frame three and
        # throws the uploads away. A stop-motion sequence of ONE beat is the case that wants it.
        # Honour an explicit mode even with no pictures: a defining beat's sequence has nothing
        # to match yet, but frames 2..k still have to chain off frame 1.
        "consistency": (
            consistency if consistency is not None
            else (
                ("edit" if beats and any(path == board.asset_path(beats[0]) for path, _ in pictures)
                 else "anchor")
                if pictures else "none"
            )
        ),
        "beats": texts if texts is not None else [_beat_text(board, n, pictures) for n in beats],
    }
    if slide_background:
        # Older image servers ignore unknown fields. A build that advertises
        # `slideBackground` on /api/health swaps the chain continuity clause so the set
        # may translate; without it the pose text is the only instruction, and it often
        # loses to "keep the same background".
        body["slideBackground"] = True
    selected_model = gemini_model
    selected_size = gemini_image_size
    if beats:
        beat_model, beat_size = _gemini_settings(board, beats[0])
        selected_model = selected_model or beat_model
        selected_size = selected_size or beat_size
    if selected_model:
        body["geminiModel"] = selected_model
    if selected_size:
        body["geminiImageSize"] = selected_size
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
    somewhere else. Re-encoding through PIL normalises the file, which is a no-op on Gemini's
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
                  style: str | None = None,
                  gemini_model: str | None = None,
                  gemini_image_size: str | None = None,
                  vary_seeds: bool | None = None,
                  duration: float | None = None,
                  negative: str | None = None,
                  label: Callable[[int], str] | None = None,
                  slide_background: bool = False) -> list[int]:
    """Create, render and collect one scene. Returns the beats whose frames landed.

    `out_paths` says where each frame goes, positionally. Without it every frame lands on its
    beat's still, which is what this module was written to do and what `generate` still wants;
    with it, `draw` puts a frame on a reference picture instead.

    `label` names each frame in the log. Only a caller rendering something that is not a beat
    needs it -- a staging sheet is keyed `NO_BEAT`, and "beat 0: done in 11s" is a worse line
    than no line at all.
    """
    name = label or (lambda n: f"beat {n}")
    created = client.post("/api/scenes", json=_scene_body(
        board, beats, pictures, seed, texts=texts, aspect=aspect, consistency=consistency,
        style=style, gemini_model=gemini_model, gemini_image_size=gemini_image_size,
        vary_seeds=vary_seeds, duration=duration, negative=negative,
        slide_background=slide_background))
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
            # Papercut's cancel aborts the Gemini request plus a flag, so the frame in flight is
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
            log(f"[stills] {name(n)}: done in {frame.get('elapsed', 0)}s")
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


def _pose_texts(board: board_mod.Board, n: int, count: int, pictures: Pictures) -> list[str]:
    """One frame text per pose: the beat's still prompt, then where in the action this pose sits.

    Papercut's own `beatHint` is a left-to-right walk, which is the wrong action for most
    shots. The beat text already names the moment; `pose_phase` only says how far through it
    this frame is, so pose 4 of 7 of "she raises the lantern" is the lantern partway up.

    Lateral travel adds the pull instruction on every pose, because the image server's
    chain clause otherwise locks background position to the previous frame.
    """
    base = _beat_text(board, n, pictures)
    action = (board.beat(n).get("action") or "").strip()
    pull = f" {config.TRAVEL_POSE_NOTE}" if config.is_travel(action) else ""
    return [
        f"{base} Stop-motion pose {index} of {count}: "
        f"{config.pose_phase(index, count, action)}.{pull}"
        for index in range(1, count + 1)
    ]


def _clear_extra_poses(board: board_mod.Board, n: int, keep: int) -> None:
    """Drop pose files past `keep`, so a shorter sequence cannot leave a gap-free longer one.

    `pose_paths` stops at the first missing file, but a regenerate that drew 6 after a previous
    9 would otherwise keep posing 7..9 on disk -- and `pictures_for` would hand H3 those stale
    in-betweens.
    """
    for index in range(max(1, keep) + 1, config.MAX_REF_IMAGES + 1):
        board.pose_path(n, index).unlink(missing_ok=True)


def _render_sequence(client: httpx.Client, board: board_mod.Board, n: int, *,
                     cap: int, refs_cap: int, log: Callable[[str], None],
                     progress: Callable[[int, float], None] | None,
                     on_still: Callable[[int], None] | None,
                     cancelled: Callable[[], bool] | None,
                     seed: int | None,
                     gemini_model: str | None,
                     gemini_image_size: str | None) -> list[int]:
    """One Papercut scene of chained poses for a single reference beat.

    `consistency="chain"` is the point: each pose is the next increment of one take, conditioned
    on the frame before it, which is how H3 then interpolates through them instead of treating
    nine stills as nine cuts. `varySeeds` is held for the same reason a walk cycle holds it.
    The first frame of a defining beat has nothing to match yet -- `_scene_body` honours chain
    even with no pictures -- and frames 2..k still chain off it.

    Capped at the image server's per-scene frame limit, because a chain that spanned two
    scenes would lose the previous frame at the cut and the rest would be independent shots.
    """
    count = min(board.sequence_count(n), cap)
    pictures = _still_sources(board, n, refs_cap, include_current=False)
    out_paths = [board.pose_path(n, index) for index in range(1, count + 1)]
    log(f"[stills] beat {n}: {count} stop-motion poses through Gemini"
        + (f", drawn from {', '.join(path.name for path, _ in pictures)}"
           if pictures else " (nothing to match yet -- this defines the look)"))
    at = {"i": 0}

    def label(_n: int) -> str:
        at["i"] += 1
        return f"beat {n} pose {at['i']}/{count}"

    frames = _render_scene(
        client, board, [n] * count, pictures, log=log, progress=progress,
        on_still=on_still, cancelled=cancelled, seed=seed,
        texts=_pose_texts(board, n, count, pictures), out_paths=out_paths,
        consistency="chain", vary_seeds=False,
        duration=board.seconds_for(board.beat(n)),
        gemini_model=gemini_model, gemini_image_size=gemini_image_size, label=label,
        slide_background=board.is_travel(board.beat(n)),
    )
    keep = sum(1 for path in out_paths if path.is_file())
    _clear_extra_poses(board, n, keep)
    return [n] if frames or board.asset_path(n).is_file() else []


def generate(board: board_mod.Board, beats: list[int], *,
             log: Callable[[str], None] = print,
             progress: Callable[[int, float], None] | None = None,
             on_still: Callable[[int], None] | None = None,
             cancelled: Callable[[], bool] | None = None,
             url: str | None = None,
             seed: int | None = None,
             gemini_model: str | None = None,
             gemini_image_size: str | None = None,
             include_current: bool = True) -> list[int]:
    """Render the opening stills (or a stop-motion sequence) for these beats. Returns the ones that landed.

    Beats that want more than one pose are rendered alone, as a chained scene: grouping them
    with neighbours would make Papercut treat independent shots as one take. Beats that still
    want a single opening still keep the old grouping -- runs that share their conditioning
    images -- so a board that never grew sequences is byte-identical in what it asks for.

    A beat with no cast reference yet is rendered on its own first, because its still becomes
    the reference every later one is anchored to -- the same order `storyboard.py --assets` uses.

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
    singles: list[int] = []

    def flush_singles() -> None:
        nonlocal made, singles
        if not singles:
            return
        if cancelled is not None and cancelled():
            singles = []
            return
        for pictures, run in _runs(board, singles, cap, refs_cap, include_current):
            if cancelled is not None and cancelled():
                break
            log(f"[stills] beats {run}: rendering through Gemini"
                + (f", drawn from {', '.join(path.name for path, _note in pictures)}"
                   if pictures else " (nothing to match yet -- this defines the look)"))
            landed = _render_scene(client, board, run, pictures, log=log,
                                   progress=progress, on_still=on_still,
                                   cancelled=cancelled, seed=seed,
                                   gemini_model=gemini_model,
                                   gemini_image_size=gemini_image_size)
            for n in landed:
                _clear_extra_poses(board, n, 1)
            made.extend(landed)
        singles = []

    with httpx.Client(base_url=url or config.PAPERCUT_URL, timeout=30.0) as client:
        for n in beats:
            if cancelled is not None and cancelled():
                break
            if board.sequence_count(n) > 1:
                flush_singles()
                made.extend(_render_sequence(
                    client, board, n, cap=cap, refs_cap=refs_cap, log=log,
                    progress=progress, on_still=on_still, cancelled=cancelled,
                    seed=seed, gemini_model=gemini_model,
                    gemini_image_size=gemini_image_size))
            else:
                singles.append(n)
        flush_singles()
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


def slides(reported: dict | None) -> bool:
    """Does this image server license the set to translate on a travel chain?

    An older build keeps the continuity clause that locks background position, so a
    travel sequence has to fight that with pose text alone. Advertised as `slideBackground`
    on /api/health, same contract as `edit`.
    """
    return "slideBackground" in ((reported or {}).get("modes") or [])


def draw(board: board_mod.Board, n: int, *, pictures: Pictures, text: str, out_path: Path,
         editing: bool,
         log: Callable[[str], None] = print,
         progress: Callable[[int, float], None] | None = None,
         cancelled: Callable[[], bool] | None = None,
         seed: int | None = None, url: str | None = None,
         gemini_model: str | None = None,
         gemini_image_size: str | None = None,
         style: str | None = None,
         aspect: str | None = None,
         label: str | None = None,
         negative: str | None = None) -> bool:
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

    `style`, `aspect` and `label` default to a beat's reference picture -- the prop-sheet suffix,
    the square, and "beat {n}". They exist for `staging.py`, which draws the same KIND of thing
    at reel level: a set sheet needs the opposite suffix (it is nothing but scenery) and the
    reel's own vertical shape, and it has no beat to be named after. Defaulted rather than made
    required so a reference-picture draw composes the byte-identical scene it always did.

    `negative` defaults to the board's medium, same as a still. Pass `""` for a storyboard
    panel: those negatives name the neighbouring cartoon the sketch is supposed to be.
    """
    reported = health(url)
    if reported is None:
        raise PapercutError(
            f"no image server at {url or config.PAPERCUT_URL}. Start it with `make images` "
            "(or `make run`, which starts all three), or upload the picture by hand."
        )
    mode = "edit" if pictures and edits(reported) else ("anchor" if pictures else "none")
    if editing and mode != "edit":
        log("[picture] this image server predates the `edit` mode, so the redraw is anchored "
            "instead -- it keeps the reference and may re-pose the subject")
    refs = pictures[:max_references(reported)]
    named = label or f"beat {n}"
    log(f"[picture] {named}: drawing {out_path.name}"
        + (f", from {', '.join(path.name for path, _note in refs)}" if refs
           else " (from the words alone)"))
    beat_model, beat_size = _gemini_settings(board, n)
    with httpx.Client(base_url=url or config.PAPERCUT_URL, timeout=30.0) as client:
        made = _render_scene(
            client, board, [n], refs, log=log, progress=progress, on_still=None,
            cancelled=cancelled, seed=seed, texts=[text], out_paths=[out_path],
            aspect=aspect or config.PAPERCUT_REF_ASPECT, consistency=mode,
            gemini_model=gemini_model or beat_model,
            gemini_image_size=gemini_image_size or beat_size,
            # The medium, and NOT the board's style bible -- see `_scene_body`. A prop sheet
            # shares the film's material with it, not the cast.
            style=style or board.look().sheet,
            negative=negative,
            label=lambda _n: named,
        )
    return bool(made)


def draw_frames(board: board_mod.Board, n: int, *, pictures: Pictures,
                texts: list[str], out_paths: list[Path],
                editing: bool,
                log: Callable[[str], None] = print,
                progress: Callable[[int, float], None] | None = None,
                cancelled: Callable[[], bool] | None = None,
                seed: int | None = None, url: str | None = None,
                gemini_model: str | None = None,
                gemini_image_size: str | None = None,
                style: str | None = None,
                aspect: str | None = None,
                label: str | None = None,
                negative: str | None = None) -> bool:
    """Render several pictures of one beat as a chained scene, into `out_paths`.

    Storyboard panels are the caller: three graphite frames of one shot, unconditioned on the
    film (`pictures=[]` so the first frame is `none` and the rest chain off it). `editing` is
    accepted so the keyword bag `panels.draw` shares with `draw` still type-checks; it is
    ignored, because a panel is never an edit of an existing still.

    Same Lite/style/aspect contract as `draw`. `vary_seeds` is held so the three sketches read
    as one take rather than three independent doodles.
    """
    del editing  # panels.draw passes the same bag as draw(); a chain is not an edit.
    if not texts or not out_paths or len(texts) != len(out_paths):
        return False
    reported = health(url)
    if reported is None:
        raise PapercutError(
            f"no image server at {url or config.PAPERCUT_URL}. Start it with `make images` "
            "(or `make run`, which starts all three), or upload the picture by hand."
        )
    refs = pictures[:max_references(reported)]
    named = label or f"beat {n}"
    log(f"[picture] {named}: drawing {len(out_paths)} frames"
        + (f", from {', '.join(path.name for path, _note in refs)}" if refs
           else " (from the words alone)"))
    beat_model, beat_size = _gemini_settings(board, n)
    at = {"i": 0}

    def frame_label(_n: int) -> str:
        at["i"] += 1
        return f"{named} {at['i']}/{len(out_paths)}"

    with httpx.Client(base_url=url or config.PAPERCUT_URL, timeout=30.0) as client:
        made = _render_scene(
            client, board, [n] * len(out_paths), refs, log=log, progress=progress,
            on_still=None, cancelled=cancelled, seed=seed, texts=texts,
            out_paths=out_paths,
            aspect=aspect or config.PAPERCUT_REF_ASPECT,
            consistency="chain", vary_seeds=False,
            duration=float(max(len(out_paths), 2)),
            gemini_model=gemini_model or beat_model,
            gemini_image_size=gemini_image_size or beat_size,
            style=style or board.look().sheet,
            negative=negative,
            label=frame_label,
        )
    return bool(made) or out_paths[0].is_file()
