"""The conversation that drives the board.

The model runs locally through Ollama and it calls tools, so a turn is a real loop rather
than a single structured answer: it can edit a beat, read the board back to see what that
did, edit another, and ask the image server for the stills it now needs -- all inside one
turn, with every step visible in the job log.

That loop is what the Antigravity CLI could not do. `agy -p` was one non-interactive shot,
so every turn had to ask for a reply plus a batch of board operations and hope the batch was
internally consistent; there was no way for the model to look at the result of its own first
edit before making the second. Locally there is, and turns are unmetered, so it can.

Deliberately absent: any tool that spends money. The model can write, rewrite, reorder and
re-time every beat, and it can ask the local image server for stills, but rendering video on
a GPU stays a button a human presses.
"""

from __future__ import annotations

from typing import Callable

from . import board as board_mod
from . import config, papercut, planner, qwen, script, stills as stills_mod

# The board operations, by name. Each one is also a tool the model can call, and `apply_one`
# below is the single place any of them is carried out -- the HTTP layer applies the same ops
# for its own add/remove endpoints, so a change in behaviour cannot land in only one path.
OPS = [
    "set_script",    # title / style_bible
    "set_beat",      # scene / action / asset_prompt / seconds on beat n
    "add_beat",      # insert a beat at position n
    "remove_beat",   # delete beat n
    "set_source",    # n + source: "asset" | "chain" | "bridge" -- see SYSTEM below
    "set_caption",   # the Instagram caption on the reel node
    "set_reel",      # board-wide seconds / steps
]

# Tool descriptions are load-bearing, not documentation. Given a bare "Edit one beat" the
# model spent an entire turn reasoning about what a parameter called `action` wanted -- reading
# the field name as a verb, which is what it means in every other tool it has ever seen. Told
# what each field holds, in the same words the system prompt uses, it answers in one call.
TOOLS = [
    qwen.tool(
        "read_board",
        "Read the current board back: every beat with its state, length, join, scene and "
        "action. Call this when you need to see the effect of an edit you just made, or when "
        "the board may have changed since the digest you were given.",
        {},
    ),
    qwen.tool(
        "set_script",
        "Set the reel's title and/or its style bible. The style bible is the one paragraph "
        "that fixes what the characters and the set LOOK like, and it goes into every image "
        "and video prompt.",
        {
            "title": {"type": "string", "description": "short title for the reel"},
            "style_bible": {
                "type": "string",
                "description": "the whole replacement paragraph -- appearance only, never motion",
            },
        },
    ),
    qwen.tool(
        "set_beat",
        "Change one existing beat. Send only the fields you are changing; anything you leave "
        "out is kept as it is.",
        {
            "n": {"type": "integer", "description": "which beat, 1-based"},
            "scene": {
                "type": "string",
                "description": "replacement line for WHERE this beat happens and at what scale",
            },
            "action": {
                "type": "string",
                "description": "replacement line for what MOVES in this shot",
            },
            "asset_prompt": {
                "type": "string",
                "description": "replacement prompt for this beat's own still frame",
            },
            "seconds": {
                "type": "number", "enum": [5, 10],
                "description": "how long the beat runs; only 5 or 10 exist",
            },
        },
        ["n"],
    ),
    qwen.tool(
        "add_beat",
        "Insert a new beat. Everything from that position on shifts down, with its stills and "
        "clips. Use n = one past the last beat to append.",
        {
            "n": {"type": "integer", "description": "position to insert at, 1-based"},
            "scene": {"type": "string", "description": "where this beat happens and at what scale"},
            "action": {"type": "string", "description": "what MOVES in this shot"},
            "asset_prompt": {"type": "string", "description": "prompt for its own still, if it needs one"},
            "source": {
                "type": "string", "enum": list(board_mod.SOURCES),
                "description": "where its frames come from; omit to continue from the beat before",
            },
        },
        ["n"],
    ),
    qwen.tool(
        "remove_beat",
        "Delete a beat and everything rendered for it. The beats after it move up.",
        {"n": {"type": "integer", "description": "which beat, 1-based"}},
        ["n"],
    ),
    qwen.tool(
        "set_source",
        "Change where a beat's frames come from -- the single most important choice on the "
        "board, because it decides whether the beat is a cut or a continuation.",
        {
            "n": {"type": "integer", "description": "which beat, 1-based"},
            "source": {
                "type": "string", "enum": list(board_mod.SOURCES),
                "description": "asset = a hard cut onto its own still; chain = the same take "
                               "continuing; bridge = continues AND lands on its own still; "
                               "reference = conditioned on uploaded pictures, no keyframe",
            },
        },
        ["n", "source"],
    ),
    qwen.tool(
        "set_caption",
        "Replace the Instagram caption for the whole reel.",
        {"caption": {"type": "string", "description": "the caption text, hashtags included"}},
        ["caption"],
    ),
    qwen.tool(
        "set_reel",
        "Change a board-wide setting: the default beat length, or the sampler step count.",
        {
            "seconds": {"type": "number", "enum": [5, 10],
                        "description": "default length for beats that do not set their own"},
            "steps": {"type": "integer", "description": "sampler steps; 8 is the measured default"},
        },
    ),
    qwen.tool(
        "generate_stills",
        "Ask the local image server to render the opening stills for these beats, then look "
        "at what came back. Free and unmetered, but roughly 10-18 seconds per still, so ask "
        "for the beats that need one rather than for all of them. Only works on beats whose "
        "frames come from their own still ('asset' or 'bridge').",
        {
            "beats": {
                "type": "array", "items": {"type": "integer"},
                "description": "which beats to render stills for, 1-based",
            },
        },
        ["beats"],
    ),
]

