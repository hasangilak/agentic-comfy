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

# Everything a staging entry stores, and what a missing key reads as. One tuple rather than four
# `.get(key, default)` calls scattered through the module, for the reason `REF_SLOT_KEYS` is one
# tuple: a hand-edited storyboard, an older board and a freshly created entry must all present
# the same shape, and a fifth field added later must not need finding in six places.
STAGE_FIELDS: tuple[tuple[str, object], ...] = (
    ("kind", config.STAGE_CHARACTER),
    ("name", ""),
    ("note", ""),    # what it IS, in the director's words -- reaches both prompts
    ("draw", ""),    # the Gemini prompt the sheet was drawn from -- reaches neither
    ("chat", None),  # the conversation about it; None means "a fresh list each time"
)

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

    def panel_path(self, n: int) -> Path:
        """This beat's storyboard panel -- a sketch of the shot, drawn to be looked at and nothing else.

        The one per-beat image that reaches no renderer: it is not conditioning, it is not a
        keyframe, and it is in no fingerprint. See `panels.py` and `config.PANEL_STYLE_SUFFIX`.

        Directly in the reel directory like every other per-beat file, because `api.media_file`
        serves only files whose parent IS that directory -- a panel in a subfolder would render
        and then never be visible.
        """
        return self.workdir / f"beat{n}_panel.png"

    def sheet_path(self) -> Path:
        """The whole reel's panels stitched into one numbered contact sheet.

        Reel-level rather than per-beat, and named without a beat number for that reason. Rebuilt
        by `panels.sheet` after any panel changes, so it is derived state that happens to be a
        file -- deleting it costs nothing but a redraw of the sheet.
        """
        return self.workdir / "storyboard_sheet.png"

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

    # Everything a beat stores per reference picture, one entry per file in `ref_paths`. They
    # are read and written through one pair of methods rather than a trio each, because the
    # thing that has to be true of all of them is the same thing: `remove_ref` deletes index
    # i-1 from EVERY one of them, and a list that grew its own accessor is a list somebody
    # forgets there. `blank` is what a missing entry reads as.
    REF_SLOT_KEYS: tuple[tuple[str, object], ...] = (
        ("ref_prompts", ""),   # what the picture is FOR -- reaches both prompts
        ("ref_draws", ""),     # the Gemini prompt it was drawn from -- reaches neither
        ("ref_chats", None),   # the conversation about it; None means "a fresh list each time"
        ("ref_ids", ""),       # opaque, minted on store, the one value a renumber preserves
    )

    def _ref_slots(self, n: int, key: str, blank) -> list:
        """One per-picture list, padded or truncated to exactly `len(ref_paths(n))`.

        Always exactly as long as the picture list. Missing entries come back blank rather
        than short, so index i of one list always describes index i of the others even after a
        hand-edit of the storyboard. `blank=None` means a fresh mutable per call -- shared
        `[]` defaults across four slots would have appending to one append to all of them.
        """
        stored = self.beat(n).get(key) or []
        count = len(self.ref_paths(n))
        return [
            stored[i] if i < len(stored)
            else (blank if blank is not None else [])
            for i in range(count)
        ]

    def _store_ref_slots(self, n: int, key: str, values: list) -> None:
        """Write one list back, or drop the key when there is nothing left in it.

        Trailing blanks are trimmed so a board where nobody described, drew or discussed
        anything carries none of these keys at all -- the document stays readable, and the
        difference between "no notes" and "notes that happen to be blank" never has to be
        meaningful. Falsiness is the test, which covers "" and [] together.
        """
        values = list(values)
        while values and not values[-1]:
            values.pop()
        beat = self.beat(n)
        if values:
            beat[key] = values
        else:
            beat.pop(key, None)

    def ref_prompts(self, n: int) -> list[str]:
        """What each reference picture is FOR, aligned to `ref_paths` position by position.

        A reference is not self-explanatory: ref2va reproduces every subject it is shown, so
        a picture of the cast standing in the finished set reads as "this is what exists" and
        the model renders it AND the character the action describes -- two of the same puppet
        in one shot. Saying "<Picture 1> is the same single Moth that acts in this shot" is
        what collapses them back into one.

        Reaches both renderers: `config.reference_roles` turns it into "<Picture 3> is ..." for
        the video model, and `papercut._beat_text` splices it into "The reference images show:
        ..." for the still. That is why it is not the same field as `ref_draws`.
        """
        return [str(value) for value in self._ref_slots(n, "ref_prompts", "")]

    def ref_draws(self, n: int) -> list[str]:
        """The Gemini prompt each reference picture was last drawn from, or "" for an upload.

        The analogue of `asset_prompt` for a picture, and kept apart from `ref_prompts` for the
        same reason `asset_prompt` is kept apart from `scene`: one says what to draw, the other
        says what the drawing is for, and they read in different registers. "A close-up of an
        iron-grey club on flat black" is a good draw prompt and a terrible end to the sentence
        "<Picture 3> is ...".

        Reaches no renderer. It is an input to `pictures.draw` alone, which is why it is
        deliberately absent from the fingerprint -- see `frame_ids_for`.
        """
        return [str(value) for value in self._ref_slots(n, "ref_draws", "")]

    def ref_chats(self, n: int) -> list[list[dict]]:
        """The conversation about each reference picture, aligned position by position.

        Per picture rather than one feed on the beat, unlike `asset_chat`: a mixed transcript
        would let one chatty picture evict the still's automatic review verdicts, which are the
        thing that makes a surprising still legible at all.
        """
        return [list(value or []) for value in self._ref_slots(n, "ref_chats", None)]

    def ref_ids(self, n: int) -> list[str]:
        """A stable handle for each picture, minted on store and preserved across a renumber.

        The file index is not one. `remove_ref` compacts, so `beat3_ref3.png` becomes
        `beat3_ref2.png` and every position-shaped thing pointing at it -- a mention typed into
        an action, the modal's current selection, a queued draw job -- silently re-points at a
        different picture. An id is the one address that survives that, which is why the
        mention token carries it rather than a number.

        Backfilled for any picture a hand-edit or an older board left without one, so the list
        is never short and never has to be checked for holes.
        """
        stored = [str(value) for value in self._ref_slots(n, "ref_ids", "")]
        if all(stored):
            return stored
        filled = [value or self._mint_ref_id(n, stored) for value in stored]
        for index, value in enumerate(filled):
            stored[index] = value
        self._store_ref_slots(n, "ref_ids", filled)
        return filled

    def _mint_ref_id(self, n: int, taken: list[str]) -> str:
        """A short id no picture on this beat is already using.

        Random rather than counted: a counter would have to live somewhere, and the only place
        to keep it is the beat -- a second piece of state whose whole job is to not disagree
        with the list beside it. Six hex characters is 16M, against nine pictures.
        """
        import secrets

        while True:
            candidate = secrets.token_hex(3)
            if candidate not in taken:
                return candidate

    def set_ref_prompt(self, n: int, index: int, text: str) -> None:
        """Describe picture `index` (1-based, as the prompt names it)."""
        self._set_ref_text(n, index, "ref_prompts", text)

    def store_ref_chats(self, n: int, chats: list[list[dict]]) -> None:
        """Write every picture's transcript back. The trim to a memory length is the caller's.

        Public because `pictures.remember` owns how long a conversation is kept -- that is a
        prompt-budget decision and lives beside the other ones -- while the trailing-blank rule
        that keeps a quiet board free of empty keys belongs here with its three siblings.
        """
        self._store_ref_slots(n, "ref_chats", chats)

    def set_ref_draw(self, n: int, index: int, text: str) -> None:
        """Say what picture `index` (1-based) should be drawn as."""
        self._set_ref_text(n, index, "ref_draws", text)

    def _set_ref_text(self, n: int, index: int, key: str, text: str) -> None:
        values = self._ref_slots(n, key, "")
        if not 1 <= index <= len(values):
            raise IndexError(f"beat {n} has no reference picture {index}")
        values[index - 1] = " ".join(text.split())
        self._store_ref_slots(n, key, values)
        # Minting here rather than on upload is what keeps `ref_ids` honest without a second
        # write path: every route that touches a picture reads it, and `ref_ids` backfills.
        self.ref_ids(n)

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
        """Delete one reference picture and everything the beat said about it, then close the gap.

        Every half moves together or the board starts lying: deleting <Picture 1> of three
        leaves files 2 and 3, which the prompt -- numbering by connection order -- would then
        call 1 and 2. If the notes did not shift with them, picture 1 would be rendered under
        the description written for the one that was deleted. The same is now true of the draw
        prompt and the conversation, which is why they are one list of keys rather than four
        places to remember.

        The mentions are the half that reaches outside the picture list. A sentence saying
        "@ref:a1b2c3 is what he swings" outlives the picture it names, and the expander would
        quietly drop it at render time -- the beat would read one way on screen and render
        another. So the token is replaced here by what the picture was FOR, which keeps the
        sentence, and removed entirely when nothing was ever said about it. Lossy, and the
        honest option: a reference to a file that does not exist is not recoverable, only
        hideable.

        Done here rather than in the route because `agent.apply_ops` and the CLI reach this
        method without going through HTTP.

        Deliberately NOT rewritten: `asset_chat` and `ref_chats`. Those are history, and
        editing what was already said is exactly the drift `agent.transcript` exists to prevent.
        """
        count = len(self.ref_paths(n))
        # Bounds-checked against the FILES, not against a text list. They are the same length
        # by construction, but a hand-edited `ref_draws` that ran long must not widen what this
        # method accepts -- it would delete a note for a picture that was never there.
        if not 1 <= index <= count:
            raise IndexError(f"beat {n} has no reference picture {index}")

        # Every list is read BEFORE the file goes, and that ordering is the whole trick.
        # `_ref_slots` sizes itself off `ref_paths`, so one read taken after the unlink is
        # already a picture short -- it silently drops the LAST entry, and then deleting
        # index i-1 on top of that loses a second one. Read at the old length, delete the one
        # entry that is actually going, write back at the new length.
        keeping = {key: self._ref_slots(n, key, blank) for key, blank in self.REF_SLOT_KEYS}
        going_id = str(self.ref_ids(n)[index - 1])
        going_role = self.ref_prompts(n)[index - 1]

        self.ref_path(n, index).unlink(missing_ok=True)
        for target, path in enumerate(self.ref_paths(n), start=1):
            wanted = self.ref_path(n, target)
            if path != wanted:
                path.replace(wanted)
        for key, values in keeping.items():
            del values[index - 1]
            self._store_ref_slots(n, key, values)

        self._drop_mentions(n, going_id, going_role)

    def _drop_mentions(self, n: int, going_id: str, role: str) -> None:
        """Replace every mention of a picture that has gone with what it was for.

        Scanned: the beat's own words and the words about its other pictures. Not the reel's
        style bible or another beat -- a picture is beat-scoped, so a token naming it cannot
        have been valid anywhere else.
        """
        if not going_id:
            return
        beat = self.beat(n)
        for field in ("scene", "action", "asset_prompt"):
            if beat.get(field):
                beat[field] = config.drop_mention(str(beat[field]), going_id, role)
        for key in ("ref_prompts", "ref_draws"):
            values = self._ref_slots(n, key, "")
            if any(values):
                self._store_ref_slots(
                    n, key,
                    [config.drop_mention(str(value), going_id, role) for value in values],
                )

    def mentions(self, n: int, pictures: list[tuple[Path, str]]) -> dict[str, tuple[int | None, str]]:
        """Where each mentionable picture sits in `pictures`, and what it is FOR.

        Keyed by the token body that names it -- a picture's id, or `config.CAST_MENTION` --
        and valued as a pair, for the same reason `pictures_for` returns pairs: the position IS
        the meaning here, and a positions dict beside a roles dict is one edit away from
        expanding a token onto the picture next to it.

        Positions are found by matching PATHS against the list handed in, never by counting the
        automatic slots. That is what lets one method serve `pictures_for` (own still, cast,
        uploads), `still_pictures` (identity sheets or the cast still, capped at four) and a
        picture redraw (itself, then the cast) without any of them having to declare how they
        are ordered -- and it is what makes a truncated list answer None for the pictures that
        fell off the end, which is the signal the expander needs to stop naming a position the
        model was never given.
        """
        where = {path: position for position, (path, _note) in enumerate(pictures, start=1)}
        found: dict[str, tuple[int | None, str]] = {}
        for index, (path, role) in enumerate(zip(self.ref_paths(n), self.ref_prompts(n))):
            token = self.ref_ids(n)[index]
            if token:
                found[token] = (where.get(path), role)
        # `reference_for`, not `reference_path`: the cast slot in both picture lists comes from
        # it, so this is the cast AS THIS BEAT SEES IT. On the beat whose own still is the
        # reference that is None, and `@cast` correctly degrades to its role text rather than
        # resolving onto <Picture 1> and telling the model its opening composition is the cast
        # sheet. (`pictures.conditioning` asks the other question and uses `reference_path`.)
        cast = self.reference_for(n)
        if cast is not None:
            found[config.CAST_MENTION] = (where.get(cast), config.CAST_MENTION_ROLE)
        # Every staging entry the reel has, not only the ones this beat binds. A token naming an
        # unbound sheet is not a mistake to punish: it resolves to a position of None and
        # degrades to what the design IS, which is exactly "the wardrobe, tall and lacquered
        # black" spliced into the sentence -- the director named a design without spending one of
        # nine picture slots on it, which is a reasonable thing to want.
        for entry in self.staging:
            body = config.STAGE_MENTION_PREFIX + str(entry.get("id"))
            found[body] = (where.get(self.stage_path(str(entry.get("id")))),
                           self.stage_role(entry))
        return found

    def stage_mentions(self, pictures: list[tuple[Path, str]]) -> dict[str, tuple[int | None, str]]:
        """Resolve the @-tokens in a REEL-level text -- a staging sheet's own draw prompt.

        The staging entries and the cast, and deliberately not `@ref:`. A beat's picture is
        beat-scoped, and a design sheet has no beat: a token naming one here could only ever mean
        a picture from some other shot, so it degrades to nothing rather than quietly resolving
        onto one.

        `@cast` is in, with no position ever. It is beat 1's own still -- a composed shot -- and
        conditioning a design sheet on it is the failure `pictures.draw_text` records, where "a
        single iron-grey club" against a fox reference came back as the fox. So naming it here
        gets the words and never the image, which is exactly right for "the same orange as
        @cast".
        """
        where = {path: position for position, (path, _note) in enumerate(pictures, start=1)}
        found: dict[str, tuple[int | None, str]] = {
            config.CAST_MENTION: (None, config.CAST_MENTION_ROLE),
        }
        for entry in self.staging:
            body = config.STAGE_MENTION_PREFIX + str(entry.get("id"))
            found[body] = (where.get(self.stage_path(str(entry.get("id")))),
                           self.stage_role(entry))
        return found

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
        # The panel is in here despite reaching no renderer, and for exactly the reason the
        # docstring gives: `renumber` renames through this tuple, so a panel left out would hand
        # beat 2 the sketch of the beat that used to be there -- and a panel is read by eye, which
        # is the one kind of orphan nothing else would catch.
        return (self.asset_path, self.frame_path, self.end_frame_path, self.video_path,
                self.carry_path, self.panel_path, *refs)

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

    # ## Staging -- the reel's cast and sets, designed once and bound to the beats that use them
    #
    # The layer between the style bible (one paragraph, reel-wide, words only) and a beat's own
    # reference pictures (images, one beat). A staging entry is named, written down, drawn once
    # as a design sheet, and bound to whichever beats it appears in -- so the same wolf reaches
    # every shot it is in as the same image rather than as another reading of the same sentence.
    #
    # Reel-scoped on purpose, which is what makes it different from everything above it. A
    # picture on beat 3 cannot be used by beat 7 without being uploaded again; the whole point of
    # a bible is that it is the same one everywhere.

    def stage_path(self, entry_id: str) -> Path:
        """The design sheet for one staging entry.

        Directly in the reel directory rather than in a subfolder, and that is a constraint
        rather than a preference: `api.media_file` serves only files whose parent IS the reel
        directory (a prefix check alone would let a name climb out with ".."), so a sheet in
        `staging/` would exist, render and never be visible on the canvas.

        Named by id, not by position or name: an entry can be renamed and the bible reordered,
        and neither may move a file that a bound beat and an @-mention both point at.
        """
        return self.workdir / f"stage_{entry_id}.png"

    @property
    def staging(self) -> list[dict]:
        """The design bible, for reading. Never `setdefault`, unlike `beats`.

        Reading a board must not write to it. `beats` gets away with defaulting in place because
        every board has some; a bible is optional, and a property that materialised an empty list
        would put `"staging": []` into every storyboard.json on the next save of every reel that
        has never used the feature. Same rule `_store_ref_slots` follows for the per-picture
        lists, and for the same reason: the document stays readable, and "no bible" and "an empty
        bible" never have to mean different things.

        The two mutators reach for the stored list through `_staging`.
        """
        return self.data.get("staging") or []

    def _staging(self) -> list[dict]:
        """The stored list, created on demand. For the two methods that actually change it."""
        return self.data.setdefault("staging", [])

    def stage_entry(self, entry_id: str) -> dict:
        for entry in self.staging:
            if str(entry.get("id")) == entry_id:
                return entry
        raise KeyError(f"no staging entry {entry_id!r} in {self.slug}")

    def stage_field(self, entry: dict, key: str):
        """One field of a staging entry, with the shape a missing key reads as.

        Every read goes through here so a hand-edited board, an entry written by an older
        version and a fresh one are indistinguishable to everything downstream.
        """
        blank = dict(STAGE_FIELDS)[key]
        value = entry.get(key)
        if key == "chat":
            return list(value or [])
        if value in (None, ""):
            return blank
        return value

    def stage_kind(self, entry: dict) -> str:
        kind = str(self.stage_field(entry, "kind"))
        return kind if kind in config.STAGE_KINDS else config.STAGE_CHARACTER

    def stage_name(self, entry: dict) -> str:
        return " ".join(str(self.stage_field(entry, "name")).split()) or "an unnamed design"

    def stage_role(self, entry: dict) -> str:
        """What this sheet IS, as the phrase that follows "<Picture 3> is ".

        The name always leads, whether or not the director wrote a note, and that is the whole
        job of this method. Both prompts name the pictures by position and neither has any other
        way to learn that <Picture 3> and the "Vera" the action line talks about are the same
        animal -- so a role that read "the fox mother, warm orange" would describe the sheet
        perfectly and still leave the model free to put a second fox on screen. That is the
        two-of-the-same-character failure `ref_prompts` was written for, one level up.

        For characters, MiniMax-H3's ref2va path needs the role to say appearance-only: the
        sheet fixes look, not this shot's pose or framing. Without that clause the model treats
        a design sheet as a scene to replay. Environments keep place wording -- they *are* the
        place.
        """
        name = self.stage_name(entry)
        kind = self.stage_kind(entry)
        note = " ".join(str(self.stage_field(entry, "note")).split()).strip().rstrip(".")
        if note:
            role = f"{name}, {note}"
        else:
            role = config.STAGE_ROLE[kind].format(name=name)
        if kind == config.STAGE_CHARACTER and note:
            role = (
                f"{role} -- appearance reference only: fixes what {name} looks like, "
                f"not this shot's pose or framing, and the same single {name} performs "
                f"the action below"
            )
        return role

    def bound_staging(self, n: int) -> list[dict]:
        """The staging entries beat `n` binds, in bind order, skipping ids that have gone.

        Bind order is the director's: it is the order the sheets are numbered in, and reordering
        is a real edit for the same reason reordering a beat's pictures is -- the prompt names
        them by position.

        A dangling id is dropped rather than raising. `remove_stage` unbinds as it deletes, so
        this only fires on a hand-edited board, and a bible entry that is not there is not a
        reason to refuse to render the beat.
        """
        try:
            bound = self.beat(n).get("staging") or []
        except KeyError:
            return []
        found = []
        for entry_id in bound:
            try:
                found.append(self.stage_entry(str(entry_id)))
            except KeyError:
                continue
        return found

    def staging_pictures(self, n: int, *, for_still: bool) -> list[tuple[Path, str]]:
        """The bound design sheets that go into THIS render as images, with their roles.

        The two callers want different ORDERINGS, and the difference is the one measured
        constraint in the whole feature: the video model takes nine pictures and the still
        renderer takes `config.MAX_STILL_REFS` (four, one of which is already the cast
        reference). When the still's cap bites, the set is what should give -- characters are
        what drift visibly between shots; a clearing redrawn from a fixed sentence is
        survivable, a wolf that changes species is not. So on the still side environments
        sort last and the truncation in `still_pictures` drops them first, and `staging_text`
        picks up whatever falls off as words. Dropping the set unconditionally was the
        spider-flea reel's five-different-webs failure: a set that fits the cap is a picture,
        because prose invents a new geometry for it in every shot.

        A sheet with no file on disk is skipped here and picked up as text by `staging_text`, so
        writing the bible is useful before a single sheet has been drawn.
        """
        found = []
        for entry in self.bound_staging(n):
            path = self.stage_path(str(entry.get("id")))
            if path.is_file():
                found.append((path, self.stage_role(entry),
                              self.stage_kind(entry) == config.STAGE_ENVIRONMENT))
        if for_still:
            # Stable sort: characters and props keep their binding order, sets move behind
            # them, so a tight cap sacrifices the set first rather than whatever was bound
            # last.
            found.sort(key=lambda item: item[2])
        return [(path, role) for path, role, _is_set in found]

    def staging_text(self, n: int, shown: list[tuple[Path, str]]) -> str:
        """What the bound sheets say, for the ones this render was NOT handed as pictures.

        One rule, applied by both renderers: a sheet the model can see is described by position
        in `ref_notes` / the still's notes clause, and a sheet it cannot see is described in
        prose. Saying it both ways is the failure worth avoiding -- a model told "<Picture 2> is
        the clearing" AND "also designed: the clearing, a moonlit ring of birches" reads two
        clearings, and puts both in the shot.

        `shown` is the picture list this particular render is being given, so the same board
        answers differently for the clip and for the still it opens on. Matched on path rather
        than on id, which is what lets a truncated list answer correctly without either caller
        having to know where the truncation happened.
        """
        seen = {path for path, _role in shown}
        lines = [
            self.stage_role(entry) for entry in self.bound_staging(n)
            if self.stage_path(str(entry.get("id"))) not in seen
        ]
        return "; ".join(line.rstrip(".") for line in lines if line)

    def staging_digest(self, n: int) -> str:
        """One hash of everything beat `n`'s staging contributes, or "" when it binds nothing.

        Empty rather than a hash-of-nothing, and that is the point: every caller appends this to
        a fingerprint only when it is non-empty, so a board built before staging existed keeps
        the byte-identical fingerprint it had -- and its rendered beats stay `rendered` rather
        than all going stale and re-pricing a paid render the moment this shipped.

        Everything that reaches a prompt is in here: the order, the kind (which decides whether a
        sheet is an image or a sentence on the still side), the role text, and the sheet's own
        content hash. The draw prompt is deliberately absent, for the reason `ref_draws` is --
        it produces an image, and the image is hashed.
        """
        bound = self.bound_staging(n)
        if not bound:
            return ""
        return fingerprint(*(
            part for entry in bound
            for part in (str(entry.get("id")), self.stage_kind(entry), self.stage_role(entry),
                         file_hash(self.stage_path(str(entry.get("id")))))
        ))

    def add_stage(self, *, kind: str, name: str, note: str = "", draw: str = "") -> dict:
        """Mint one staging entry. No file: a sheet exists because it was drawn or uploaded.

        Same rule as a beat's reference pictures, and for the same reason -- `ref_paths` is
        file-existence based there, and here a bible entry with a placeholder image would be a
        picture `staging_pictures` picks up and a render pays reference tokens for.
        """
        if kind not in config.STAGE_KINDS:
            raise ValueError(f"kind must be one of {', '.join(config.STAGE_KINDS)}")
        if len(self.staging) >= config.MAX_STAGE_SHEETS:
            raise ValueError(
                f"this reel already has {config.MAX_STAGE_SHEETS} designed things, which is the "
                "ceiling. Remove one first."
            )
        entry = {
            "id": self._mint_stage_id(),
            "kind": kind,
            "name": " ".join(str(name).split()),
            "note": " ".join(str(note).split()),
            "draw": " ".join(str(draw).split()),
        }
        self._staging().append(entry)
        return entry

    def _mint_stage_id(self) -> str:
        """A short id no staging entry is using. Random for the reason `_mint_ref_id` is."""
        import secrets

        taken = {str(entry.get("id")) for entry in self.staging}
        while True:
            candidate = secrets.token_hex(3)
            if candidate not in taken:
                return candidate

    def set_stage_chat(self, entry_id: str, turns: list[dict]) -> None:
        """Write one sheet's transcript back. The trim to a memory length is the caller's.

        Same split as `store_ref_chats`: how long a conversation is kept is a prompt-budget
        decision and lives beside the other ones, in `staging.py`.
        """
        self.stage_entry(entry_id)["chat"] = list(turns)

    def bind_stage(self, n: int, ids: list[str]) -> list[str]:
        """Set exactly which sheets beat `n` uses, in the order they are to be numbered.

        Replaces rather than appends, because the canvas control is a set of toggles and "which
        of these does this shot contain" is one answer, not a series of additions. Unknown ids
        are dropped and duplicates collapsed, so a stale tab cannot bind a sheet that has just
        been deleted or number the same one twice.

        Deliberately does NOT move the join. A beat's own uploads do (`api.store_refs`), because
        an upload only reaches a render through the reference join and storing one otherwise
        means nothing. A staging entry always reaches the render: as pictures on the reference
        join, and as words on every other one. So there is nothing here to warn about and
        nothing to silently change.
        """
        known = [str(entry.get("id")) for entry in self.staging]
        wanted: list[str] = []
        for entry_id in ids:
            entry_id = str(entry_id)
            if entry_id in known and entry_id not in wanted:
                wanted.append(entry_id)
        beat = self.beat(n)
        if wanted:
            beat["staging"] = wanted
        else:
            beat.pop("staging", None)
        return wanted

    def remove_stage(self, entry_id: str) -> None:
        """Delete one staging entry, its sheet, every binding to it, and every mention of it.

        All four move together or the board starts lying, exactly as in `remove_ref`. The
        difference is reach: a picture is beat-scoped, so a token naming one could only ever be
        valid on its own beat, while a staging entry is reel-scoped -- so the mention rewrite has
        to walk every beat.

        The token becomes what the entry WAS, which keeps the sentence readable, and nothing when
        it was never described. Lossy and honest: a reference to a design that no longer exists
        is not recoverable, only hideable.

        The transcripts are deliberately untouched, for the reason `remove_ref` leaves them
        alone: they are history, and editing what was already said is the drift `agent.transcript`
        exists to prevent.
        """
        entry = self.stage_entry(entry_id)
        role = self.stage_role(entry)
        self.stage_path(entry_id).unlink(missing_ok=True)
        remaining = self._staging()
        remaining.remove(entry)
        # Dropped rather than left as `[]`, so removing the last design leaves the document
        # exactly as it was before there was a bible at all.
        if not remaining:
            self.data.pop("staging", None)
        body = config.STAGE_MENTION_PREFIX + entry_id
        for beat in self.beats:
            bound = [str(i) for i in (beat.get("staging") or []) if str(i) != entry_id]
            if bound:
                beat["staging"] = bound
            else:
                beat.pop("staging", None)
            for field in ("scene", "action", "asset_prompt"):
                if beat.get(field):
                    beat[field] = config.drop_mention(str(beat[field]), body, role)
            for key in ("ref_prompts", "ref_draws"):
                values = self._ref_slots(beat["n"], key, "")
                if any(values):
                    self._store_ref_slots(
                        beat["n"], key,
                        [config.drop_mention(str(value), body, role) for value in values],
                    )

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

        Three tiers, in this order and for this reason: the automatic slots (this shot's own
        opening composition, then the cast) come first because they always did; the reel's bound
        staging sheets come next, because they are the film's fixed designs and a beat cannot
        change them; the director's own uploads come last, because they are this one shot's and
        appending is what keeps a new upload from renumbering the ones already described.

        Binding a sheet DOES renumber the uploads below it, and that is safe by construction
        rather than by luck: this method returns (path, role) pairs, so every note travels with
        its picture, and `mentions` resolves by path rather than by position.
        """
        if not uses_refs(self.source_for(self.beat(n))):
            return []
        uploaded = list(zip(self.ref_paths(n), self.ref_prompts(n)))
        return (
            self.auto_pictures(n) + self.staging_pictures(n, for_still=False) + uploaded
        )[:config.MAX_REF_IMAGES]

    def still_identity_sheets(self, n: int) -> list[tuple[Path, str]]:
        """Bound character and prop sheets on disk, in binding order.

        These are the identity lock for a still. Beat 1's composed opening is a camera angle; a
        turnaround is a puppet. Sending both made Gemini copy the wide.
        """
        found = []
        for entry in self.bound_staging(n):
            if self.stage_kind(entry) == config.STAGE_ENVIRONMENT:
                continue
            path = self.stage_path(str(entry.get("id")))
            if path.is_file():
                found.append((path, self.stage_role(entry)))
        return found

    def still_pictures(self, n: int, limit: int | None = None) -> list[tuple[Path, str]]:
        """What beat `n`'s STILL is drawn from, paired with what each picture is for.

        A different list from `pictures_for`, and the difference is the beat's own still: that is
        the thing being generated here, so it cannot also condition itself. What is left, in this
        order:

          1. the reel's locked cast reference -- beat 1's still -- ONLY when this beat binds no
             character or prop sheet. A turnaround is the puppet; the composed wide is a shot,
             and sending both locked every later still to that camera;
          2. the bound design sheets, characters and props first, environments last so the
             four-slot cap drops the set first (`staging_pictures`). A set that fits is a
             picture: dropping it unconditionally was five different webs from one environment
             note. Sheets are join-agnostic -- an asset or bridge still still has to match the
             puppets;
          3. the director's uploads, only on a reference join. `uses_refs` still gates those,
             because a picture on a keyframe beat reaches the clip never, and must not quietly
             steer the still either.

        Beat 1 itself has no cast slot (`reference_for` is None) and now gets the sheets, so the
        defining still is drawn from the designs rather than from a paragraph.

        Pairs, like `pictures_for`: the notes are the director's words about a specific picture,
        and a path list beside a note list that can slip by one is the bug that method exists to
        make impossible. The still prompt names nothing by number.
        """
        found: list[tuple[Path, str]] = []
        if not self.still_identity_sheets(n):
            cast = self.reference_for(n)
            if cast is not None:
                found.append((cast, ""))
        found += self.staging_pictures(n, for_still=True)
        if uses_refs(self.source_for(self.beat(n))):
            found += list(zip(self.ref_paths(n), self.ref_prompts(n)))
        cap = config.MAX_STILL_REFS if limit is None else min(limit, config.MAX_STILL_REFS)
        return found[:max(0, cap)]

    def ref_budget(self, n: int) -> int:
        """How many pictures the director may upload to this beat, after everything automatic.

        Seven rather than nine on a beat that opens a shot, and fewer again once it binds
        staging sheets. Read off the same two methods `pictures_for` composes from, so the budget
        and the render can never disagree about how many slots are already spoken for -- an
        upload the render would truncate away has to be refused, not stored.
        """
        spoken = len(self.auto_pictures(n))
        if uses_refs(self.source_for(self.beat(n))):
            spoken += len(self.staging_pictures(n, for_still=False))
        return max(0, config.MAX_REF_IMAGES - spoken)

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
        # Appended only when the beat binds something, which is what keeps every board built
        # before staging existed on the byte-identical fingerprint it already had. Adding an
        # unconditional part here would mark every rendered beat in every reel stale at once and
        # re-price a paid render for a feature nobody had used yet.
        #
        # It overlaps `frame_ids.refs` on a reference beat, where the sheets are hashed as
        # pictures too. Harmless -- both move together -- and it is what carries staging onto the
        # three joins that have no picture list at all: a chained beat is given the same sheets
        # as words, so rewriting one really does change what it would render as.
        staging = self.staging_digest(beat["n"])
        parts: list = [
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
        ]
        if staging:
            parts.append(staging)
        # Both conditional and both appended after the staging part, in this order, in BOTH
        # fingerprints. `fingerprint()` is positional, so a part inserted mid-list rather than at
        # the end would rehash every beat that has the parts before it -- and the two methods
        # disagreeing on order is how a beat flips between `stale` and `invalidated`.
        blocking = " ".join(str(beat.get("blocking") or "").split())
        if blocking:
            parts.append(blocking)
        medium = self.medium_digest()
        if medium:
            parts.append(medium)
        return fingerprint(*parts)

    def medium(self) -> str:
        """Which medium this reel is made of. Absent means the default, and must keep meaning it.

        Every board written before the medium bundle existed has no `medium` key, so a missing
        one has to resolve to paper cutout AND has to hash to nothing -- see `medium_digest`.
        A key naming a medium nobody ships falls back rather than raising (`config.medium`),
        because this document is hand-editable and can predate a rename.
        """
        return str(self.data.get("medium") or config.DEFAULT_MEDIUM)

    def look(self) -> config.Medium:
        return config.medium(self.medium())

    def medium_digest(self) -> str:
        """The medium, for the fingerprints -- and empty on every board that never named one.

        The `staging_digest` rule, applied to something board-wide: an unconditional part would
        rehash every beat of every reel at once, mark them all stale, and re-price a paid render
        over a feature nobody had used. Empty when the board is in the default medium, whether it
        says so or says nothing, so the two are indistinguishable to the hash -- which they have
        to be, since a director who sets the medium to paper cutout has changed nothing.
        """
        key = self.medium()
        return "" if key == config.DEFAULT_MEDIUM else f"medium:{key}"

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
        # Reel-wide, like the style bible above, so redrawing a sheet marks every beat that binds
        # it `edited` rather than `follows a change` -- correct, because you did edit it, and it
        # is not something a beat inherits from the one before it. Conditional for the reason
        # `render_fingerprint` gives: a board that binds nothing keeps the fingerprint it had.
        staging = self.staging_digest(beat["n"])
        if staging:
            parts.append(staging)
        # The beat's blocking is in the video prompt, so rewriting where things stand really does
        # change what it would render as -- and it is the beat's own line, not something inherited,
        # so it reads as `edited`. The medium is board-wide like the style bible above. Both
        # conditional, both last, in the same order as `render_fingerprint`.
        blocking = " ".join(str(beat.get("blocking") or "").split())
        if blocking:
            parts.append(blocking)
        medium = self.medium_digest()
        if medium:
            parts.append(medium)
        # Deliberately absent: `panel` and the panel image. A storyboard panel is a sketch of the
        # shot that reaches no renderer, so nothing about the render changes when one is redrawn --
        # and putting it in here would mark every beat of every existing board `edited` at once and
        # re-price a paid render over a drawing nobody rendered from. Same reasoning that keeps
        # `staging_digest` conditional, one step stronger: this part is never added at all.
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
        from . import critique as critique_mod

        beats = []
        states = self.states(rendering=rendering)  # once: each call hashes files
        for beat in self.ordered_beats():
            n = beat["n"]
            seconds = self.seconds_for(beat)
            frames = config.frame_count(seconds)
            still = self.still_pictures(n)
            cast = self.reference_for(n)
            stage_paths = {path for path, _ in self.staging_pictures(n, for_still=True)}
            upload_paths = set(self.ref_paths(n))
            beats.append({
                "n": n,
                "scene": beat.get("scene", ""),
                "action": beat.get("action", ""),
                "asset_prompt": beat.get("asset_prompt", ""),
                "gemini_model": beat.get("gemini_model"),
                "gemini_image_size": beat.get("gemini_image_size"),
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
                # The shot grammar this beat's storyboard panel is drawn from -- shot size, angle,
                # camera move -- and the panel itself. Shown on every join, unlike the reference
                # pictures: a panel is a sketch of the shot rather than an input to it, so no join
                # can make one unreachable.
                "panel": beat.get("panel", ""),
                "panel_url": self.media_url(self.panel_path(n)),
                # Beside the panel because they answer next to each other -- the panel says how
                # the shot is framed and this says what is standing in it. Unlike the panel, it
                # reaches the render and is in both fingerprints, conditionally.
                "blocking": beat.get("blocking", ""),
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
                # What each was last drawn from, "" for one that was uploaded rather than drawn.
                "ref_draws": self.ref_draws(n),
                # The conversation about each, one transcript per picture rather than one feed
                # on the beat -- see `ref_chats`.
                "ref_chats": self.ref_chats(n),
                # The handle that survives a delete. The canvas keys its selection off these
                # rather than off the position, because `remove_ref` compacts: without them,
                # deleting picture 2 of four leaves the modal showing "picture 3" while the
                # panel beside it edits what used to be picture 4. It is also what an @-mention
                # carries.
                "ref_ids": self.ref_ids(n),
                # The slots that filled themselves: the beat's own still as the composition to
                # open on, and the reel's cast reference. Read-only on the canvas -- they follow
                # the still and the reference rather than being editable in their own right.
                "auto_refs": [
                    {"url": self.media_url(path), "note": note}
                    for path, note in self.auto_pictures(n)
                ],
                # Which of the reel's design sheets this scene uses, in the order they are
                # numbered. Ids rather than objects: the sheets themselves are published once at
                # board level, and a second copy per beat is a second thing to keep in step.
                "staging": [str(entry.get("id")) for entry in self.bound_staging(n)],
                # How many of them actually reach the clip and the still as PICTURES. Counted
                # off the lists those renders are handed, after the still's cap, so a set that
                # fitted is a picture here and a set that did not is only in staging_still_text.
                "staging_refs": len(self.staging_pictures(n, for_still=False)),
                "staging_still_refs": sum(1 for path, _ in still if path in stage_paths),
                # Whether beat 1's still is in this beat's still_pictures. False when character
                # sheets are the identity lock -- the canvas must not draw a cast slot Gemini
                # is never handed.
                "still_cast": bool(cast) and any(path == cast for path, _ in still),
                # What the sheets this render was not handed say instead, exactly as the model
                # will be told it. Published rather than recomputed on the canvas so there is
                # one answer to "does binding this set actually do anything here".
                #
                # Two of them, because the two renders answer differently: the clip has nine
                # picture slots, the still has four, and a set that does not fit the still's cap
                # arrives as prose. One field showing the clip's empty answer beside "1 of 2
                # reach the still" reads as a bug.
                "staging_text": self.staging_text(n, self.pictures_for(n)),
                "staging_still_text": self.staging_text(n, still),
                # How far the director's pictures are pushed down the numbering by the automatic
                # slots AND the bound sheets. The prompt calls upload i <Picture ref_offset + i>,
                # and the node has to show the same number the model is told or the notes
                # describe the wrong picture.
                "ref_offset": len(self.auto_pictures(n)) + len(
                    self.staging_pictures(n, for_still=False)
                ) if uses_refs(self.source_for(beat)) else len(self.auto_pictures(n)),
                # What is left of the model's nine after the automatic ones.
                "ref_slots": self.ref_budget(n),
                # How many of the director's pictures also condition the STILL, counted off the
                # capped list `still_pictures` actually returns -- see that method.
                "still_refs": sum(1 for path, _ in still if path in upload_paths),
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
            # The reel's cast and sets, designed once and bound to the beats that use them. Sent
            # whole rather than as ids alone: this is the one list the canvas renders on its own
            # panel, and it is small -- MAX_STAGE_SHEETS entries, and the sheets themselves go
            # over as URLs.
            "staging": [
                {
                    "id": str(entry.get("id")),
                    "kind": self.stage_kind(entry),
                    "name": self.stage_name(entry),
                    "note": str(self.stage_field(entry, "note")),
                    "draw": str(self.stage_field(entry, "draw")),
                    "chat": self.stage_field(entry, "chat"),
                    "sheet": self.media_url(self.stage_path(str(entry.get("id")))),
                    # What the prompts are actually told this design is, name included --
                    # derived here so the panel shows the sentence the model gets rather than
                    # a second rendering of the same fields.
                    "role": self.stage_role(entry),
                }
                for entry in self.staging
            ],
            "stage_kinds": list(config.STAGE_KINDS),
            "max_staging": config.MAX_STAGE_SHEETS,
            # What this reel is made of, and what else it could be. Derived through `medium()`
            # so a board that never named one publishes the default rather than an empty string,
            # which is what lets the canvas show a selection without writing one to disk.
            "medium": self.medium(),
            "mediums": [{"key": entry.key, "name": entry.name}
                        for entry in config.MEDIUMS.values()],
            # Phase cursor for the gated crew. Workflow state only -- like chat, not like a
            # fingerprint -- so editing it re-prices nothing. Absent on boards that never ran
            # a gated phase; the studio treats that as "start at the first phase of next_stage".
            "crew": (
                {"done": list(self.data["crew"].get("done") or []),
                 "awaiting": self.data["crew"].get("awaiting")}
                if isinstance(self.data.get("crew"), dict) else None
            ),
            # Standing inspect failures, derived from `asset_chat`. The canvas render bar
            # confirms against this rather than 409ing the render API -- `manual_stills`,
            # imported boards and `reel.py` must still be able to spend.
            "inspect_failing": [
                {"beat": item["beat"], "lens": item["lens"], "text": item["text"]}
                for item in critique_mod.failing(self)
            ],
            "beats": beats,
            # The whole reel's panels on one numbered sheet, or None until one has been built.
            # Reel-level because that is what a storyboard is -- the sequence read at once.
            "panel_sheet": self.media_url(self.sheet_path()),
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
            # What is thin about this script -- a missing style bible, a cut with no prompt. It
            # was already computed once, at import, and shown as a strip that vanished; a board
            # document IS a plan document, so the same function answers for one at any age.
            #
            # Derived, never persisted, and in no fingerprint: it is a reading of the beats, not
            # an input to anything. The import is local because `script` imports this module.
            "notes": self.script_notes(),
            # The node grows one upload slot per picture and stops here. Per-beat `ref_slots` is
            # the number that actually matters on a node; this is the model's hard cap.
            "max_refs": config.MAX_REF_IMAGES,
            # And the still renderer's much smaller cap, so a node can say how many of a beat's
            # pictures reach the still as well as the clip. The image server may report a lower
            # one, which `papercut.max_references` honours -- this is the ceiling, not a promise.
            "max_still_refs": config.MAX_STILL_REFS,
        }

    def script_notes(self) -> list[str]:
        """What is thin about this script, from the same function the import path uses.

        `script.notes` was written against a freshly normalised plan, where every beat carries a
        `source`. A board on disk may not: `source_for` is what resolves the default, and a beat
        written before that field existed simply has no key. So the beats go over as a view with
        the join resolved -- which is the same answer `notes` would have got from a plan, and is
        what keeps this one function answering for a script at any age.

        The import is local because `script.py` imports this module; at the top it is a cycle.
        """
        if not self.beats:
            return []
        from . import script

        return script.notes({
            "style_bible": self.data.get("style_bible", ""),
            "beats": [
                {"n": beat["n"],
                 "source": self.source_for(beat),
                 "asset_prompt": beat.get("asset_prompt", "")}
                for beat in self.ordered_beats()
            ],
        })

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
