"""The agy conversation that drives the board.

`agy -p` is a one-shot, non-interactive call, so there is no long-lived tool loop to hold
open. Instead each turn asks for a structured reply plus a list of board operations, and
this module applies them. The conversation is replayed from the board document every turn,
which also means it survives a page reload or a restart of the studio server.

Deliberately absent: any operation that spends money. The agent can write, rewrite,
reorder and re-time every beat, and it can spend image quota when asked, but only a human
presses render.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import board as board_mod
from . import config, planner

# Kept flat rather than a oneOf union: models fill flat schemas far more reliably, and the
# apply step below validates anyway.
OPS = [
    "set_script",    # title / style_bible
    "set_beat",      # scene / action / asset_prompt / seconds on beat n
    "add_beat",      # insert a beat at position n
    "remove_beat",   # delete beat n
    "set_source",    # n + source: "asset" (own still) or "chain" (previous last frame)
    "set_caption",   # the Instagram caption on the reel node
    "set_reel",      # board-wide seconds / steps
]

CHAT_SCHEMA = {
    "type": "object",
    "required": ["reply", "ops"],
    "properties": {
        "reply": {
            "type": "string",
            "description": "One or two sentences to the user. Plain, no markdown, no lists.",
        },
        "ops": {
            "type": "array",
            "description": (
                "Board edits to apply. Empty when the user only asked a question. "
                "Never include an op that does not change something."
            ),
            "items": {
                "type": "object",
                "required": ["op"],
                "properties": {
                    "op": {"type": "string", "enum": OPS},
                    "n": {"type": "integer", "description": "which beat, 1-based"},
                    "title": {"type": "string"},
                    "style_bible": {"type": "string"},
                    "scene": {"type": "string"},
                    "action": {"type": "string"},
                    "asset_prompt": {"type": "string"},
                    "seconds": {"type": "number", "enum": [5, 10],
                                "description": "beat length; only 5 or 10 are allowed"},
                    "steps": {"type": "integer"},
                    "source": {"type": "string", "enum": ["asset", "chain"]},
                    "caption": {"type": "string"},
                },
            },
        },
    },
}

SYSTEM = f"""You are the story editor for a paper-cutout stop-motion Instagram Reel studio.
You edit a board of beats. Each beat is ONE continuous shot from a locked-off camera.

Hard rules of the medium -- breaking these wastes the user's money:
- The camera never moves, pans, zooms or cuts inside a beat.
- Only one thing animates at a time. No new characters walk into frame.
- No dialogue, no on-screen text, no watermarks.
- The same character appears in every beat and must be described identically. The
  style_bible holds that description; reuse its exact wording in every asset_prompt.
- An `action` describes only what MOVES. Appearance belongs in the style_bible.

Every beat is either 5 or 10 seconds. There is no other length -- anything else you ask for
will be snapped to the nearer of the two, so choose one of them deliberately. Use 5 for a
quick gesture and 10 for a beat that needs room to breathe.

Each beat's opening frame comes from one of two places, and it matters:
- "asset": its own generated still. A clean new shot. Costs one image from a quota of
  roughly five per five hours, so it is the scarce resource.
- "chain": the previous beat's final frame. Continuous motion, perfect continuity, free.