SYSTEM = f"""You are the story editor for a paper-cutout stop-motion Instagram Reel studio.
You edit a board of beats. Each beat is ONE continuous shot from a locked-off camera.

Hard rules of the medium -- breaking these wastes the user's money:
- The camera never moves, pans, zooms or cuts inside a beat.
- Only one thing animates at a time. No new characters walk into frame.
- No dialogue, no on-screen text, no watermarks.
- The same character appears in every beat and must be described identically. The
  style_bible holds that description; reuse its exact wording in every asset_prompt.
- An `action` describes only what MOVES. Appearance belongs in the style_bible.
- A `scene` is one line: where this beat happens and at what scale. It is rendered too --
  the video prompt is the style_bible, then the scene, then the action -- so it must never
  contain movement, and beats that belong to one continuous shot must carry the same line.

Every beat is either 5 or 10 seconds. There is no other length -- anything else you ask for
will be snapped to the nearer of the two, so choose one of them deliberately. Use 5 for a
quick gesture and 10 for a beat that needs room to breathe.

The video model takes up to two still frames per beat -- one for where the clip starts and
one for where it ends -- so a beat's frames come from one of four places. This is the single
most important choice on the board, because it decides what the beat is:
- "asset": its own generated still as the FIRST frame. A hard cut to somewhere else -- new
  setting, new framing, new composition. The still is generated from the reel's locked
  character reference, so the cast stays identical across the cut; only the world around them
  changes. A still is rendered on this machine and costs no money, but it does cost about
  10-18 seconds, and it needs the local image server to be running.
- "chain": the previous beat's final frame as the FIRST frame, and no end frame. Not a new
  shot at all -- the same take continuing, same set, same camera, same lighting, with the
  movement carrying straight through. Needs no still at all.
- "bridge": both. It opens on the previous beat's final frame AND is given its own still as
  the frame it must ARRIVE at. The take carries on unbroken, and it also lands on a
  composition that was designed rather than one the model drifted into. Needs a still, same
  as a cut. Choose it when a continuous shot has to reach a specific state -- the lamp lit,
  the character back in position -- or when a long chain of continuations is drifting away
  from the style bible and needs pinning back to a still.

- "reference": no still frame at all. The model is instead shown up to
  {config.MAX_REF_IMAGES} pictures the user uploaded of the cast and the set, and composes
  the opening frame itself from the scene line. It runs on a different set of weights, so it
  cannot continue from anything and cannot land on anything -- but it is the only join that
  can be given several pictures. Never set it on a beat yourself unless the user asked for
  it: without uploaded pictures the beat cannot render at all, and you cannot upload them.

So choose "asset" when the story genuinely moves somewhere else, "chain" when the movement
should carry on unbroken and where it ends does not matter, and "bridge" when it should carry
on unbroken TO somewhere specific. A chained or bridged beat's `action` must read as the
continuation of the beat before it -- it starts from the pose that beat ended in and takes the
movement onward. Writing it as a fresh instruction ("the fox sits down in the meadow") makes
the model reset the puppet and start over, which is visible as a jolt at the join.

On a "bridge" beat the `asset_prompt` describes the LAST frame, not the first: the composition
the clip has to end on. Everything else about writing it is the same, and it must still match
the style bible word for word.

Use the tools to carry out what the user asked for, and nothing else. Do not call a tool that
would not change anything, and do not restate the board back at the user. Rendering video is
not something you can do: it costs real money and only the user starts it. When you are done,
answer in one or two plain sentences -- no markdown, no lists.

The board you are shown is the truth. Read every answer about it straight off that list --
count the BEAT lines, quote the text -- rather than working it out from the conversation above,
which describes edits and not the result of them. If you have made a change and need to see
where it left things, call read_board; do not reason about it."""


