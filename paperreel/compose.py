"""Assemble a still (and optional stop-motion) from bound design sheets.

This is the local answer to the flatten-then-regenerate failure: generate the puppets
once as sheets, then place them. Gemini is not called. H3 is not called. Iteration is
free.

    compose.still(board, 3)          # writes beat3_asset.png
    compose.clip(board, 3)           # writes beat3_assemble.mp4
    compose.reel(board)              # stitches every beat's assemble clip

A character model sheet is four labelled views in one 16:9 image. The FRONT cell is
the top-left quadrant of that layout (`config.CHAR_SHEET_LAYOUT`); composing the whole
contact sheet would put four foxes in the shot. Props and sets are already isolated
subjects and are keyed as they stand.

Placement lives on the beat as `place` / `move`, optional, defaulting to the numbers
`media.compose` has always used. Neither field is in a fingerprint: the composed PNG
is hashed instead, and the assemble clip is a preview.
"""

from __future__ import annotations

import math
import random
import shutil
from pathlib import Path
from typing import Callable

from . import board as board_mod
from . import config, media


class ComposeError(RuntimeError):
    """A compose or assemble that must not run. `status` is the HTTP code the API answers with."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _front_cell(path: Path):
    """The FRONT view of a four-section character model sheet.

    `CHAR_SHEET_LAYOUT` is turnaround / expressions / head / palette as a 2x2. FRONT is
    the first cell of the turnaround, top-left. Heuristic, not a crop the model was
    asked to respect -- one-shot Gemini may drop a section -- so a sheet that is already
    a single puppet (a prop, an older full-body) still keys cleanly: a 1x1 'quadrant' of
    itself is itself.
    """
    from PIL import Image

    with Image.open(path) as image:
        image = image.convert("RGBA")
        width, height = image.size
        return image.crop((0, 0, max(1, width // 2), max(1, height // 2))).copy()


def _puppet(board: board_mod.Board, entry: dict):
    """Keyed RGBA for a character or prop sheet. Raises ComposeError if nothing keys."""
    path = board.stage_path(str(entry.get("id")))
    kind = board.stage_kind(entry)
    source: Path | object = path
    if kind == config.STAGE_CHARACTER:
        source = _front_cell(path)
    try:
        return media.cutout(source)
    except ValueError as failed:
        raise ComposeError(
            f"{board.stage_name(entry)} has no subject to cut from its sheet: {failed}",
            status=422,
        ) from failed


def ready(board: board_mod.Board, n: int) -> None:
    """Refuse up front when this beat cannot be assembled, before a job is queued."""
    if not any(beat["n"] == n for beat in board.beats):
        raise ComposeError(f"no such beat: {n}", status=404)
    bound = board.bound_staging(n)
    if not bound:
        raise ComposeError(
            f"scene {n} binds no designs. Bind a set or a puppet, then assemble.",
            status=422,
        )
    if not any(board.stage_path(str(entry.get("id"))).is_file() for entry in bound):
        raise ComposeError(
            f"scene {n} binds designs but none have a sheet on disk yet. Draw one first.",
            status=422,
        )


def layers_for(board: board_mod.Board, n: int, *,
               t: float = 0.0, pose: int = 0, seed: int = 7,
               jitter_px: float = 0.0, jitter_deg: float = 0.0) -> tuple[Path | None, list[dict]]:
    """Background path (or None) and placed cutouts for beat `n` at normalised time `t`.

    `t` is 0 at the opening and 1 at the end of the beat. `move` on the beat, if present,
    is `{id: {dx, dy}}` in frame fractions added across that span. Jitter is per-pose and
    seeded, so the same call is reproducible.
    """
    bound = board.bound_staging(n)
    if not bound:
        raise ComposeError(
            f"scene {n} binds no designs. Bind a set or a puppet, then assemble.",
            status=422,
        )
    sets = [entry for entry in bound
            if board.stage_kind(entry) == config.STAGE_ENVIRONMENT
            and board.stage_path(str(entry.get("id"))).is_file()]
    puppets = [entry for entry in bound
               if board.stage_kind(entry) != config.STAGE_ENVIRONMENT
               and board.stage_path(str(entry.get("id"))).is_file()]
    if not sets and not puppets:
        raise ComposeError(
            f"scene {n} binds designs but none have a sheet on disk yet. Draw one first.",
            status=422,
        )
    background = board.stage_path(str(sets[0].get("id"))) if sets else None
    move = board.beat(n).get("move") or {}
    if not isinstance(move, dict):
        move = {}
    rng = random.Random(seed + pose * 1009 + n * 17)
    layers = []
    for entry in puppets:
        entry_id = str(entry.get("id"))
        place = board.place_for(n, entry_id)
        delta = move.get(entry_id) or {}
        if not isinstance(delta, dict):
            delta = {}
        try:
            dx = float(delta.get("dx", 0.0)) * t
            dy = float(delta.get("dy", 0.0)) * t
        except (TypeError, ValueError):
            dx = dy = 0.0
        jx = rng.uniform(-jitter_px, jitter_px) / config.GEN_WIDTH if jitter_px else 0.0
        jy = rng.uniform(-jitter_px, jitter_px) / config.GEN_HEIGHT if jitter_px else 0.0
        rot = rng.uniform(-jitter_deg, jitter_deg) if jitter_deg else 0.0
        layers.append({
            "image": _puppet(board, entry),
            "x": place["x"] + dx + jx,
            "y": place["y"] + dy + jy,
            "scale": place["scale"],
            "rotation": rot,
        })
    return background, layers


def still(board: board_mod.Board, n: int, *,
          log: Callable[[str], None] = print) -> Path:
    """Write beat `n`'s opening still from its bound sheets. No Gemini, no GPU."""
    if not any(beat["n"] == n for beat in board.beats):
        raise ComposeError(f"no such beat: {n}", status=404)
    background, layers = layers_for(board, n)
    out = board.asset_path(n)
    media.compose_layers(background, layers, out)
    names = ", ".join(board.stage_name(entry) for entry in board.bound_staging(n)
                      if board.stage_path(str(entry.get("id"))).is_file())
    log(f"[compose] beat {n}: assembled {out.name} from {names}")
    return out


