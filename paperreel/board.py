"""The board document: a storyboard plus canvas layout, and the state derived from disk.

`reels/<slug>/storyboard.json` stays the only database. Node state is *derived* from what
is actually on disk rather than stored, so the CLIs and the studio UI can never drift out
of sync -- hand-edit the JSON, drop in your own PNG, or run storyboard.py, and the canvas
reflects it on the next read.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import config

# Where a beat's opening frame comes from. This is what the wire between two nodes means.
SOURCE_ASSET = "asset"  # its own generated still -- a new shot, costs one image quota
SOURCE_CHAIN = "chain"  # the previous beat's last frame -- continuous motion, free

# An explicit character reference, dropped in the reel directory. Every still generated for
# a cut is conditioned on it, which is what keeps the same characters across a scene change.
REFERENCE_NAME = "character.png"

# Node states, in the order the UI paints them.
PLANNED = "planned"          # prompt only
NEEDS_ASSET = "needs_asset"  # wants its own still, hasn't got one
READY = "ready"              # has everything it needs to render
RENDERING = "rendering"
RENDERED = "rendered"
STALE = "stale"              # rendered, but its own inputs changed since
INVALIDATED = "invalidated"  # rendered, but an upstream beat it chains from changed


def reels_dir() -> Path:
    path = config.ROOT / "reels"
    path.mkdir(parents=True, exist_ok=True)
    return path


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "reel"


def fingerprint(*parts: Any) -> str:
    """Identify the exact inputs a render was produced from.

    Anything in here, when changed, marks a beat stale and re-costs the render button.
    The opening frame is included by content hash, which is what makes chain staleness
    propagate: re-rendering beat 2 changes beat 3's first frame, so beat 3 goes stale
    without anyone touching its prompt.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode())
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


