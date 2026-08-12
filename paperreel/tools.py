"""What the three agents can do, as tools over the modules that already do it.

Every tool here is a thin call into `agent.py`, `board.py`, `coherence.py`, `develop.py`,
`panels.py`, `pictures.py`, `planner.py`, `staging.py` or `stills.py`. That is the whole design
and it is worth stating plainly: the measured prompt scaffolding, the fingerprint rules, the
still review, the join guards and the picture budget all keep exactly one copy, in the module
that was written around them. An agent is a different way to *reach* those, never a second
version.

Where a description already exists, it is borrowed rather than rewritten. `agent.TOOLS` carries
descriptions that were tuned against a live model -- given a bare "Edit one beat" it spent a
whole turn reasoning about what a parameter called `action` wanted -- and a second wording of
the same tool is how two loops quietly start asking for different things.

**Nothing here can spend the GPU.** That is structural rather than a rule anyone has to
remember: this module does not import `render`, `pipeline`, `comfy` or `modal`, there is no
tool that could reach them, and `runtime.build` refuses a skill naming a tool that does not
exist. The invariant is checkable in one command --

    grep -n 'render\\|pipeline\\|comfy\\|modal' paperreel/tools.py paperreel/runtime.py \\
        paperreel/crew.py

-- and should return nothing outside prose. Same rule `agent.py` states for its own toolbox:
words are cents and stills are cents, but a reel is dollars, and dollars stay a button a human
presses.

The one thing that IS metered here is `generate_stills`, and it carries the only guard this
layer adds rather than inherits: a per-run budget in `Context.state`, because `max_rounds`
bounds turns and not money.
"""

from __future__ import annotations

import copy

from . import agent as agent_mod
from . import board as board_mod
from . import coherence, config, critique, develop, llm as llm_mod, panels, pictures
from . import planner, skills, staging, stills
from .runtime import Context, Outcome, Tool, ToolRefused


def toolbox(llm: llm_mod.LLM | None = None) -> dict[str, Tool]:
    """Every tool, by name, with its declaration built in this provider's dialect.

    Built per call rather than cached at import because the declaration shape belongs to the
    provider (`llm.tool`), and building a few dozen dicts is nothing next to one model turn.
    """
    speaker = llm or llm_mod.provider()
    found: dict[str, Tool] = {}
    for make in (_shared, _script_tools, _storyboard_tools, _asset_tools,
                 _style_tools, _blocking_tools, _coherence_tools, _check_tools):
        for tool in make(speaker):
            found[tool.spec["name"]] = tool
    return found


def director_toolbox(llm: llm_mod.LLM | None = None) -> dict[str, Tool]:
    """The director's toolbox: board edits, still generation, and delegation to specialists."""
    speaker = llm or llm_mod.provider()
    found: dict[str, Tool] = {}
    for make in (_shared, _director_board, _director_delegate):
        for tool in make(speaker):
            found[tool.spec["name"]] = tool
    return found


# ## Plumbing


def borrowed(llm: llm_mod.LLM, name: str, *, keep: set[str] | None = None,
             called: str | None = None, description: str | None = None) -> dict:
    """One of `agent.TOOLS`, re-declared here so its description keeps a single copy.

    Re-declared rather than handed over as-is because a declaration is written in the
    provider's dialect and `agent.TOOLS` is built at import through Gemini's. Passing the same
    name, description and properties back through `llm.tool` is idempotent for Gemini and
    correct for anything else.

    `keep` narrows the parameters -- the trick `develop.write_tool` uses on `PLAN_SCHEMA`,
    derived rather than retyped, so a field added upstream cannot go missing here. `called`
    renames the narrowed version, which is needed rather than tidy: the toolbox is one flat
    namespace, so a full `set_beat` and a one-field one cannot both answer to that name.
    """
    for spec in agent_mod.TOOLS:
        if spec["name"] != name:
            continue
        parameters = spec.get("parameters") or {}
        properties = copy.deepcopy(parameters.get("properties") or {})
        required = list(parameters.get("required") or [])
        if keep is not None:
            properties = {key: value for key, value in properties.items() if key in keep}
            required = [key for key in required if key in keep]
        return llm.tool(called or name, description or spec["description"],
                        properties, required)
    raise KeyError(f"agent.TOOLS has no tool called {name!r}")