Reply briefly, then return the ops that carry out what was asked. Return ops ONLY for what
the user actually asked to change. Return JSON only."""


def board_digest(board: board_mod.Board) -> str:
    """A compact view of the board for the prompt -- cheaper and clearer than raw JSON."""
    lines = [f'TITLE: {board.data.get("title", "")}',
             f'STYLE BIBLE: {board.data.get("style_bible", "")}']
    for beat in board.ordered_beats():
        state = board.state_of(beat)
        lines.append(
            f'BEAT {beat["n"]} [{state}, {board.seconds_for(beat):.0f}s, '
            f'first frame from {board.source_for(beat)}]\n'
            f'  scene: {beat.get("scene", "")}\n'
            f'  action: {beat.get("action", "")}'
        )
    if board.data.get("caption"):
        lines.append(f'CAPTION: {board.data["caption"]}')
    return "\n".join(lines)


def transcript(board: board_mod.Board, limit: int = 12) -> str:
    turns = board.data.get("chat", [])[-limit:]
    if not turns:
        return ""
    rendered = "\n".join(f'{t["role"].upper()}: {t["text"]}' for t in turns)
    return f"\nCONVERSATION SO FAR:\n{rendered}\n"


def turn(board: board_mod.Board, message: str, *, selection: list[int] | None = None) -> dict:
    """Run one conversational turn and apply whatever it asks for."""
    focus = ""
    if selection:
        focus = (
            f"\nThe user currently has beat(s) {', '.join(map(str, selection))} selected. "
            "Unqualified references like 'this one' or 'make it slower' mean those beats.\n"
        )
    prompt = (
        f"{SYSTEM}\n\nCURRENT BOARD:\n{board_digest(board)}\n"
        f"{transcript(board)}{focus}\nUSER: {message}"
    )
    reply = planner.agy(
        prompt, model=config.PLANNER_MODEL, schema=CHAT_SCHEMA, cwd=board.workdir
    )
    if not isinstance(reply, dict):
        raise RuntimeError(f"agy returned unstructured output: {str(reply)[:400]}")

    applied = apply_ops(board, reply.get("ops") or [])
    chat = board.data.setdefault("chat", [])
    chat.append({"role": "user", "text": message, "selection": selection or []})
    chat.append({"role": "agy", "text": reply.get("reply", ""), "ops": applied})
    board.save()
    return {"reply": reply.get("reply", ""), "ops": applied}


def apply_ops(board: board_mod.Board, ops: list[dict]) -> list[dict]:
    """Apply the agent's edits, skipping anything malformed rather than failing the turn.

    A partly-understood instruction that changes three of four beats is more useful than
    an exception, and the canvas shows exactly what landed.
    """
    applied: list[dict] = []
    for op in ops:
        try:
            summary = apply_one(board, op)
        except (KeyError, ValueError, TypeError) as error:
            summary = f"skipped {op.get('op')}: {error}"
        if summary:
            applied.append({"op": op.get("op"), "n": op.get("n"), "summary": summary})
    board.renumber()
    return applied


def apply_one(board: board_mod.Board, op: dict) -> str | None:
    kind = op.get("op")

    if kind == "set_script":
        changed = [key for key in ("title", "style_bible") if op.get(key)]
        for key in changed:
            board.data[key] = op[key]
        return f"script: {', '.join(changed)}" if changed else None

    if kind == "set_beat":
        beat = board.beat(int(op["n"]))
        changed = []
        for key in ("scene", "action", "asset_prompt"):
            if op.get(key):
                beat[key] = op[key]
                changed.append(key)
        if op.get("seconds"):
            beat["seconds"] = config.snap_seconds(op["seconds"])
            changed.append("seconds")
        return f'beat {op["n"]}: {", ".join(changed)}' if changed else None

    if kind == "add_beat":
        requested = int(op.get("n") or len(board.beats) + 1)
        position = max(1, min(requested, len(board.beats) + 1))

        # Move media before changing the numbers. Descending order keeps beat 2 -> 3 from
        # overwriting the original beat 3 when inserting into the middle of a prepared reel.
        for existing in sorted(board.beats, key=lambda beat: beat["n"], reverse=True):
            old = existing["n"]
            if old < position:
                continue
            for maker in (board.asset_path, board.frame_path, board.video_path):
                source = maker(old)
                if source.exists():
                    source.replace(maker(old + 1))
            existing["n"] = old + 1
        board.beats.append({
            "n": position,
            "scene": op.get("scene", ""),
            "action": op.get("action", ""),
            "asset_prompt": op.get("asset_prompt", ""),
            # A new first scene cannot continue from anything. Every other insertion joins
            # the existing linear handoff unless the caller explicitly asks for a cut.
            "source": (
                board_mod.SOURCE_ASSET if position == 1
                else op.get("source") or board_mod.SOURCE_CHAIN
            ),
        })
        reset_sequence_layout(board)
        return f"added beat {position}"

    if kind == "remove_beat":
        n = int(op["n"])
        board.data["beats"] = [b for b in board.beats if b["n"] != n]
        for path in (board.asset_path(n), board.frame_path(n), board.video_path(n)):
            path.unlink(missing_ok=True)
        reset_sequence_layout(board)
        return f"removed beat {n}"

    if kind == "set_source":
        source = op.get("source")
        if source not in (board_mod.SOURCE_ASSET, board_mod.SOURCE_CHAIN):
            raise ValueError(f"bad source {source!r}")
        board.beat(int(op["n"]))["source"] = source
        return f'beat {op["n"]}: first frame from {source}'

    if kind == "set_caption":
        board.data["caption"] = op.get("caption", "")
        return "caption"

    if kind == "set_reel":
        changed = []
        if op.get("seconds"):
            board.data["seconds"] = config.snap_seconds(op["seconds"])
            changed.append("seconds")
        if op.get("steps"):
            board.data["steps"] = int(op["steps"])
            changed.append("steps")
        return f'reel: {", ".join(changed)}' if changed else None

    raise ValueError(f"unknown op {kind!r}")


def reset_sequence_layout(board: board_mod.Board) -> None:
    """Reflow a structurally changed chain while preserving the script node's position."""
    canvas = board.data.setdefault("canvas", {})
    nodes = canvas.get("nodes") or {}
    canvas["nodes"] = {"script": nodes["script"]} if "script" in nodes else {}


# ## Creating a board from a concept
#
# The first turn is different: there is no board yet, so this uses the planner's schema
# directly and seeds the conversation with the result.


def create(concept: str, beats: int, seconds: float) -> board_mod.Board:
    slug = board_mod.slugify(concept)
    workdir = board_mod.reels_dir() / slug
    workdir.mkdir(parents=True, exist_ok=True)
    plan = planner.plan(concept, beats, seconds, workdir)

    plan["seconds"] = seconds
    plan["steps"] = config.DEFAULT_STEPS
    plan["seed"] = 1101
    plan["concept"] = concept
    # Beat 1 opens on its own still; the rest inherit, which keeps a whole reel inside a
    # single image from the scarce quota. Flip any wire to "asset" for a hard cut.
    for index, beat in enumerate(plan.get("beats", [])):
        beat["source"] = board_mod.SOURCE_ASSET if index == 0 else board_mod.SOURCE_CHAIN
    plan["chat"] = [
        {"role": "user", "text": concept, "selection": []},
        {"role": "agy",
         "text": f'Wrote "{plan.get("title", slug)}" as {len(plan.get("beats", []))} beats. '
                 "Beat 1 needs a still; the rest continue from it.",
         "ops": [{"op": "set_script", "summary": "created the board"}]},
    ]
    return board_mod.Board.create(slug, plan)


def caption(board: board_mod.Board) -> str:
    """Write the Instagram caption. Free, and the last thing a Reels tool needs."""
    prompt = (
        "Write an Instagram caption for this Reel. One or two short sentences with at most "
        "one emoji, then 6 to 10 relevant hashtags on their own line. No quotes around it, "
        "no markdown, output the caption text only.\n\n"
        f"{board_digest(board)}"
    )
    text = str(planner.agy(prompt, model=config.PLANNER_MODEL, schema=None,
                           cwd=board.workdir)).strip()
    board.data["caption"] = text
    board.save()
    return text