def board_digest(board: board_mod.Board) -> str:
    """A compact view of the board for the prompt -- cheaper and clearer than raw JSON.

    The two "waiting on" lines at the end are spelled out rather than left to be worked out
    from the joins, because asked which beats need a still the model reasoned it out from the
    join names and got it wrong in both directions -- naming a chained beat, which needs
    nothing, and a reference beat, which needs pictures instead. Both lists are already derived
    in `board.py`; quoting them costs a few tokens and removes the inference entirely.
    """
    lines = [f'TITLE: {board.data.get("title", "")}',
             f'STYLE BIBLE: {board.data.get("style_bible", "")}']
    # Once, not once per beat: every call to states() hashes every conditioning image on the
    # board, and this loop used to ask for one beat at a time.
    states = board.states()
    for beat in board.ordered_beats():
        source = board.source_for(beat)
        # A reference beat's pictures are the whole of its conditioning, and only the user can
        # add them, so the count is the part of its state worth spending tokens on.
        if board_mod.uses_refs(source):
            source += f" ({len(board.ref_paths(beat['n']))} pictures)"
        lines.append(
            f'BEAT {beat["n"]} [{states[beat["n"]]}, {board.seconds_for(beat):.0f}s, '
            f'frames from {source}]\n'
            f'  scene: {beat.get("scene", "")}\n'
            f'  action: {beat.get("action", "")}'
        )
    if board.data.get("caption"):
        lines.append(f'CAPTION: {board.data["caption"]}')

    stills = [b["n"] for b in board.ordered_beats()
              if board_mod.uses_asset(board.source_for(b))
              and not board.asset_path(b["n"]).exists()]
    pictures = [b["n"] for b in board.ordered_beats()
                if board_mod.uses_refs(board.source_for(b)) and not board.ref_paths(b["n"])]
    lines.append(
        "BEATS WAITING ON A GENERATED STILL: "
        + (", ".join(map(str, stills)) if stills else "none")
        + " (this is the complete list -- every other beat either has its still already or "
        "does not take one)"
    )
    lines.append(
        "BEATS WAITING ON UPLOADED REFERENCE PICTURES: "
        + (", ".join(map(str, pictures)) if pictures else "none")
        + " (you cannot supply these; only the user can)"
    )
    return "\n".join(lines)


def transcript(board: board_mod.Board, limit: int = 12) -> str:
    """The conversation so far, labelled as history rather than as fact.

    The label is not decoration. An earlier reply of the model's own is the most authoritative
    thing in the prompt as far as the model is concerned, so a turn that answered "this reel
    has five beats" when it had four went on insisting on five for the rest of the session --
    reasoning from what it had said instead of reading the board it was given. Saying what this
    section is, and putting the board after it rather than before it, is what stops that.
    """
    turns = board.data.get("chat", [])[-limit:]
    if not turns:
        return ""
    rendered = "\n".join(f'{t["role"].upper()}: {t["text"]}' for t in turns)
    return (
        "CONVERSATION SO FAR -- history only. These are things that were said at the time, and "
        "some of them are now out of date. Never answer a question about the board from this "
        "section.\n"
        f"{rendered}\n\n"
    )