def _cadence(board: board_mod.Board, n: int) -> dict:
    stored = board.beat(n).get("cadence") or {}
    if not isinstance(stored, dict):
        stored = {}
    def _int(key: str, default: int) -> int:
        try:
            return int(stored.get(key, default))
        except (TypeError, ValueError):
            return default
    def _float(key: str, default: float) -> float:
        try:
            return float(stored.get(key, default))
        except (TypeError, ValueError):
            return default
    hold = max(1, _int("hold_frames", config.ASSEMBLE_HOLD))
    return {
        "hold_frames": hold,
        "jitter_px": max(0.0, _float("jitter_px", config.ASSEMBLE_JITTER_PX)),
        "jitter_deg": max(0.0, _float("jitter_deg", config.ASSEMBLE_JITTER_DEG)),
        "seed": _int("seed", board.data.get("seed", 7) or 7),
    }


def clip(board: board_mod.Board, n: int, *,
         log: Callable[[str], None] = print,
         cancelled: Callable[[], bool] | None = None) -> Path:
    """Hold-on-twos stop-motion for beat `n`, from the same sheets as `still`.

    Writes `beatN_assemble.mp4`. Does not touch the H3 clip or any fingerprint. Duration
    is this beat's snapped length, so a stitch of every beat matches the reel's timing.
    """
    if not any(beat["n"] == n for beat in board.beats):
        raise ComposeError(f"no such beat: {n}", status=404)
    beat = board.beat(n)
    frames = config.frame_count(board.seconds_for(beat))
    cadence = _cadence(board, n)
    hold = cadence["hold_frames"]
    poses = max(1, math.ceil(frames / hold))
    work = board.workdir / f".assemble_{n}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    try:
        written = 0
        for pose in range(poses):
            if cancelled is not None and cancelled():
                raise ComposeError("assemble cancelled", status=409)
            t = 0.0 if poses == 1 else pose / (poses - 1)
            background, layers = layers_for(
                board, n, t=t, pose=pose, seed=cadence["seed"],
                jitter_px=cadence["jitter_px"], jitter_deg=cadence["jitter_deg"],
            )
            frame = work / f"frame_{written:04d}.png"
            media.compose_layers(background, layers, frame)
            written += 1
            for _held in range(1, hold):
                if written >= frames:
                    break
                copy = work / f"frame_{written:04d}.png"
                shutil.copy2(frame, copy)
                written += 1
            if written >= frames:
                break
        while written < frames:
            last = work / f"frame_{written - 1:04d}.png"
            shutil.copy2(last, work / f"frame_{written:04d}.png")
            written += 1
        out = board.assemble_path(n)
        media.encode_frames(work, out)
        log(f"[assemble] beat {n}: {poses} poses on {hold}s, {frames} frames -> {out.name}")
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def reel(board: board_mod.Board, beats: list[int] | None = None, *,
         log: Callable[[str], None] = print,
         cancelled: Callable[[], bool] | None = None) -> Path:
    """Assemble every requested beat and stitch them. Missing clips are built first."""
    wanted = beats or [beat["n"] for beat in board.ordered_beats()]
    clips = []
    for n in wanted:
        if cancelled is not None and cancelled():
            raise ComposeError("assemble cancelled", status=409)
        path = board.assemble_path(n)
        if not path.is_file():
            path = clip(board, n, log=log, cancelled=cancelled)
        clips.append(path)
    if not clips:
        raise ComposeError("nothing to assemble", status=422)
    out = board.workdir / "assemble.mp4"
    media.stitch(clips, out, mute=True)
    log(f"[assemble] reel -> {out.name} ({len(clips)} beats)")
    return out