def board_op(name: str) -> callable:
    """A board operation, carried out through the one function that carries them all out.

    `agent.apply_ops` is the single place any of the seven ops happens -- the HTTP layer's own
    add/remove routes go through it too -- so a change in what an op means cannot land in only
    one path. The save/announce/log block is `agent._run_tool`'s, kept identical on purpose.
    """

    def run(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        summaries = agent_mod.apply_ops(board, [{"op": name, **arguments}])
        if not summaries:
            return "that changed nothing", []
        board.save()
        context.hooks.changed()
        for summary in summaries:
            context.hooks.say(f"[{name}] {summary['summary']}")
        return "; ".join(summary["summary"] for summary in summaries), summaries

    return run


def _beat_number(board: board_mod.Board, arguments: dict, key: str = "n") -> int:
    """One beat number, validated against the board rather than trusted."""
    raw = arguments.get(key)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ToolRefused(f"{key} has to be a beat number, 1-based") from None
    if board.beat(n) is None:
        raise ToolRefused(f"there is no beat {n} on this board; it has {len(board.beats)}")
    return n


def _beat_list(board: board_mod.Board, arguments: dict, key: str = "beats") -> list[int] | None:
    """A beat list, or None for "all of them" -- which is what both panel calls default to."""
    raw = arguments.get(key)
    if raw in (None, ""):
        return None
    if not isinstance(raw, list):
        raise ToolRefused(f"{key} has to be a list of beat numbers")
    wanted = []
    for value in raw:
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise ToolRefused(f"{value!r} is not a beat number") from None
        if board.beat(n) is None:
            raise ToolRefused(f"there is no beat {n} on this board")
        wanted.append(n)
    return wanted or None


def _text(arguments: dict, key: str, what: str) -> str:
    body = str(arguments.get(key) or "").strip()
    if not body:
        raise ToolRefused(f"say {what} -- {key} was empty")
    return body


def _design(board: board_mod.Board, arguments: dict, key: str = "id") -> dict:
    """One staging entry, by the id the model quoted, with the miss said in its own words.

    `Board.stage_entry` raises `KeyError`, which the dispatcher would report as a crash. A
    design id the model got wrong is an ordinary state -- it is reading ids out of a digest --
    so it comes back as a refusal naming the ones that do exist.
    """
    entry_id = str(arguments.get(key) or "").strip()
    try:
        return board.stage_entry(entry_id)
    except KeyError:
        known = ", ".join(str(entry.get("id")) for entry in board.staging) or "none"
        raise ToolRefused(
            f"there is no design {entry_id!r} on this reel. Designs: {known}."
        ) from None


# ## Shared


def _shared(llm: llm_mod.LLM) -> list[Tool]:
    def read_board(context: Context, _arguments: dict) -> Outcome:
        if context.board is None:
            return "there is no board yet -- nothing has been written", []
        return agent_mod.board_digest(context.board), []

    def preview_video_prompt(context: Context, arguments: dict) -> Outcome:
        """The exact H3 prompt this beat would send, plus its wired reference roles.

        Read-only: no Gemini, no Papercut, no Comfy, no Modal. Continuity (and the director)
        use it to audit seams against the scaffold `config.build_prompt` actually composes,
        rather than guessing from the board digest alone.
        """
        board = context.need_board()
        n = _beat_number(board, arguments)
        beat = board.beat(n)
        source = board.source_for(beat)
        pictures = board.pictures_for(n)
        carry = board.carries_motion(beat)
        opens_on = board.opens_on_still(beat)
        prompt = config.build_prompt(
            beat.get("action", ""),
            scene=beat.get("scene", ""),
            mute=bool(board.data.get("mute")),
            identity=board.identity(),
            continues=board_mod.chains(source),
            lands=source == board_mod.SOURCE_BRIDGE,
            refs=len(pictures),
            ref_notes=[note for _, note in pictures] or None,
            opens_on=opens_on,
            staging=board.staging_text(n, pictures),
            blocking=beat.get("blocking", ""),
            medium_key=board.medium(),
            ref_videos=1 if carry else 0,
            mentions=board.mentions(n, pictures),
        )
        lines = [
            f"beat {n} join={source}"
            + (f", carrying upstream motion as <Video 1>" if carry else "")
            + (f", opens on its own still as <Picture 1>" if opens_on else ""),
            "references:",
        ]
        if pictures:
            for index, (path, role) in enumerate(pictures, start=1):
                on_disk = "on disk" if path.is_file() else "MISSING"
                lines.append(f"  <Picture {index}> ({on_disk}): {role}")
        else:
            lines.append("  (none -- keyframe path, not ref2va)")
        if carry:
            lines.append("  <Video 1>: previous clip's tail")
        lines.append("prompt:")
        lines.append(prompt)
        return "\n".join(lines), []

    return [
        Tool(spec=borrowed(llm, "read_board"), run=read_board),
        Tool(spec=llm.tool(
            "preview_video_prompt",
            "Read the exact MiniMax-H3 video prompt one beat would send, plus the "
            "<Picture N> roles wired into it. Read-only: changes nothing and spends no "
            "render. Use before fixing a chain or bridge seam.",
            {
                "n": {"type": "integer", "description": "which beat, 1-based"},
            },
            ["n"],
        ), run=preview_video_prompt),
    ]


# ## The script writer


def _script_tools(llm: llm_mod.LLM) -> list[Tool]:
    def write_script(context: Context, arguments: dict) -> Outcome:
        """The interview's outcome: a whole script, self-checked, onto this board.

        `develop.reviewed` and `develop.adopt` do the work, which means the second pass over
        the brief's section 11 and the merge-rather-than-create rule both keep one copy. The
        `developable` guard is the money one: a board with any render record refuses the
        rewrite, because a new script would orphan clips somebody paid for.
        """
        board = context.need_board()
        develop.developable(board)
        concept = str(board.data.get("concept") or board.data.get("title") or "")
        draft = develop.reviewed(dict(arguments), concept, log=context.hooks.log)
        develop.adopt(board, draft)
        context.hooks.changed()
        written = len(board.beats)
        return f"the script is on the board: {written} beats", [
            {"op": "write_script", "summary": f"wrote a {written}-beat script"}]

    def plan_script(context: Context, arguments: dict) -> Outcome:
        """The one-shot path, onto the board that already exists.

        `agent.create` is deliberately NOT what this calls: it mints a new directory, which
        would move the run out from under a board the director is already looking at.
        `planner.plan` writes the same script and `develop.adopt` merges it, which is the same
        split `develop.py` already makes for the conversational path.
        """
        board = context.need_board()
        develop.developable(board)
        concept = str(arguments.get("concept")
                      or board.data.get("concept") or board.data.get("title") or "")
        if not concept.strip():
            raise ToolRefused("say what the film is about")
        beats = int(arguments.get("beats") or 4)
        seconds = float(arguments.get("seconds") or config.BEAT_LENGTHS[-1])
        draft = planner.plan(concept, beats, seconds, log=context.hooks.log)
        develop.adopt(board, draft)
        context.hooks.changed()
        return (f"planned {len(board.beats)} beats from the brief",
                [{"op": "plan_script", "summary": f"planned {len(board.beats)} beats"}])

    def revise_line(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        n = _beat_number(board, arguments)
        field = str(arguments.get("field") or "").strip()
        if field not in agent_mod.REVISE_FIELDS:
            raise ToolRefused(
                f"field has to be one of {', '.join(sorted(agent_mod.REVISE_FIELDS))}")
        note = _text(arguments, "note", "what should change about that line")
        done = agent_mod.revise(board, n, field, note, log=context.hooks.log)
        context.hooks.changed()
        return (f"beat {n} {field} is now: {done['text']}",
                [{"op": "revise_line", "n": n, "summary": f"rewrote beat {n} {field}"}])

    def write_caption(context: Context, _arguments: dict) -> Outcome:
        board = context.need_board()
        written = agent_mod.caption(board)
        context.hooks.changed()
        return written, [{"op": "set_caption", "summary": "wrote the caption"}]

    return [
        # `develop.write_tool()` verbatim: the schema it derives from `planner.PLAN_SCHEMA`
        # carries the per-beat `seconds` field and the reason it is a number with its two legal
        # values in the description rather than an enum. Re-deriving it here would be the copy
        # that gets forgotten.
        Tool(spec=develop.write_tool(), run=write_script),
        Tool(spec=llm.tool(
            "plan_script",
            "Write the whole script in one pass from the authoring brief, instead of "
            "interviewing the director for it. Use this when the director has already said "
            "how many beats and how long, and does not want to be asked.",
            {
                "concept": {"type": "string",
                            "description": "what the film is about, in one sentence"},
                "beats": {"type": "integer", "description": "how many shots the reel has"},
                "seconds": {
                    "type": "number", "enum": list(config.BEAT_LENGTHS),
                    "description": "how long each beat runs unless it says otherwise",
                },
            },
        ), run=plan_script),
        Tool(spec=borrowed(llm, "set_script"), run=board_op("set_script")),
        Tool(spec=borrowed(llm, "set_beat"), run=board_op("set_beat")),
        Tool(spec=borrowed(llm, "add_beat"), run=board_op("add_beat")),
        Tool(spec=borrowed(llm, "remove_beat"), run=board_op("remove_beat")),
        Tool(spec=borrowed(llm, "set_source"), run=board_op("set_source")),
        Tool(spec=borrowed(llm, "set_reel"), run=board_op("set_reel")),
        Tool(spec=llm.tool(
            "revise_line",
            "Rewrite ONE beat's scene or action line from a note about it, in a call of its "
            "own that is shown the beats either side. Use it instead of set_beat when the "
            "rewrite has to read as continuing from the beat before.",
            {
                "n": {"type": "integer", "description": "which beat, 1-based"},
                "field": {"type": "string", "enum": sorted(agent_mod.REVISE_FIELDS),
                          "description": "which line: the scene, or the action"},
                "note": {"type": "string",
                         "description": "what should change about that line, in the "
                                        "director's own words"},
            },
            ["n", "field", "note"],
        ), run=revise_line),
        Tool(spec=llm.tool(
            "write_caption",
            "Write the Instagram caption for the finished reel. Takes no arguments -- it reads "
            "the board.",
            {},
        ), run=write_caption),
    ]


# ## The storyboarder


def _storyboard_tools(llm: llm_mod.LLM) -> list[Tool]:
    def add_design(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        kind = str(arguments.get("kind") or "").strip()
        if kind not in config.STAGE_KINDS:
            raise ToolRefused(f"kind has to be one of {', '.join(config.STAGE_KINDS)}")
        name = _text(arguments, "name", "what this design is called")
        entry = board.add_stage(kind=kind, name=name,
                                note=str(arguments.get("note") or ""),
                                draw=str(arguments.get("draw") or ""))
        board.save()
        context.hooks.changed()
        return (f"{kind} {name!r} is now design {entry['id']}",
                [{"op": "add_design", "summary": f"added the {kind} {name!r}"}])

    def describe_design(context: Context, arguments: dict) -> Outcome:
        # The same field-by-field write `api.describe_staging` makes, and the same reason only
        # the keys present are touched: `name`/`note` reach every render that binds the design
        # and `draw` reaches only the sheet, so four controls edit four fields independently.
        board = context.need_board()
        entry = _design(board, arguments)
        changed = []
        for key in ("name", "note", "draw"):
            if arguments.get(key) is not None:
                entry[key] = " ".join(str(arguments[key] or "").split())
                changed.append(key)
        if not changed:
            return "that changed nothing", []
        if not board.stage_field(entry, "name"):
            raise ToolRefused("a design needs a name -- it is what the prompts call it")
        board.save()
        context.hooks.changed()
        return (f"design {entry['id']}: {', '.join(changed)} updated",
                [{"op": "describe_design", "summary": f"described {board.stage_name(entry)}"}])

    def draw_design(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        entry_id = str(_design(board, arguments)["id"])
        landed = staging.draw(board, entry_id,
                              prompt=str(arguments.get("prompt") or "") or None,
                              log=context.hooks.log, progress=context.hooks.progress,
                              announce=context.hooks.announce, cancelled=context.hooks.cancelled)
        context.hooks.changed()
        return (f"design {landed} is drawn",
                [{"op": "draw_design", "summary": f"drew design {landed}"}])

    def revise_design(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        entry_id = str(_design(board, arguments)["id"])
        note = _text(arguments, "note", "what should change about that sheet")
        done = staging.converse(board, entry_id, note,
                                log=context.hooks.log, progress=context.hooks.progress,
                                announce=context.hooks.announce,
                                cancelled=context.hooks.cancelled)
        context.hooks.changed()
        again = " and redrew it" if done.get("regenerated") else ""
        return (f"{done['reply']}{again}",
                [{"op": "revise_design", "summary": f"revised design {entry_id}{again}"}])

    def bind_designs(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        n = _beat_number(board, arguments)
        raw = arguments.get("ids")
        if not isinstance(raw, list):
            raise ToolRefused("ids has to be a list of design ids, even for one")
        bound = board.bind_stage(n, [str(value) for value in raw])
        board.save()
        context.hooks.changed()
        named = ", ".join(board.stage_name(board.stage_entry(entry_id))
                          for entry_id in bound) or "nothing"
        return (f"beat {n} is now shot with {named}",
                [{"op": "bind_designs", "n": n, "summary": f"beat {n}: {named}"}])

    def write_panels(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        written = panels.write(board, _beat_list(board, arguments),
                               log=context.hooks.log, announce=context.hooks.announce)
        context.hooks.changed()
        return (f"panel lines written for beats {written}",
                [{"op": "write_panels", "summary": f"wrote {len(written)} panel lines"}])

    def draw_panels(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        drawn = panels.draw_all(board, _beat_list(board, arguments),
                                log=context.hooks.log, progress=context.hooks.progress,
                                announce=context.hooks.announce,
                                cancelled=context.hooks.cancelled)
        context.hooks.changed()
        return (f"panels drawn for beats {drawn}",
                [{"op": "draw_panels", "summary": f"drew {len(drawn)} panels"}])

    kinds = ", ".join(config.STAGE_KINDS)
    return [
        Tool(spec=llm.tool(
            "add_design",
            f"Add one thing this reel has designed once and reuses -- a {kinds}. A design is "
            "reel-wide: bind it to the beats it appears in and every one of them is drawn from "
            "the same sheet instead of from the same paragraph read differently each time.",
            {
                "kind": {"type": "string", "enum": list(config.STAGE_KINDS),
                         "description": "what sort of thing this is"},
                "name": {"type": "string",
                         "description": "what to call it, e.g. 'Vera' or 'the clearing'"},
                "note": {"type": "string",
                         "description": "what it IS, for the shots that are told about it in "
                                        "words rather than shown the sheet"},
                "draw": {"type": "string",
                         "description": "the prompt its sheet is drawn from; leave it out and "
                                        "the note is used"},
            },
            ["kind", "name"],
        ), run=add_design),
        Tool(spec=llm.tool(
            "describe_design",
            "Change what a design says about itself. Send only the fields you are changing.",
            {
                "id": {"type": "string", "description": "the design's id"},
                "name": {"type": "string", "description": "replacement name"},
                "note": {"type": "string", "description": "replacement description"},
                "draw": {"type": "string", "description": "replacement drawing prompt"},
            },
            ["id"],
        ), run=describe_design),
        Tool(spec=llm.tool(
            "draw_design",
            "Draw a design's sheet. This is a metered image call and takes tens of seconds, so "
            "describe the thing properly first and draw it once.",
            {
                "id": {"type": "string", "description": "the design's id"},
                "prompt": {"type": "string",
                           "description": "what to draw, if it should differ from the design's "
                                          "own drawing prompt"},
            },
            ["id"],
        ), run=draw_design),
        Tool(spec=llm.tool(
            "revise_design",
            "Look at a design's sheet and say what should change about it. Ends in a redraw "
            "when the change needs one.",
            {
                "id": {"type": "string", "description": "the design's id"},
                "note": {"type": "string", "description": "what is wrong with it, or what to "
                                                          "change"},
            },
            ["id", "note"],
        ), run=revise_design),
        Tool(spec=llm.tool(
            "bind_designs",
            "Say which designs appear in one shot. This REPLACES what that beat was bound to, "
            "so send the complete list every time, and an empty list to unbind everything.",
            {
                "n": {"type": "integer", "description": "which beat, 1-based"},
                "ids": {"type": "array", "items": {"type": "string"},
                        "description": "every design in this shot, by id"},
            },
            ["n", "ids"],
        ), run=bind_designs),
        Tool(spec=llm.tool(
            "write_panels",
            "Write the shot grammar -- size, angle, camera move, where the subject sits, what "
            "the arrows point at -- for these beats, or for the whole reel when no beats are "
            "given. One call covers them all on purpose: shot sizes have to vary ACROSS the "
            "film, which a model shown one beat at a time cannot do.",
            {"beats": {"type": "array", "items": {"type": "integer"},
                       "description": "which beats, 1-based; omit for all of them"}},
        ), run=write_panels),
        Tool(spec=llm.tool(
            "draw_panels",
            "Draw the storyboard sketches for these beats, or for every beat that has a panel "
            "line and no sketch yet. Metered image calls, one per beat, so write the lines "
            "first and look at them before drawing.",
            {"beats": {"type": "array", "items": {"type": "integer"},
                       "description": "which beats, 1-based; omit for the ones still missing"}},
        ), run=draw_panels),
    ]


# ## The asset maker


def _asset_tools(llm: llm_mod.LLM) -> list[Tool]:
    def generate_stills(context: Context, arguments: dict) -> Outcome:
        """The one metered tool in the crew, and the only one with a budget of its own.

        The work is `agent.generate_stills`, which carries the `wanted`/`claim` guards -- a
        board whose stills are the director's own work is off limits, and a reference beat
        carrying motion must never be handed a still. What is added here is the count: a round
        cap bounds turns, and an agent rejecting and re-rendering its own stills is bounded by
        nothing else.
        """
        board = context.need_board()
        wanted = _beat_list(board, arguments) or []
        if not wanted:
            raise ToolRefused("say which beats, as a list of beat numbers")
        spent = int(context.state.get("stills_made") or 0)
        room = config.CREW_STILL_BUDGET - spent
        if room <= 0:
            return (f"this run has already rendered {spent} stills, which is its budget "
                    f"({config.CREW_STILL_BUDGET}). Say what is still wrong and stop."), []
        if len(wanted) > room:
            context.hooks.say(f"[stills] budget: rendering {room} of {len(wanted)} asked for")
            wanted = wanted[:room]
        outcome = agent_mod.generate_stills(
            board, {"beats": wanted}, log=context.hooks.log, progress=context.hooks.progress,
            announce=context.hooks.announce, cancelled=context.hooks.cancelled)
        context.state["stills_made"] = spent + len(wanted)
        context.hooks.changed()
        return outcome, [{"op": "generate_stills", "summary": f"rendered stills for {wanted}"}]

    def revise_still(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        n = _beat_number(board, arguments)
        note = _text(arguments, "note", "what is wrong with that still")
        done = stills.converse(board, n, note,
                               log=context.hooks.log, progress=context.hooks.progress,
                               announce=context.hooks.announce,
                               cancelled=context.hooks.cancelled)
        if done.get("regenerated"):
            context.state["stills_made"] = int(context.state.get("stills_made") or 0) + 1
        context.hooks.changed()
        again = " and drew it again" if done.get("regenerated") else ""
        return (f"{done['reply']}{again}",
                [{"op": "revise_still", "n": n, "summary": f"revised beat {n}'s still{again}"}])

    def draw_picture(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        n = _beat_number(board, arguments)
        prompt = _text(arguments, "prompt", "what the picture shows")
        index = arguments.get("index")
        slot = pictures.draw(board, n, int(index) if index else None, prompt=prompt,
                             log=context.hooks.log, progress=context.hooks.progress,
                             announce=context.hooks.announce, cancelled=context.hooks.cancelled)
        context.hooks.changed()
        return (f"beat {n} picture {slot} is drawn",
                [{"op": "draw_picture", "n": n, "summary": f"drew beat {n} picture {slot}"}])

    def revise_picture(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        n = _beat_number(board, arguments)
        try:
            index = int(arguments.get("index"))
        except (TypeError, ValueError):
            raise ToolRefused("index has to be the picture's number on that beat") from None
        note = _text(arguments, "note", "what should change about that picture")
        done = pictures.converse(board, n, index, note,
                                 log=context.hooks.log, progress=context.hooks.progress,
                                 announce=context.hooks.announce,
                                 cancelled=context.hooks.cancelled)
        context.hooks.changed()
        again = " and drew it again" if done.get("regenerated") else ""
        return (f"{done['reply']}{again}",
                [{"op": "revise_picture", "n": n,
                  "summary": f"revised beat {n} picture {index}{again}"}])

    return [
        Tool(spec=borrowed(llm, "generate_stills"), run=generate_stills),
        # Narrowed from `set_beat` rather than declared fresh, so the @ref-token warning in
        # that parameter's description -- the one a model rewriting a prompt actually reads --
        # keeps one copy. Renamed because the toolbox is one namespace and the script writer
        # holds the full `set_beat`; it still carries out the same op. There is deliberately no
        # `set_source` here: moving a join changes what renders and can strand a paid clip.
        Tool(spec=borrowed(llm, "set_beat", keep={"n", "asset_prompt"},
                           called="set_asset_prompt",
                           description="Replace one beat's still prompt. This is the only "
                                       "thing about a beat this agent changes -- the story "
                                       "lines and the join belong to other stages."),
             run=board_op("set_beat")),
        Tool(spec=llm.tool(
            "revise_still",
            "Look at a beat's still and say what should change about it. Ends in the still "
            "being drawn again when the change needs it, which costs another image.",
            {
                "n": {"type": "integer", "description": "which beat, 1-based"},
                "note": {"type": "string",
                         "description": "what is wrong with the still, or what to change"},
            },
            ["n", "note"],
        ), run=revise_still),
        Tool(spec=llm.tool(
            "draw_picture",
            "Draw one of a beat's own reference pictures -- a prop, a second character, "
            f"anything that shot needs held to a design. Up to {config.MAX_REF_IMAGES} "
            "pictures reach the clip; the still is drawn from at most "
            f"{config.MAX_STILL_REFS}. Prefer a reel-wide design over a per-beat picture when "
            "the thing appears in more than one shot.",
            {
                "n": {"type": "integer", "description": "which beat, 1-based"},
                "prompt": {"type": "string", "description": "what the picture shows"},
                "index": {"type": "integer",
                          "description": "an existing picture's number to redraw it; omit for "
                                         "a new one"},
            },
            ["n", "prompt"],
        ), run=draw_picture),
        Tool(spec=llm.tool(
            "revise_picture",
            "Look at one of a beat's reference pictures and say what should change about it.",
            {
                "n": {"type": "integer", "description": "which beat, 1-based"},
                "index": {"type": "integer", "description": "the picture's number on that beat"},
                "note": {"type": "string", "description": "what to change about it"},
            },
            ["n", "index", "note"],
        ), run=revise_picture),
    ]


# ## The style artists
#
# Two skills, one tool set. What differs between `style-paper-cutout` and `style-claymation` is
# the prompt, not what they can do -- both write the style bible, both set the medium, both
# describe and redraw the designs the film is made of. `crew.style_artist(board)` picks which
# one runs from `board.medium()`, so the skill and the render are asking for the same material
# by construction rather than by the director remembering to set both.


def _style_tools(llm: llm_mod.LLM) -> list[Tool]:
    def set_medium(context: Context, arguments: dict) -> Outcome:
        """Say what this film is physically made of.

        It reaches nine places in a render and the vision review's reject criteria, so it is
        validated here rather than stored as typed -- a typo would fall back to paper while the
        board said something else, which is the one failure that is invisible until the stills
        come back wrong.
        """
        board = context.need_board()
        wanted = str(arguments.get("medium") or "").strip()
        if wanted not in config.MEDIUMS:
            raise ToolRefused(f"medium has to be one of {', '.join(config.MEDIUMS)}")
        if wanted == board.medium():
            return f"this reel is already {config.medium(wanted).name}", []
        board.data["medium"] = wanted
        board.save()
        context.hooks.changed()
        return (f"this reel is now {config.medium(wanted).name}",
                [{"op": "set_medium", "summary": f"medium: {config.medium(wanted).name}"}])

    def read_medium(context: Context, _arguments: dict) -> Outcome:
        """What the render is actually asked for, in the words it is asked in.

        A style artist writing a bible without this is guessing at what the pipeline already
        says on every prompt -- and a bible that contradicts the suffix is two instructions
        fighting inside one request.
        """
        look = context.need_board().look()
        return ("\n".join([
            f"medium: {look.key} ({look.name})",
            f"every video prompt opens: {look.shot.strip()}",
            f"every video prompt ends: {look.craft.strip()}",
            f"every still is asked for as: {look.still}",
            f"every design sheet is asked for as: {look.sheet}",
            f"every set sheet is asked for as: {look.set}",
            f"the review rejects a still that is not: {look.judge}",
            f"the physics of this medium:\n{look.physics}",
        ]), [])

    return [
        Tool(spec=llm.tool(
            "set_medium",
            "Say what this film is physically made of. This is not a description -- it changes "
            "the words on every video prompt, every still, every design sheet and the review "
            "that rejects a still for being the wrong material. Set it before writing the "
            "style bible.",
            {"medium": {"type": "string", "enum": list(config.MEDIUMS),
                        "description": "which medium this reel is made in"}},
            ["medium"],
        ), run=set_medium),
        Tool(spec=llm.tool(
            "read_medium",
            "Read back exactly what the pipeline already asks every render for in this medium: "
            "the opening clause, the craft clause, the still and sheet suffixes, the physics, "
            "and what the review rejects. Takes no arguments. Read it before writing the style "
            "bible so the bible extends those words rather than contradicting them.",
            {},
        ), run=read_medium),
    ]


# ## The mise-en-scene artist


def _blocking_tools(llm: llm_mod.LLM) -> list[Tool]:
    def set_blocking(context: Context, arguments: dict) -> Outcome:
        """Where things stand in one frame, and what the set holds.

        Straight onto the beat rather than through `agent.apply_ops`, because it is not one of
        the seven board ops -- `apply_one` is the single place those happen and adding an eighth
        there would put a field the chat agent has no tool for into its dispatch table.
        """
        board = context.need_board()
        n = _beat_number(board, arguments)
        text = " ".join(_text(arguments, "blocking", "where things stand in this frame").split())
        board.beat(n)["blocking"] = text
        board.save()
        context.hooks.changed()
        return (f"beat {n} in frame: {text}",
                [{"op": "set_blocking", "n": n, "summary": f"blocked beat {n}"}])

    return [
        Tool(spec=llm.tool(
            "set_blocking",
            "Say where things stand in ONE shot's frame and what the set holds: which third "
            "each thing sits in, which way it faces, how much room is above it, what is in the "
            "foreground and the background. This reaches the video prompt, so rewriting it "
            "marks the beat as needing a re-render. It is not the shot size or the camera "
            "angle -- those are the storyboard panel's job.",
            {
                "n": {"type": "integer", "description": "which beat, 1-based"},
                "blocking": {
                    "type": "string",
                    "description": ("one or two sentences: where each thing stands in this "
                                    "frame and what the set holds. Copy any @ref: or @cast "
                                    "token in it exactly."),
                },
            },
            ["n", "blocking"],
        ), run=set_blocking),
    ]


# ## Coherence
#
# Text-only audit of action / blocking / asset_prompt / look-only fields. Writes nothing;
# the coherence agent (or the director) fixes what it names. Lives before continuity in the
# storyboard cast so seam phrases are written on the reconciled actions.


def _coherence_tools(llm: llm_mod.LLM) -> list[Tool]:
    def audit_coherence(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        deep = arguments.get("deep")
        if deep is None:
            deep = True
        findings = coherence.audit(
            board, deep=bool(deep), llm=context.llm or llm,
        )
        return coherence.format_report(findings), [
            {"op": "audit_coherence",
             "summary": f"{len(findings)} coherence finding(s)"},
        ]

    return [
        Tool(spec=llm.tool(
            "audit_coherence",
            "Read-only audit of fights between action, blocking, asset_prompt, style bible "
            "and design notes that will make the video model walk in place, animate idle "
            "doors, or otherwise invent motion. Returns findings only — fix them with "
            "set_beat / set_blocking / set_asset_prompt / describe_design / set_script, then "
            "re-audit. Costs nothing when deep is false (deterministic only).",
            {
                "deep": {
                    "type": "boolean",
                    "description": (
                        "When true (default), run one cheap structured pass if the "
                        "deterministic checks found nothing. Set false for a free "
                        "deterministic-only scan."
                    ),
                },
            },
            [],
        ), run=audit_coherence),
    ]


# ## The cross-check
#
# One tool, given to the three agents that check the assets stage's work. It looks and it
# reports; it renders nothing and it edits no prompt. See `inspect.py` for why that bound is
# where it is -- three lenses that could each reject and re-render turn one disagreeing panel
# into a run that spends its whole still budget on one beat.


def _check_tools(llm: llm_mod.LLM) -> list[Tool]:
    def inspect_still(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        n = _beat_number(board, arguments)
        lens = str(arguments.get("lens") or "").strip()
        try:
            verdict = critique.look(board, n, lens, llm=context.llm, log=context.hooks.log)
        except critique.InspectError as refused:
            raise ToolRefused(str(refused)) from None
        critique.record(board, n, verdict)
        board.save()
        context.hooks.changed()
        if verdict["passed"]:
            return (f"beat {n} passes on {lens}",
                    [{"op": "inspect_still", "n": n, "summary": f"beat {n}: {lens} passes"}])
        return (f"beat {n} fails on {lens}: {verdict['problem']} "
                f"Suggested fix: {verdict['fix']}",
                [{"op": "inspect_still", "n": n,
                  "summary": f"beat {n}: {lens} failed -- {verdict['problem']}"}])

    return [
        Tool(spec=llm.tool(
            "inspect_still",
            "Look at one beat's finished still through one lens and file a verdict with a "
            "suggested fix. You are one of several checking this picture, each at a different "
            "thing -- report only on yours. This renders nothing and changes no prompt: the "
            "director reads the verdicts and decides.",
            {
                "n": {"type": "integer", "description": "which beat, 1-based"},
                "lens": {"type": "string", "enum": critique.lenses(),
                         "description": ("style = is it really made of this material. blocking "
                                         "= is what is in frame what the beat said, standing "
                                         "where it said. story = is it the moment the script "
                                         "asked for.")},
            },
            ["n", "lens"],
        ), run=inspect_still),
    ]


# ## The director
#
# One conversational agent that edits the board directly or delegates to specialists. Delegation
# returns findings as tool text rather than top-level chat turns -- the director synthesizes
# for the user. The crew panel's run-all path still exists for directors who want batch work.


def _director_board(llm: llm_mod.LLM) -> list[Tool]:
    def write_caption(context: Context, _arguments: dict) -> Outcome:
        board = context.need_board()
        written = agent_mod.caption(board)
        context.hooks.changed()
        return written, [{"op": "set_caption", "summary": "wrote the caption"}]

    def generate_stills(context: Context, arguments: dict) -> Outcome:
        board = context.need_board()
        wanted = _beat_list(board, arguments) or []
        if not wanted:
            raise ToolRefused("say which beats, as a list of beat numbers")
        spent = int(context.state.get("stills_made") or 0)
        room = config.CREW_STILL_BUDGET - spent
        if room <= 0:
            return (f"this run has already rendered {spent} stills, which is its budget "
                    f"({config.CREW_STILL_BUDGET}). Say what is still wrong and stop."), []
        if len(wanted) > room:
            context.hooks.say(f"[stills] budget: rendering {room} of {len(wanted)} asked for")
            wanted = wanted[:room]
        outcome = agent_mod.generate_stills(
            board, {"beats": wanted}, log=context.hooks.log, progress=context.hooks.progress,
            announce=context.hooks.announce, cancelled=context.hooks.cancelled)
        context.state["stills_made"] = spent + len(wanted)
        context.hooks.changed()
        return outcome, [{"op": "generate_stills", "summary": f"rendered stills for {wanted}"}]

    return [
        Tool(spec=borrowed(llm, "set_script"), run=board_op("set_script")),
        Tool(spec=borrowed(llm, "set_beat"), run=board_op("set_beat")),
        Tool(spec=borrowed(llm, "add_beat"), run=board_op("add_beat")),
        Tool(spec=borrowed(llm, "remove_beat"), run=board_op("remove_beat")),
        Tool(spec=borrowed(llm, "set_source"), run=board_op("set_source")),
        Tool(spec=borrowed(llm, "set_caption"), run=write_caption),
        Tool(spec=borrowed(llm, "set_reel"), run=board_op("set_reel")),
        Tool(spec=borrowed(llm, "generate_stills"), run=generate_stills),
    ]


def _director_delegate(llm: llm_mod.LLM) -> list[Tool]:
    def crew_plan(context: Context, _arguments: dict) -> Outcome:
        from . import crew as crew_mod

        summary = crew_mod.plan_summary(context.board)
        plan = summary["plan"]
        if not plan and not summary["awaiting"]:
            return "nothing left for the crew -- only the render remains, which no agent can start", []
        lines = []
        if summary["awaiting"]:
            lines.append(f"awaiting phase: {summary['awaiting']}")
        if summary["done"]:
            lines.append("done: " + ", ".join(summary["done"]))
        for entry in plan:
            for phase in entry.get("phases") or []:
                agents = ", ".join(
                    f"{member['agent']}" + (f" ({member['lens']})" if member.get("lens") else "")
                    for member in phase["agents"]
                )
                lines.append(f"{entry['stage']}/{phase['id']} [{phase['status']}]: {agents}")
        return "Remaining crew work:\n" + "\n".join(lines), []

    def delegate_agent(context: Context, arguments: dict) -> Outcome:
        from . import crew as crew_mod

        board = context.need_board()
        name = str(arguments.get("agent") or "").strip()
        brief = _text(arguments, "brief", "what this specialist should do")
        roster = {entry["name"] for entry in skills.catalogue()}
        if name not in roster:
            raise ToolRefused(f"no agent called {name!r}. Available: {', '.join(sorted(roster))}")
        turn = crew_mod.one(name, board, brief, hooks=context.hooks, state=context.state,
                            via_director=True)
        board = turn.board or board
        lines = [
            f"agent: {turn.agent}",
            f"stopped: {turn.stopped}",
            f"rounds: {turn.rounds}",
            f"reply: {turn.reply}",
        ]
        if turn.ops:
            lines.append("edits: " + "; ".join(op["summary"] for op in turn.ops))
        findings = _checker_verdicts(board)
        if findings:
            lines.append("checker verdicts:\n" + findings)
        summary = f"delegated to {name}"
        return "\n".join(lines), [{"op": "delegate_agent", "summary": summary}]

    def run_crew_stage(context: Context, arguments: dict) -> Outcome:
        from . import crew as crew_mod

        board = context.need_board()
        stage = str(arguments.get("stage") or "").strip()
        if stage not in crew_mod.STAGES:
            raise ToolRefused(f"stage has to be one of {', '.join(crew_mod.STAGES)}")
        note = str(arguments.get("note") or "").strip()
        ungated = bool(arguments.get("ungated"))
        phase = str(arguments.get("phase") or "").strip() or None
        if phase is not None and phase not in crew_mod.PHASE_STAGE:
            raise ToolRefused(f"phase has to be one of {', '.join(crew_mod.PHASES)}")
        if ungated:
            turns = crew_mod.stage(stage, board, note=note, hooks=context.hooks,
                                   state=context.state, via_director=True)
            label = f"ran {stage} stage ungated"
        else:
            if phase is None:
                awaiting = crew_mod.awaiting_phase(board)
                if awaiting and crew_mod.PHASE_STAGE.get(awaiting) == stage:
                    phase = awaiting
                else:
                    remaining = [name for name in crew_mod.phases_for(stage)
                                 if name not in crew_mod.crew_record(board)["done"]]
                    phase = remaining[0] if remaining else None
            if phase is None:
                return (f"nothing left to run on the {stage} stage -- approve what is awaiting "
                        "or say ungated if you really want the whole cast again"), []
            turns = crew_mod.stage(stage, board, note=note, phase=phase, hooks=context.hooks,
                                   state=context.state, via_director=True)
            label = f"ran {stage}/{phase} (stopped at gate)"
        board = board_mod.Board.load(board.slug)
        lines = [f"{label} with {len(turns)} agent(s):"]
        for turn in turns:
            lines.append(f"  {turn.agent}: {turn.reply[:200]}")
        cursor = crew_mod.crew_record(board)
        if cursor["awaiting"]:
            lines.append(f"awaiting next: {cursor['awaiting']} -- stop and let the director approve")
        findings = _checker_verdicts(board)
        if findings:
            lines.append("checker verdicts:\n" + findings)
        return "\n".join(lines), [{"op": "run_crew_stage", "summary": label}]

    return [
        Tool(spec=llm.tool(
            "crew_plan",
            "Read what the crew would do next on this board, without doing any of it. "
            "Returns remaining stages, gated phases, and which phase is awaiting approval.",
            {},
        ), run=crew_plan),
        Tool(spec=llm.tool(
            "delegate_agent",
            "Hand one task to a named specialist and read back what they did. Use this when "
            "the work belongs to a particular craft -- writing, styling, storyboarding, stills. "
            "The specialist's report comes back here; synthesize it for the director in your "
            "reply rather than quoting it verbatim.",
            {
                "agent": {"type": "string",
                          "description": ("skill name, e.g. script-writer, style-paper-cutout, "
                                          "character-sheet, set-designer, continuity, "
                                          "mise-en-scene, storyboarder, asset-maker")},
                "brief": {"type": "string",
                          "description": "what this specialist should do on this board"},
            },
            ["agent", "brief"],
        ), run=delegate_agent),
        Tool(spec=llm.tool(
            "run_crew_stage",
            "Run the next gated phase of a stage (designs, seams, panels, stills, or inspect) "
            "and stop so the director can approve. Pass ungated=true only when the director "
            "explicitly wants the whole stage without pausing. Prefer this over burning through "
            "every specialist at once.",
            {
                "stage": {"type": "string", "enum": ["script", "storyboard", "assets"],
                          "description": "script, storyboard, or assets"},
                "phase": {"type": "string",
                          "description": ("optional phase id: script, designs, seams, panels, "
                                          "stills, inspect. Default is the awaiting phase.")},
                "note": {"type": "string",
                         "description": "anything specific the director wants them to know"},
                "ungated": {"type": "boolean",
                            "description": ("true to run every specialist in the stage without "
                                            "stopping at gates. Default false.")},
            },
            ["stage"],
        ), run=run_crew_stage),
    ]


def _checker_verdicts(board: board_mod.Board) -> str:
    """Recent checker verdicts from asset_chat, for the director to synthesize.

    Through `critique.filed` rather than a walk of its own, so the read of what a checker
    said has one copy -- `crew` reads the same turns to decide whether the inspect gate
    reopens the stills phase, and two walks of `asset_chat` is how they would drift.
    """
    lines = [f"  beat {item['beat']} {item['lens']} {item['verdict']}: {item['text'][:160]}"
             for item in critique.filed(board, recent=6)]
    return "\n".join(lines)