def turn(board: board_mod.Board, message: str, *, selection: list[int] | None = None,
         log: Callable[[str], None] = print,
         progress: Callable[[int, float], None] | None = None,
         announce: Callable[[], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> dict:
    """Run one conversational turn, tools and all, and apply whatever it asks for.

    The conversation is rebuilt from the board document every turn rather than held in
    memory, which is what lets it survive a page reload or a restart of the studio server.
    Only the tool round-trips inside this call are transient; the board and the transcript
    are the record.
    """
    focus = ""
    if selection:
        focus = (
            f"\nThe user currently has beat(s) {', '.join(map(str, selection))} selected. "
            "Unqualified references like 'this one' or 'make it slower' mean those beats.\n"
        )
    # Order matters: history, then the board, then the question. The board used to come first,
    # which put a stale line of the model's own transcript nearer to the question than the
    # truth was -- and the model answered from the nearer one.
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": (
            f"{transcript(board)}"
            f"THE BOARD AS IT IS RIGHT NOW -- this is the only current state, and every answer "
            f"about the reel comes from here:\n{board_digest(board)}\n"
            f"{focus}\nUSER: {message}"
        )},
    ]

    applied: list[dict] = []
    reply = ""
    for round_number in range(1, config.AGENT_MAX_ROUNDS + 1):
        assistant = qwen.chat(messages, tools=TOOLS)
        spoken = str(assistant.get("content") or "").strip()
        if spoken:
            reply = spoken
        calls = qwen.calls_of(assistant)
        if not calls:
            break
        results: list[tuple[str, str]] = []
        for name, arguments in calls:
            outcome, summaries = _run_tool(
                board, name, arguments,
                log=log, progress=progress, announce=announce, cancelled=cancelled,
            )
            results.append((name, outcome))
            applied += summaries
        messages += qwen.answered(assistant, results)
        if round_number == config.AGENT_MAX_ROUNDS:
            # Not an error: whatever landed is real and the board shows it. But the turn is
            # over, and saying so beats a reply that reads as though more was coming.
            log(f"[qwen] stopped after {config.AGENT_MAX_ROUNDS} tool rounds")

    if not reply:
        # A model that spent its whole turn on tools and then said nothing still owes the user
        # a sentence, and the ops are the honest one.
        reply = ("Done: " + "; ".join(op["summary"] for op in applied) if applied
                 else "Nothing to change.")
    chat = board.data.setdefault("chat", [])
    chat.append({"role": "user", "text": message, "selection": selection or []})
    chat.append({"role": "qwen", "text": reply, "ops": applied})
    board.save()
    return {"reply": reply, "ops": applied}


def _run_tool(board: board_mod.Board, name: str, arguments: dict, *,
              log: Callable[[str], None],
              progress: Callable[[int, float], None] | None,
              announce: Callable[[], None] | None,
              cancelled: Callable[[], bool] | None) -> tuple[str, list[dict]]:
    """Carry out one tool call. Returns (what the model is told, what the user is shown).

    Every failure comes back as text for the model rather than as an exception, because the
    model can recover from being told "beat 7 is not on this board" and cannot recover from
    the turn ending. The user-facing list is separate and only records what actually changed.
    """
    if name == "read_board":
        return board_digest(board), []

    if name == "generate_stills":
        return _generate_stills(
            board, arguments, log=log, progress=progress,
            announce=announce, cancelled=cancelled,
        ), []

    if name not in OPS:
        return f"there is no tool called {name}", []

    summaries = apply_ops(board, [{"op": name, **arguments}])
    if not summaries:
        return "that changed nothing", []
    board.save()
    if announce is not None:
        announce()
    for summary in summaries:
        log(f"[qwen] {summary['summary']}")
    return "; ".join(summary["summary"] for summary in summaries), summaries


def _generate_stills(board: board_mod.Board, arguments: dict, *,
                     log: Callable[[str], None],
                     progress: Callable[[int, float], None] | None,
                     announce: Callable[[], None] | None,
                     cancelled: Callable[[], bool] | None) -> str:
    """The one tool that reaches outside this process, into the image server next door.

    Guarded by exactly the same rules as the canvas button, from the same place: a board whose
    stills are the user's own work is off limits, and a reference beat must never be handed a
    still -- doing so would drop its pictures and silently turn it into a cut.
    """
    requested = arguments.get("beats")
    beats = [int(n) for n in requested if isinstance(n, (int, float, str)) and str(n).isdigit()] \
        if isinstance(requested, list) else []
    if not beats:
        return "say which beats, as a list of beat numbers"
    try:
        beats = stills_mod.wanted(board, beats)
        stills_mod.claim(board, beats)
        if announce is not None:
            announce()
        made = stills_mod.generate(board, beats, log=log, progress=progress,
                                   announce=announce, cancelled=cancelled)
    except (stills_mod.StillsError, papercut.PapercutError, qwen.OllamaError) as refused:
        # All three are ordinary states, not faults: a board that supplies its own stills, an
        # image server that is not running, a model that went away mid-turn. The model is told
        # in the same words the user would be, and gets to say so in its reply.
        log(f"[qwen] stills: {refused}")
        return str(refused)
    except Exception as failed:  # noqa: BLE001 -- the image server is a separate process
        log(f"[qwen] stills failed: {failed}")
        return f"the image server could not do it: {failed}"
    missing = [n for n in beats if n not in made]
    return (f"stills rendered for beats {made}"
            + (f"; beats {missing} did not land" if missing else ""))


