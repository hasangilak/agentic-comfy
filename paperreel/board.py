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

# Where a beat's frames come from. This is what the wire between two nodes means.
#
# H3 conditions on up to two keyframes -- a first and a last -- so there are three keyframe
# joins. The third is what lets a beat both continue AND be given a picture: the continuation
# goes in the first slot and the picture in the last, and the clip is the move between them.
#
# The fourth join is a different checkpoint rather than a different wiring, and it is now the
# DEFAULT for a beat that opens a shot. `ref2va` takes up to config.MAX_REF_IMAGES pictures and
# NO keyframe at all, so a reference beat composes its own opening frame -- but it does not
# compose it out of nothing any more. Its own still goes in as <Picture 1> and the reel's
# locked cast reference as <Picture 2>, so a cut carries two anchors where the keyframe cut
# carried one, and both of them keep asserting themselves through every sampling step instead
# of only fixing frame zero. That is the trade the default is making: the opening is close
# rather than pixel-exact, and the cast holds better for the ten seconds after it.
#
# `asset` did not go away -- it is the beat that needs its opening frame EXACTLY, since a
# keyframe latent is re-injected every step and never denoised. The two continuations cannot
# move: they hand over the previous clip's true last frame, and ref2va has no socket for one.
SOURCE_ASSET = "asset"    # its own still as the FIRST frame, exactly -- the keyframe cut
SOURCE_CHAIN = "chain"    # the previous beat's last frame -- continuous motion, free
SOURCE_BRIDGE = "bridge"  # continues from the previous clip AND lands on its own still
SOURCE_REFERENCE = "reference"  # the default cut: its still + the cast, on ref2va
SOURCES = (SOURCE_ASSET, SOURCE_CHAIN, SOURCE_BRIDGE, SOURCE_REFERENCE)


def chains(source: str) -> bool:
    """Does this beat's first frame come out of the clip before it?

    True for both continuations, which is what makes staleness propagate the same way
    down either of them: re-rendering a beat moves the frame the next one opens on.
    """
    return source in (SOURCE_CHAIN, SOURCE_BRIDGE)


def uses_asset(source: str) -> bool:
    """Does this beat's own still go into one of H3's two KEYFRAME slots?

    A cut opens on it; a bridge arrives at it. Either way it is handed to fl2va as a latent
    that is re-injected at every step, so the frame is exact.

    False for a reference beat, which also has a still of its own now but hands it over as
    <Picture 1> instead -- conditioning rather than a keyframe. So this is not the question
    "does a still have to exist on disk"; that one is `Board.wants_still`, which is a beat-level
    question because a reference beat carrying motion answers it differently. Same split as
    `chains` and `Board.follows_upstream`, and for the same reason.
    """
    return source in (SOURCE_ASSET, SOURCE_BRIDGE)


def uses_refs(source: str) -> bool:
    """Is this beat conditioned on reference pictures rather than on a keyframe?"""
    return source == SOURCE_REFERENCE


# A reference beat's optional link back to the clip before it. ref2va has no keyframe input,
# so a continuation cannot be a frame handoff here -- but the node does take reference VIDEO,
# and the tail of the previous clip in that slot is the same idea by other means: the model is
# shown where the take had got to rather than told where to start.
CARRY_UPSTREAM = "upstream"

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