def image_aspect(path: Path) -> float | None:
    """Width/height of an image, read from its header. None if unreadable."""
    if not path.is_file():
        return None
    try:
        from PIL import Image
        with Image.open(path) as image:
            return round(image.width / image.height, 3)
    except Exception:  # noqa: BLE001
        return None


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@dataclass
class Board:
    slug: str
    path: Path
    data: dict

    # ## Reading and writing

    @classmethod
    def load(cls, slug: str) -> Board:
        path = reels_dir() / slug / "storyboard.json"
        if not path.is_file():
            raise FileNotFoundError(f"no storyboard at {path}")
        return cls(slug=slug, path=path, data=json.loads(path.read_text()))

    @classmethod
    def create(cls, slug: str, data: dict) -> Board:
        path = reels_dir() / slug / "storyboard.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        board = cls(slug=slug, path=path, data=data)
        board.save()
        return board

    @classmethod
    def all_slugs(cls) -> list[str]:
        return sorted(
            child.name for child in reels_dir().iterdir()
            if (child / "storyboard.json").is_file()
        )

    def save(self) -> Board:
        self.path.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        return self

    # ## Paths

    @property
    def workdir(self) -> Path:
        return self.path.parent

    def asset_path(self, n: int) -> Path:
        return self.workdir / f"beat{n}_asset.png"

    def frame_path(self, n: int) -> Path:
        return self.workdir / f"beat{n}_frame.png"

    def video_path(self, n: int) -> Path:
        return self.workdir / f"beat{n}.mp4"

    def reference_path(self) -> Path | None:
        """The still that fixes what the characters look like, for generating a new scene.

        A hard cut is where identity actually breaks: each beat's still used to be a fresh
        reading of the same paragraph of text, so the characters were redesigned per scene.
        Conditioning every still on one image instead is what makes a cut change the
        setting without changing who is in it.

        An explicit upload wins. Otherwise the first beat's own still stands in, which is
        the right default -- it is the shot the whole reel was designed from. None on a
        board with no still at all yet, where the first generation defines the look.
        """
        explicit = self.workdir / REFERENCE_NAME
        if explicit.is_file():
            return explicit
        ordered = self.ordered_beats()
        if ordered:
            first = self.asset_path(ordered[0]["n"])
            if first.is_file():
                return first
        return None

    @property
    def reel_path(self) -> Path:
        """Where a studio render writes the deliverable."""
        return self.workdir / f"{self.slug}_{config.REEL_WIDTH}x{config.REEL_HEIGHT}.mp4"

    def existing_reel(self) -> Path | None:
        """Any stitched deliverable, including the _draft / _final names storyboard.py uses."""
        if self.reel_path.exists():
            return self.reel_path
        found = sorted(
            self.workdir.glob(f"*_{config.REEL_WIDTH}x{config.REEL_HEIGHT}.mp4"),
            key=lambda p: p.stat().st_mtime,
        )
        return found[-1] if found else None

    # ## Beats

    @property
    def beats(self) -> list[dict]:
        return self.data.setdefault("beats", [])

    def beat(self, n: int) -> dict:
        for beat in self.beats:
            if beat["n"] == n:
                return beat
        raise KeyError(f"beat {n} not in {self.slug}")

    def upstream(self, n: int) -> dict | None:
        """The beat a chained node takes its first frame from -- the previous in order."""
        ordered = self.ordered_beats()
        for index, beat in enumerate(ordered):
            if beat["n"] == n:
                return ordered[index - 1] if index else None
        return None

    def ordered_beats(self) -> list[dict]:
        return sorted(self.beats, key=lambda b: b["n"])

    def renumber(self) -> None:
        """Compact beat numbers to 1..N after an add or remove.

        Renaming the files alongside keeps derived state honest; a stale beat3.mp4 left
        behind after a delete would otherwise show up as somebody else's finished render.
        """
        ordered = self.ordered_beats()
        moves = [(b, index + 1) for index, b in enumerate(ordered) if b["n"] != index + 1]
        if moves:
            # Two passes through a temp name, so a 3->2 shift cannot clobber beat 2's files.
            for beat, target in moves:
                for maker in (self.asset_path, self.frame_path, self.video_path):
                    src = maker(beat["n"])
                    if src.exists():
                        src.rename(src.with_name(f"tmp_{target}_{src.name}"))
            for beat, target in moves:
                for maker in (self.asset_path, self.frame_path, self.video_path):
                    final = maker(target)
                    staged = final.with_name(f"tmp_{target}_{maker(beat['n']).name}")
                    if staged.exists():
                        staged.replace(final)
                beat["n"] = target

        # This is the topology invariant behind the canvas: scene 1 has no incoming scene,
        # so it can never be chained. It matters especially after deleting the old scene 1.
        if ordered:
            ordered[0]["source"] = SOURCE_ASSET

    # ## Derived state
    #
    # Everything below is computed from the document plus the filesystem. Nothing here is
    # persisted, which is why an outside edit can never leave the canvas lying.

    def seconds_for(self, beat: dict) -> float:
        """One of the two offered lengths, always.

        Snapped on read as well as on write so a hand-edited storyboard, or a board made
        before the choice narrowed, still lines up with the two buttons on the node.
        """
        return config.snap_seconds(
            beat.get("seconds") or self.data.get("seconds") or config.BEAT_LENGTHS[-1]
        )

    def source_for(self, beat: dict) -> str:
        """Default the opening beat to its own asset; later beats inherit unless told."""
        explicit = beat.get("source")
        if explicit in (SOURCE_ASSET, SOURCE_CHAIN):
            return explicit
        return SOURCE_ASSET if self.upstream(beat["n"]) is None else SOURCE_CHAIN

    def identity(self) -> str:
        """The style bible: what the characters and the set look like, never how they move.

        Goes into the video prompt as well as the asset prompts, because a beat that drifts
        mid-clip drifts away from *this* description or from nothing at all.
        """
        return " ".join(str(self.data.get("style_bible") or "").split())

    def steps(self) -> int:
        return int(self.data.get("steps") or config.DEFAULT_STEPS)

    def seed_for(self, beat: dict) -> int:
        return int(self.data.get("seed") or 1101) + beat["n"]

    def render_fingerprint(self, beat: dict, *, frames: int | None = None,
                           frame_id: str | None = None) -> str:
        """What this beat WOULD be rendered from right now.

        `frames` overrides the length, so a draft pass can stamp what it ACTUALLY rendered
        rather than what the board asks for. Without that, a 5s draft would record the
        fingerprint of the 10s final and the canvas would call it finished.

        `frame_id` does the same for the opening still. A render reads the still off disk at
        the moment it starts a beat; if a new one is uploaded while the batch is still
        running, recomputing the hash afterwards would stamp the clip with an image it was
        never made from -- and the beat would show as finished when it needs redoing.
        """
        source = self.source_for(beat)
        if frame_id is None:
            frame_id = self.frame_id_for(beat)
        return fingerprint(
            beat.get("action", ""),
            # The scene line is in the video prompt too, so rewriting where a shot happens
            # really does change what it would render as.
            beat.get("scene", ""),
            frames if frames is not None else config.frame_count(self.seconds_for(beat)),
            self.steps(),
            self.seed_for(beat),
            bool(self.data.get("mute")),
            source,
            # The style bible is part of the video prompt now, so rewriting it really does
            # change what every beat would render as. Leaving it out would let the canvas
            # keep calling those clips finished.
            self.identity(),
            frame_id,
        )

    def frame_id_for(self, beat: dict) -> str:
        """Content hash of the still this beat opens on, as things stand right now."""
        if self.source_for(beat) == SOURCE_ASSET:
            return file_hash(self.asset_path(beat["n"]))
        # Chained: identified by whatever the upstream beat currently renders to, so this
        # changes the moment upstream is re-rendered.
        up = self.upstream(beat["n"])
        return file_hash(self.video_path(up["n"])) if up else ""

    def states(self, *, rendering: set[int] | None = None) -> dict[int, str]:
        """Every beat's state in one downstream pass.

        Has to be done together, not per beat: a beat's own fingerprint only detects an
        upstream re-render that already happened. A *pending* upstream change -- someone
        just rewrote beat 2's action -- leaves beat 3's fingerprint intact even though its
        first frame is about to move. So dirtiness propagates along the chain here, which
        is what stops the canvas from showing "rendered" on a beat the button is charging for.
        """
        result: dict[int, str] = {}
        upstream_dirty = False
        for beat in self.ordered_beats():
            state = self._own_state(beat, rendering=rendering)
            if state == RENDERED and upstream_dirty and self.source_for(beat) == SOURCE_CHAIN:
                state = INVALIDATED
            result[beat["n"]] = state
            # Anything other than a settled render means this beat's output will differ
            # from what downstream last chained off.
            upstream_dirty = state != RENDERED
        return result

    def state_of(self, beat: dict, *, rendering: set[int] | None = None) -> str:
        return self.states(rendering=rendering)[beat["n"]]

    def _own_state(self, beat: dict, *, rendering: set[int] | None = None) -> str:
        n = beat["n"]
        if rendering and n in rendering:
            return RENDERING

        recorded = (beat.get("render") or {}).get("fingerprint")
        has_video = self.video_path(n).exists()
        if has_video and recorded:
            if recorded == self.render_fingerprint(beat):
                return RENDERED
            # Distinguish "you changed this" from "the thing before it changed", because
            # only the first is the user's own doing and only the second cascades.
            own_changed = self.own_fingerprint(beat) != (beat.get("render") or {}).get("own")
            return STALE if own_changed else INVALIDATED
        if has_video:
            return STALE  # rendered by an older tool that left no fingerprint
        if self.source_for(beat) == SOURCE_ASSET and not self.asset_path(n).exists():
            return NEEDS_ASSET
        if beat.get("action"):
            return READY
        return PLANNED

    def own_fingerprint(self, beat: dict, *, frames: int | None = None,
                        frame_id: str | None = None) -> str:
        """What this beat is, ignoring anything inherited from upstream.

        Compared against the recorded value to tell "you changed this" from "the beat before
        it changed" -- only the first is the user's own doing, only the second cascades.

        A beat opening on its OWN still counts that still as part of itself: swapping the
        image is an edit you made, so it must read as `edited`, not as `follows a change` --
        which would be nonsense on beat 1, where there is nothing before it to follow.
        A chained beat's frame comes from upstream, so it stays out, which is exactly what
        keeps the two labels meaningful.
        """
        parts: list = [
            beat.get("action", ""),
            beat.get("scene", ""),
            frames if frames is not None else config.frame_count(self.seconds_for(beat)),
            self.steps(),
            self.seed_for(beat),
            bool(self.data.get("mute")),
            self.source_for(beat),
            # Board-wide, so editing it marks every beat `edited` rather than
            # `follows a change` -- correct, because you did edit it, and it is not
            # something a beat inherits from the one before it.
            self.identity(),
        ]
        if self.source_for(beat) == SOURCE_ASSET:
            parts.append(frame_id if frame_id is not None else self.frame_id_for(beat))
        return fingerprint(*parts)

    def pending(self, *, rendering: set[int] | None = None) -> list[int]:
        """Beats that would be rendered by 'render everything that needs it'.

        Chain-aware: once one beat is in, every later beat that chains off it must follow,
        because its first frame is about to change underneath it.
        """
        states = self.states(rendering=rendering)
        return [
            beat["n"] for beat in self.ordered_beats()
            if states[beat["n"]] in (READY, STALE, INVALIDATED)
        ]

    def cascade(self, beats: list[int]) -> list[int]:
        """Expand a manual selection to include everything chained downstream of it."""
        chosen = set(beats)
        dirty = False
        for beat in self.ordered_beats():
            if beat["n"] in chosen:
                dirty = True
                continue
            if dirty and self.source_for(beat) == SOURCE_CHAIN:
                chosen.add(beat["n"])
            elif self.source_for(beat) == SOURCE_ASSET:
                dirty = False
        return sorted(chosen)

    def cost_of(self, beats: list[int]) -> dict:
        """Predicted wall-clock and dollars for rendering exactly these beats."""
        frames = [
            config.frame_count(self.seconds_for(self.beat(n)))
            for n in beats if any(b["n"] == n for b in self.beats)
        ]
        seconds = config.predict_batch_seconds(frames, steps=self.steps())
        return {
            "beats": beats,
            "frames": frames,
            "predicted_seconds": round(seconds, 1),
            "predicted_cost": round(config.estimate_cost(seconds), 4),
            "video_seconds": round(sum(f / config.FPS for f in frames), 1),
        }

    def spent(self) -> float:
        """Everything this board has cost, cumulative across renders.

        Prefers the recorded container seconds, which include the model load and the gaps
        between beats. Falls back to summing per-beat costs for boards rendered before that
        was tracked -- which reads about 10% low.
        """
        seconds = self.data.get("spend_seconds")
        if seconds:
            return round(config.estimate_cost(float(seconds)), 4)
        return round(sum((b.get("render") or {}).get("cost", 0.0) for b in self.beats), 4)

    # ## Serialisation for the canvas

    def node_positions(self) -> dict:
        return self.data.setdefault("canvas", {}).setdefault("nodes", {})

    def to_json(self, *, rendering: set[int] | None = None) -> dict:
        beats = []
        states = self.states(rendering=rendering)  # once: each call hashes files
        for beat in self.ordered_beats():
            n = beat["n"]
            seconds = self.seconds_for(beat)
            frames = config.frame_count(seconds)
            beats.append({
                "n": n,
                "scene": beat.get("scene", ""),
                "action": beat.get("action", ""),
                "asset_prompt": beat.get("asset_prompt", ""),
                "seconds": seconds,
                # The snapped truth, so the canvas can show 10.2s when 10 was asked for.
                "frames": frames,
                "actual_seconds": round(frames / config.FPS, 2),
                "source": self.source_for(beat),
                "state": states[n],
                "asset": self.media_url(self.asset_path(n)),
                # So the node can warn when an uploaded still will be cropped hard.
                "asset_aspect": image_aspect(self.asset_path(n)),
                # The frame this beat actually opened on. A chained beat has no still of
                # its own, so this is the only thumbnail it can show.
                "frame": self.media_url(self.frame_path(n)),
                "video": self.media_url(self.video_path(n)),
                "predicted_seconds": round(
                    config.predict_render_seconds(frames, steps=self.steps()), 1
                ),
                "predicted_cost": round(
                    config.estimate_cost(
                        config.predict_render_seconds(frames, steps=self.steps())
                    ), 4,
                ),
                "render": beat.get("render"),
            })
        pending = self.pending(rendering=rendering)
        return {
            "slug": self.slug,
            "title": self.data.get("title", self.slug),
            "style_bible": self.data.get("style_bible", ""),
            "caption": self.data.get("caption", ""),
            "seconds": self.data.get("seconds", 10.0),
            "steps": self.steps(),
            "seed": self.data.get("seed", 1101),
            # The only lengths a beat may have; the node renders one button per entry.
            "lengths": list(config.BEAT_LENGTHS),
            "gen_aspect": round(config.GEN_WIDTH / config.GEN_HEIGHT, 3),
            "mute": bool(self.data.get("mute")),
            # Set when the stills are the user's own work: nothing on this board may spend
            # image quota, and every "generate" affordance is replaced by an upload.
            "manual_stills": bool(self.data.get("manual_stills")),
            # The still every cut's image is generated from, and whether it was chosen
            # deliberately or is just beat 1 standing in.
            "reference": self.media_url(self.reference_path()),
            "reference_explicit": (self.workdir / REFERENCE_NAME).is_file(),
            "beats": beats,
            "canvas": self.data.get("canvas", {}),
            "reel": self.media_url(self.existing_reel()),
            "pending": pending,
            "pending_cost": self.cost_of(pending),
            "draft_cost": self.cost_of_at(pending, config.DRAFT_SECONDS),
            "spent": self.spent(),
            "assets_needed": [
                b["n"] for b in self.ordered_beats()
                if self.source_for(b) == SOURCE_ASSET and not self.asset_path(b["n"]).exists()
            ],
        }

    def cost_of_at(self, beats: list[int], seconds: float) -> dict:
        frames = [config.frame_count(seconds) for _ in beats]
        total = config.predict_batch_seconds(frames, steps=self.steps())
        return {
            "predicted_seconds": round(total, 1),
            "predicted_cost": round(config.estimate_cost(total), 4),
            "video_seconds": round(sum(f / config.FPS for f in frames), 1),
        }

    def media_url(self, path: Path | None) -> str | None:
        """Cache-busted so a re-render replaces the thumbnail instead of showing the old one."""
        if path is None or not path.exists():
            return None
        return f"/media/{self.slug}/{path.name}?v={int(path.stat().st_mtime)}"


def summaries() -> Iterator[dict]:
    for slug in Board.all_slugs():
        try:
            board = Board.load(slug)
        except (OSError, json.JSONDecodeError):
            continue
        yield {
            "slug": slug,
            "title": board.data.get("title", slug),
            "beats": len(board.beats),
            "spent": board.spent(),
            "thumb": board.media_url(board.asset_path(1)),
            "reel": board.media_url(board.existing_reel()),
        }