def apply_ops(board: board_mod.Board, ops: list[dict]) -> list[dict]:
    """Apply board edits, skipping anything malformed rather than failing the caller.

    A partly-understood instruction that changes three of four beats is more useful than an
    exception, and the canvas shows exactly what landed.
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
            for maker in board.media_makers():
                source = maker(old)
                if source.exists():
                    source.replace(maker(old + 1))
            existing["n"] = old + 1
        requested_source = op.get("source")
        board.beats.append({
            "n": position,
            "scene": op.get("scene", ""),
            "action": op.get("action", ""),
            "asset_prompt": op.get("asset_prompt", ""),
            # A new first scene cannot continue from anything. Every other insertion joins
            # the existing linear handoff unless the caller explicitly asks for another join.
            "source": (
                # A reference beat takes nothing from upstream, so it is as valid in first
                # position as anywhere else -- the "new first scene" rule is about the two
                # continuations, which have nothing to continue from there.
                board_mod.SOURCE_REFERENCE
                if requested_source == board_mod.SOURCE_REFERENCE
                else board_mod.SOURCE_ASSET if position == 1
                else requested_source if requested_source in board_mod.SOURCES
                else board_mod.SOURCE_CHAIN
            ),
        })
        reset_sequence_layout(board)
        return f"added beat {position}"

    if kind == "remove_beat":
        n = int(op["n"])
        board.data["beats"] = [b for b in board.beats if b["n"] != n]
        for maker in board.media_makers():
            maker(n).unlink(missing_ok=True)
        reset_sequence_layout(board)
        return f"removed beat {n}"

    if kind == "set_source":
        source = op.get("source")
        if source not in board_mod.SOURCES:
            raise ValueError(f"bad source {source!r}")
        board.beat(int(op["n"]))["source"] = source
        return f'beat {op["n"]}: frames from {source}'

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
# The first turn is different: there is no board yet, so this uses the planner directly and
# seeds the conversation with the result.


def create(concept: str, beats: int, seconds: float, *,
           log: Callable[[str], None] = print) -> board_mod.Board:
    """Plan a reel and put it on the canvas.

    The plan goes through `script.normalise` -- the same function an imported script takes --
    rather than being written into a board document here. Both paths now produce a script
    against the same brief, so both should become a board the same way: beat numbers compacted
    to 1..N, lengths snapped to the two the hardware offers, beat 1 forced onto its own still
    whatever the model said, and a beat with no action refused rather than saved as a node
    that can never render.

    The joins are the model's to choose, which they were not before. They used to be
    overwritten with "beat 1 is a cut, everything after it chains", because a cut cost one
    image from a five-per-five-hours quota and a chained reel needed exactly one image no
    matter how long it was. Stills are local and free now, so the shape of the film can be
    decided by the shape of the story -- which is what section 2 of the brief is about.
    """
    plan = planner.plan(concept, beats, seconds, log=log)
    plan.setdefault("concept", concept)
    # The board-wide default, which every beat inherits: the planner is told the length is
    # fixed and not its to choose, so nothing per-beat should be overriding this.
    plan["seconds"] = seconds
    document = script.normalise(plan)
    document["steps"] = config.DEFAULT_STEPS
    document["seed"] = 1101

    cuts = [b["n"] for b in document["beats"] if b["source"] == board_mod.SOURCE_ASSET]
    stills = [b["n"] for b in document["beats"] if board_mod.uses_asset(b["source"])]
    # Said once, here, rather than discovered later as a node with a button and no prompt.
    for note in script.notes(document):
        log(f"[plan] {note}")
    document["chat"] = [
        {"role": "user", "text": concept, "selection": []},
        {"role": "qwen",
         "text": (f'Wrote "{document["title"]}" as {len(document["beats"])} beats in '
                  f'{len(cuts)} shot{"" if len(cuts) == 1 else "s"}. '
                  + (f'Beats {", ".join(map(str, stills))} need a still of their own; the '
                     "others continue from the beat before them."
                     if len(stills) < len(document["beats"]) else
                     "Every beat opens on its own still, so nothing is chained.")),
         "ops": [{"op": "set_script", "summary": "created the board"}]},
    ]
    # slugify, not script.free_slug: re-planning the same concept has always landed back on
    # the same reel directory, and that is also the slug the queued job was given.
    return board_mod.Board.create(board_mod.slugify(concept), document)


def caption(board: board_mod.Board) -> str:
    """Write the Instagram caption. Free, and the last thing a Reels tool needs."""
    prompt = (
        "Write an Instagram caption for this Reel. One or two short sentences with at most "
        "one emoji, then 6 to 10 relevant hashtags on their own line. No quotes around it, "
        "no markdown, output the caption text only.\n\n"
        f"{board_digest(board)}"
    )
    text = qwen.text([{"role": "user", "content": prompt}], temperature=0.7).strip()
    board.data["caption"] = text
    board.save()
    return text