@dataclass(frozen=True)
class FrameIds:
    """Content hashes of the images one beat is conditioned on, at one moment in time.

    Kept as two fields rather than one, because the halves mean opposite things to
    staleness: `asset` is a still the user put there, so changing it is an edit they made;
    `upstream` is inherited from the clip before, so changing it is a change to follow.
    A bridge beat has both, which is the whole reason this is a pair.

    `refs` is the third case and belongs to neither: a reference beat has no keyframe, so
    what it was conditioned on is the whole ordered set of pictures. Order is part of it --
    swapping <Picture 1> and <Picture 2> changes the prompt's meaning, so it must read as an
    edit rather than as no change at all.
    """

    asset: str = ""
    upstream: str = ""
    refs: str = ""


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

    def end_frame_path(self, n: int) -> Path:
        """The still a bridge beat has to arrive at, fitted onto the generation grid.

        Written at render time from `asset_path`, exactly as `frame_path` is for a cut, so
        what was handed to the model is on disk next to the clip it produced.
        """
        return self.workdir / f"beat{n}_end.png"

    def video_path(self, n: int) -> Path:
        return self.workdir / f"beat{n}.mp4"

    def carry_path(self, n: int) -> Path:
        """The tail of the previous clip, cut for use as this beat's reference video.

        Written at render time and kept next to the clip it produced, for the same reason
        `frame_path` is: what was handed to the model should be on disk afterwards.
        """
        return self.workdir / f"beat{n}_carry.mp4"

    def ref_path(self, n: int, index: int) -> Path:
        """One of a reference beat's pictures, numbered as the prompt names it.

        1-based on disk on purpose: `beat3_ref2.png` is the file the prompt calls
        <Picture 2>, so what the model was told and what is on disk can be read off each
        other without arithmetic. The graph's own sockets are 0-based; that conversion
        happens once, in comfy.build_graph.
        """
        return self.workdir / f"beat{n}_ref{index}.png"

    def ref_paths(self, n: int) -> list[Path]:
        """The reference pictures this beat actually has, in <Picture i> order.

        Gaps are skipped rather than preserved: deleting picture 2 of three must not leave
        the model conditioned on a hole, and the surviving files keep their own numbers only
        until the next write compacts them (see `compact_refs`).
        """
        return [p for p in (self.ref_path(n, i) for i in range(1, config.MAX_REF_IMAGES + 1))
                if p.is_file()]

    def next_ref_index(self, n: int) -> int | None:
        """The lowest free picture slot, or None when the beat is already at the cap.

        Capped on the UPLOAD budget rather than flat at config.MAX_REF_IMAGES, because two of
        the model's nine slots fill themselves on a beat that opens a shot -- its own still and
        the cast reference. Without this, the ninth upload would be accepted, written to disk,
        and then silently dropped by `pictures_for` when it truncated the list to what the node
        actually takes: a picture on the canvas that is not in the render.
        """
        budget = self.ref_budget(n)
        for index in range(1, config.MAX_REF_IMAGES + 1):
            if not self.ref_path(n, index).is_file():
                return index if index <= budget else None
        return None

    def ref_prompts(self, n: int) -> list[str]:
        """What each reference picture is FOR, aligned to `ref_paths` position by position.

        A reference is not self-explanatory: ref2va reproduces every subject it is shown, so
        a picture of the cast standing in the finished set reads as "this is what exists" and
        the model renders it AND the character the action describes -- two of the same puppet
        in one shot. Saying "<Picture 1> is the same single Moth that acts in this shot" is
        what collapses them back into one.

        Always exactly as long as the picture list. Missing entries come back empty rather
        than short, so index i of one list always describes index i of the other even after a
        hand-edit of the storyboard.
        """
        stored = self.beat(n).get("ref_prompts") or []
        count = len(self.ref_paths(n))
        return [str(stored[i]) if i < len(stored) else "" for i in range(count)]

    def set_ref_prompt(self, n: int, index: int, text: str) -> None:
        """Describe picture `index` (1-based, as the prompt names it)."""
        prompts = self.ref_prompts(n)
        if not 1 <= index <= len(prompts):
            raise IndexError(f"beat {n} has no reference picture {index}")
        prompts[index - 1] = " ".join(text.split())
        self._store_ref_prompts(n, prompts)

    def _store_ref_prompts(self, n: int, prompts: list[str]) -> None:
        """Write the list back, or drop the key when there is nothing left to say.

        Trailing empties are trimmed so a board where nobody described anything carries no
        `ref_prompts` at all -- the document stays readable, and the difference between "no
        notes" and "notes that happen to be blank" never has to be meaningful.
        """
        while prompts and not prompts[-1]:
            prompts.pop()
        beat = self.beat(n)
        if prompts:
            beat["ref_prompts"] = prompts
        else:
            beat.pop("ref_prompts", None)

    def discard_video(self, n: int) -> Path:
        """Throw away a beat's rendered clip, so it can be rendered again.

        Moved rather than deleted, exactly like a trashed reel: the clip cost real money and
        a mis-click must not be final. It goes to `.discarded/` inside the reel, which the
        media route cannot serve (it only serves files sitting directly in the reel
        directory), so the canvas stops offering it while the file itself survives.

        The frames go with it. They are render outputs, not inputs -- `beat3_frame.png` is
        whatever this beat opened on last time -- and leaving them behind would have the node
        showing a thumbnail from a clip that no longer exists. The beat's own still and its
        reference pictures are untouched: those are yours.

        The render record is cleared too, which is what puts the beat back to `ready` and
        marks everything chained below it as following a change.
        """
        video = self.video_path(n)
        if not video.is_file():
            raise FileNotFoundError(f"beat {n} has no rendered clip")
        trash = self.workdir / ".discarded"
        trash.mkdir(parents=True, exist_ok=True)
        stamp = int(video.stat().st_mtime)
        moved = trash / f"beat{n}-{stamp}{video.suffix}"
        video.replace(moved)
        for derived in (self.frame_path(n), self.end_frame_path(n)):
            derived.unlink(missing_ok=True)
        self.beat(n).pop("render", None)
        return moved

    def remove_ref(self, n: int, index: int) -> None:
        """Delete one reference picture and the note that described it, then close the gap.

        Both halves move together or the board starts lying: deleting <Picture 1> of three
        leaves files 2 and 3, which the prompt -- numbering by connection order -- would then
        call 1 and 2. If the notes did not shift with them, picture 1 would be rendered under
        the description written for the one that was deleted.
        """
        prompts = self.ref_prompts(n)
        if not 1 <= index <= len(prompts):
            raise IndexError(f"beat {n} has no reference picture {index}")
        self.ref_path(n, index).unlink(missing_ok=True)
        del prompts[index - 1]
        for target, path in enumerate(self.ref_paths(n), start=1):
            wanted = self.ref_path(n, target)
            if path != wanted:
                path.replace(wanted)
        self._store_ref_prompts(n, prompts)

    def media_makers(self) -> tuple:
        """Every per-beat file, so a move or a delete cannot leave one of them behind.

        One list in one place: a stale `beat3_end.png` after a delete is exactly the kind of
        orphan that later reads as somebody else's finished work. The reference pictures are
        in here for the same reason -- nine of them per beat, and a renumber that missed them
        would hand beat 2 the cast of the beat that used to be there.
        """
        refs = tuple(
            (lambda n, index=index: self.ref_path(n, index))
            for index in range(1, config.MAX_REF_IMAGES + 1)
        )
        return (self.asset_path, self.frame_path, self.end_frame_path, self.video_path,
                self.carry_path, *refs)

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

    def reference_for(self, n: int) -> Path | None:
        """What beat `n`'s still should be generated FROM, right now.

        `reference_path` with one exception, and the exception is what makes the cast
        changeable: regenerating the very still that IS the reference has to be free to
        redesign, or the first image a board ever produced would lock it forever.

        Read fresh on every call rather than computed once for a batch. Generating beat 1's
        still is what creates the reference for beats 2..N, so a value cached before the
        batch started would leave every later still unanchored.
        """
        reference = self.reference_path()
        if reference is not None and reference == self.asset_path(n):
            return None
        return reference

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
                for maker in self.media_makers():
                    src = maker(beat["n"])
                    if src.exists():
                        src.rename(src.with_name(f"tmp_{target}_{src.name}"))
            for beat, target in moves:
                for maker in self.media_makers():
                    final = maker(target)
                    staged = final.with_name(f"tmp_{target}_{maker(beat['n']).name}")
                    if staged.exists():
                        staged.replace(final)
                beat["n"] = target

        # This is the topology invariant behind the canvas: scene 1 has no incoming scene,
        # so it can never be chained. It matters especially after deleting the old scene 1.
        # A reference beat is left alone -- it takes nothing from upstream either way, so
        # promoting it to a cut would throw away its pictures for no reason.
        #
        # Demoted to the default cut, which now takes the still it already has as <Picture 1>
        # rather than as a keyframe. A beat that was chaining has no still of its own, so this
        # usually lands on `needs_still` either way -- the join is what changes, not the work.
        if ordered and chains(self.source_for(ordered[0])):
            ordered[0]["source"] = SOURCE_REFERENCE

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
        """Default a beat that opens a shot to the reference join; later beats inherit unless told.

        A beat with nothing before it cannot continue from anything, whatever the document
        says -- both continuations need an upstream clip to take their first frame from. So the
        two joins that stand on their own are what beat 1 can be, and the default of those is
        now `reference`: its still plus the cast, on ref2va, rather than one exact keyframe.

        An explicit `asset` is honoured everywhere, including in first position, and that is
        what makes this safe to change. Every board built before this has `"source": "asset"`
        written into beat 1 by `script.normalise`, so it keeps rendering as the keyframe cut it
        was rendered as -- nothing already on disk quietly changes checkpoint. The new default
        reaches a board through whoever writes the document: the planner, an import, an added
        beat.
        """
        explicit = beat.get("source")
        if explicit in (SOURCE_REFERENCE, SOURCE_ASSET):
            return explicit
        if self.upstream(beat["n"]) is None:
            return SOURCE_REFERENCE
        return explicit if explicit in SOURCES else SOURCE_CHAIN

    def carries_motion(self, beat: dict) -> bool:
        """Does this reference beat take the previous clip's tail as a reference video?

        Only meaningful on the reference join, and only where there IS a previous beat. The
        flag is stored per beat rather than derived, because it is a real editorial choice --
        a reference beat that starts a new shot and one that carries the last one on are the
        same conditioning with opposite intent.
        """
        return (
            uses_refs(self.source_for(beat))
            and beat.get("ref_video") == CARRY_UPSTREAM
            and self.upstream(beat["n"]) is not None
        )

    def follows_upstream(self, beat: dict) -> bool:
        """Does anything this beat renders from come out of the beat before it?

        True for both keyframe continuations and for a reference beat carrying motion. This is
        what staleness and the render cascade key on -- not the join name, since the reference
        join answers this question either way depending on its flag.
        """
        return chains(self.source_for(beat)) or self.carries_motion(beat)

    def opens_on_still(self, beat: dict) -> bool:
        """Does this reference beat open on a still drawn for it, rather than composing one?

        This is what makes `reference` the default cut rather than an uploads-only special case:
        the beat's own still goes in as <Picture 1> and the clip begins on that composition.
        Three things have to hold -- the beat is on this join, it is not opening on the previous
        clip's tail instead, and the still is actually on disk.

        The carry check is not a detail. A carried clip and an opening still are two different
        answers to where the shot begins, and `config.build_prompt` may only ever give one of
        them, so a beat set to carry does not wire its still at all.
        """
        return (
            uses_refs(self.source_for(beat))
            and not self.carries_motion(beat)
            and self.asset_path(beat["n"]).is_file()
        )

    def needs_still(self, beat: dict) -> bool:
        """Is this beat BLOCKED for want of `beat<n>_asset.png`?

        Not the same question as `uses_asset`, which asks whether a still goes into a keyframe
        slot. A cut and a bridge are blocked without one either way. A reference beat is only
        blocked when it has nothing else to be conditioned on: uploaded pictures do the job on
        their own, and a beat carrying the previous clip's tail opens on that instead.

        So this is what drives NEEDS_ASSET and `assets_needed`, and the uploads clause is what
        keeps a board built before the default moved to ref2va working untouched -- its
        reference beats have pictures and are not waiting for anything.
        """
        source = self.source_for(beat)
        if uses_asset(source):
            return True
        if not uses_refs(source):
            return False
        return not self.carries_motion(beat) and not self.ref_paths(beat["n"])

    def auto_pictures(self, n: int) -> list[tuple[Path, str]]:
        """The reference pictures that wire themselves, in <Picture i> order, with their roles.

        Two, on a beat that opens a shot: its own still as the composition to begin on, and the
        reel's locked cast reference so the characters keep being re-asserted through every
        sampling step rather than only at frame zero. Together they are the point of moving the
        default cut onto ref2va at all.

        The cast reference is deliberately NOT wired onto the other reference shapes -- an
        uploads-only beat, or one carrying the previous clip. Those already say what they are
        conditioned on, and quietly adding a picture to them would change what every board built
        before this rendered as, mark it stale, and charge for the extra reference tokens.

        `reference_for` returns None on the beat whose own still IS the reference, so beat 1 gets
        one picture rather than the same file twice.
        """
        if not self.opens_on_still(self.beat(n)):
            return []
        found = [(self.asset_path(n), config.REF_ROLE_OPENING)]
        cast = self.reference_for(n)
        if cast is not None:
            found.append((cast, config.REF_ROLE_CAST))
        return found

    def pictures_for(self, n: int) -> list[tuple[Path, str]]:
        """Everything this beat is conditioned on, in <Picture i> order, paired with its role.

        Pairs rather than two parallel lists, and that is the whole reason this method exists:
        position IS meaning here -- the prompt names each picture by its index -- so a path list
        and a note list that could drift by one is a bug waiting for the first auto-wired slot.
        Index i of this list is what the prompt calls <Picture i+1>, note included.

        Truncated at the model's cap rather than raising: `next_ref_index` already refuses an
        upload that would not fit, so reaching the limit here means a hand-edited board, and
        rendering the first nine pictures beats refusing to render at all.
        """
        if not uses_refs(self.source_for(self.beat(n))):
            return []
        uploaded = list(zip(self.ref_paths(n), self.ref_prompts(n)))
        return (self.auto_pictures(n) + uploaded)[:config.MAX_REF_IMAGES]

    def still_pictures(self, n: int, limit: int | None = None) -> list[tuple[Path, str]]:
        """What beat `n`'s STILL is drawn from, paired with what each picture is for.

        A different list from `pictures_for`, and the difference is the beat's own still: that is
        the thing being generated here, so it cannot also condition itself. What is left is the
        reel's locked cast reference -- the one image a still has always been conditioned on, and
        first here because that is the order it was the only entry in -- followed by the
        director's uploads on this beat.

        Sending the uploads to the still renderer as well as to the video model is the point.
        They are pictures of the cast, the set or a prop: the things that must look the same in
        this shot as everywhere else in the film. Conditioning the clip on them while the frame
        it opens on was drawn from the style bible alone left the two disagreeing about the same
        puppet, and the frame is what the clip's first sampling steps are anchored to.

        Guarded on `uses_refs` for the same reason `pictures_for` is: a beat moved off the
        reference join keeps its uploaded files on disk, and a picture that is not in the video
        render must not quietly steer the still either. One rule, read off the join.

        Pairs, like `pictures_for`, though the still prompt names nothing by number: the notes are
        the director's words about a specific picture, and a path list beside a note list that can
        slip by one is the bug that method exists to make impossible.
        """
        found: list[tuple[Path, str]] = []
        cast = self.reference_for(n)
        if cast is not None:
            found.append((cast, ""))
        if uses_refs(self.source_for(self.beat(n))):
            found += list(zip(self.ref_paths(n), self.ref_prompts(n)))
        cap = config.MAX_STILL_REFS if limit is None else min(limit, config.MAX_STILL_REFS)
        return found[:max(0, cap)]

    def ref_budget(self, n: int) -> int:
        """How many pictures the director may upload to this beat, after the automatic ones.

        Seven rather than nine on a beat that opens a shot. Read off `auto_pictures` so the two
        can never disagree about how many slots are already spoken for.
        """
        return max(0, config.MAX_REF_IMAGES - len(self.auto_pictures(n)))

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
                           frame_ids: FrameIds | None = None) -> str:
        """What this beat WOULD be rendered from right now.

        `frames` overrides the length, so a draft pass can stamp what it ACTUALLY rendered
        rather than what the board asks for. Without that, a 5s draft would record the
        fingerprint of the 10s final and the canvas would call it finished.

        `frame_ids` does the same for the conditioning stills. A render reads them off disk
        at the moment it starts a beat; if a new one is uploaded while the batch is still
        running, recomputing the hashes afterwards would stamp the clip with an image it was
        never made from -- and the beat would show as finished when it needs redoing.
        """
        source = self.source_for(beat)
        if frame_ids is None:
            frame_ids = self.frame_ids_for(beat)
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
            self.carries_motion(beat),
            # The style bible is part of the video prompt now, so rewriting it really does
            # change what every beat would render as. Leaving it out would let the canvas
            # keep calling those clips finished.
            self.identity(),
            frame_ids.asset,
            frame_ids.upstream,
            frame_ids.refs,
        )

    def frame_ids_for(self, beat: dict) -> FrameIds:
        """Content hashes of the images this beat is conditioned on, as things stand now.

        A bridge carries both keyframe halves: its own still is the last frame it has to
        reach, and the upstream clip is the first frame it starts from. Swapping either one
        really does change what the beat would render as. A reference beat carries neither,
        and its pictures are hashed together in order instead.
        """
        source = self.source_for(beat)
        asset = file_hash(self.asset_path(beat["n"])) if uses_asset(source) else ""
        upstream = ""
        if self.follows_upstream(beat):
            # Identified by whatever the upstream beat currently renders to, so this changes
            # the moment upstream is re-rendered. A carried reference video hashes the same
            # file for the same reason: it IS the previous clip.
            up = self.upstream(beat["n"])
            upstream = file_hash(self.video_path(up["n"])) if up else ""
        refs = ""
        if uses_refs(source):
            # Positional, so reordering the pictures counts as an edit -- which it is, since
            # the prompt names them by position. The notes are in here for the same reason
            # the action is: they are words the model is given, so rewriting one really does
            # change what the beat would render as.
            #
            # Taken off `pictures_for`, so the automatic slots are hashed too. Two consequences
            # worth knowing: the beat's own still is in here rather than in `asset` above, and
            # re-rendering beat 1's still moves the cast reference, which marks every beat
            # conditioned on it as edited. That is the same call `identity()` makes a few lines
            # down for the style bible -- board-wide inputs read as an edit, because changing
            # one is something you did rather than something a beat inherits.
            #
            # Hashed as (file, note) pairs in order, so swapping two pictures changes the
            # fingerprint even when both notes stay put.
            refs = fingerprint(*(
                part for path, note in self.pictures_for(beat["n"])
                for part in (file_hash(path), note)
            ))
        return FrameIds(asset=asset, upstream=upstream, refs=refs)

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
            if state == RENDERED and upstream_dirty and self.follows_upstream(beat):
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
        # One question for all four joins now: is this beat short of the still it renders from?
        # A cut and a bridge want it in a keyframe slot, and the default reference cut wants it
        # as <Picture 1>. `needs_still` is what knows that uploaded pictures and a carried clip
        # are each a complete answer on their own -- ref2va with nothing connected at all is
        # text-to-video wearing the wrong checkpoint, which is the case this catches.
        if self.needs_still(beat) and not self.asset_path(n).exists():
            return NEEDS_ASSET
        if beat.get("action"):
            return READY
        return PLANNED

    def own_fingerprint(self, beat: dict, *, frames: int | None = None,
                        frame_ids: FrameIds | None = None) -> str:
        """What this beat is, ignoring anything inherited from upstream.

        Compared against the recorded value to tell "you changed this" from "the beat before
        it changed" -- only the first is the user's own doing, only the second cascades.

        A beat with a still of its OWN counts that still as part of itself -- whether it
        opens on it or arrives at it. Swapping the image is an edit you made, so it must read
        as `edited`, not as `follows a change`, which would be nonsense on beat 1 where there
        is nothing before it to follow. The inherited frame stays out, which is exactly what
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
            # Whether this beat carries the previous clip in as a reference video is its own
            # editorial choice, not something inherited, so flipping it reads as `edited`.
            self.carries_motion(beat),
            # Board-wide, so editing it marks every beat `edited` rather than
            # `follows a change` -- correct, because you did edit it, and it is not
            # something a beat inherits from the one before it.
            self.identity(),
        ]
        source = self.source_for(beat)
        if uses_asset(source) or uses_refs(source):
            # A reference beat's pictures are its own work in exactly the same way a cut's
            # still is: nothing upstream can change them, so changing one reads as `edited`.
            ids = frame_ids if frame_ids is not None else self.frame_ids_for(beat)
            parts.append(ids.asset)
            parts.append(ids.refs)
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
        """Expand a manual selection to include everything chained downstream of it.

        A bridge does not stop the cascade: it still takes its first frame from the clip
        before it, so a re-render upstream moves the ground under it exactly as it does under
        a plain continuation. A cut breaks the run, because a cut's first frame is a file on
        disk that nothing upstream can change -- and so does a reference beat conditioned only
        on its own pictures. A reference beat CARRYING motion does not break it: the previous
        clip is one of its references, so moving that clip moves it too.
        """
        chosen = set(beats)
        dirty = False
        for beat in self.ordered_beats():
            if beat["n"] in chosen:
                dirty = True
                continue
            if dirty and self.follows_upstream(beat):
                chosen.add(beat["n"])
            elif not self.follows_upstream(beat):
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
                # The conversation about this still: what the director asked for, and what the
                # automatic review made of each render. Not derived and not a render input --
                # it is the record of how the picture got to be what it is, which is why it
                # lives on the beat rather than being recomputed.
                "asset_chat": beat.get("asset_chat") or [],
                # The frame this beat actually opened on. A chained beat has no still of
                # its own, so this is the only thumbnail it can show.
                "frame": self.media_url(self.frame_path(n)),
                # And, for a bridge, the frame it was told to arrive at.
                "end_frame": self.media_url(self.end_frame_path(n)),
                # The reference pictures the DIRECTOR added, in the order they were added.
                # Deliberately not the whole conditioning set: these are the ones that can be
                # removed and described, and `ref_path` numbers them from 1, so keeping the
                # list to them is what lets the canvas address one without arithmetic.
                "refs": [self.media_url(p) for p in self.ref_paths(n)],
                # What each of those pictures is for, same order, same length. Empty string
                # where nothing has been said about one yet.
                "ref_prompts": self.ref_prompts(n),
                # The slots that filled themselves: the beat's own still as the composition to
                # open on, and the reel's cast reference. Read-only on the canvas -- they follow
                # the still and the reference rather than being editable in their own right.
                "auto_refs": [
                    {"url": self.media_url(path), "note": note}
                    for path, note in self.auto_pictures(n)
                ],
                # How far the director's pictures are pushed down the numbering by those. The
                # prompt calls upload i <Picture ref_offset + i>, and the node has to show the
                # same number the model is told or the notes describe the wrong picture.
                "ref_offset": len(self.auto_pictures(n)),
                # What is left of the model's nine after the automatic ones.
                "ref_slots": self.ref_budget(n),
                # How many of the director's pictures also condition the STILL, which takes far
                # fewer than the video model: the cast reference is one of them, and the still
                # renderer encodes every picture through every sampling step. Derived here rather
                # than worked out on the canvas so there is one answer to "is this picture in the
                # still" -- see `still_pictures`.
                "still_refs": max(
                    0, len(self.still_pictures(n)) - (self.reference_for(n) is not None)
                ),
                # Whether this beat's still is wired as the composition it opens on. False on a
                # reference beat carrying the previous clip, which opens on that instead.
                "opens_on": self.opens_on_still(beat),
                # A reference beat can also be shown the tail of the previous clip, which is
                # how this join gets continuity without a keyframe.
                "carry": self.carries_motion(beat),
                # The clip that was actually sent, once it has been.
                "carry_clip": self.media_url(self.carry_path(n)),
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
            # Every beat short of the still it renders from, whichever join it is on. Reference
            # beats are in here now rather than in a list of their own: generating a still for
            # one puts it in <Picture 1> and leaves the join alone, so it no longer risks
            # turning a beat conditioned on pictures into a cut -- which is the only reason the
            # two lists were ever kept apart.
            "assets_needed": [
                b["n"] for b in self.ordered_beats()
                if self.needs_still(b) and not self.asset_path(b["n"]).exists()
            ],
            # The node grows one upload slot per picture and stops here. Per-beat `ref_slots` is
            # the number that actually matters on a node; this is the model's hard cap.
            "max_refs": config.MAX_REF_IMAGES,
            # And the still renderer's much smaller cap, so a node can say how many of a beat's
            # pictures reach the still as well as the clip. The image server may report a lower
            # one, which `papercut.max_references` honours -- this is the ceiling, not a promise.
            "max_still_refs": config.MAX_STILL_REFS,
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
