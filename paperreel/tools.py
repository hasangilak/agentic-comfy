"""What the three agents can do, as tools over the modules that already do it.

Every tool here is a thin call into `agent.py`, `board.py`, `develop.py`, `panels.py`,
`pictures.py`, `planner.py`, `staging.py` or `stills.py`. That is the whole design and it is
worth stating plainly: the measured prompt scaffolding, the fingerprint rules, the still review,
the join guards and the picture budget all keep exactly one copy, in the module that was
written around them. An agent is a different way to *reach* those, never a second version.

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
from . import config, develop, llm as llm_mod, panels, pictures, planner, staging, stills
from .runtime import Context, Outcome, Tool, ToolRefused


def toolbox(llm: llm_mod.LLM | None = None) -> dict[str, Tool]:
    """Every tool, by name, with its declaration built in this provider's dialect.

    Built per call rather than cached at import because the declaration shape belongs to the
    provider (`llm.tool`), and building a few dozen dicts is nothing next to one model turn.
    """
    speaker = llm or llm_mod.provider()
    found: dict[str, Tool] = {}
    for make in (_shared, _script_tools, _storyboard_tools, _asset_tools):
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

    return [Tool(spec=borrowed(llm, "read_board"), run=read_board)]


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
