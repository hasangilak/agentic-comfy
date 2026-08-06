"""Adopting a script that was written outside the studio.

The other way in asks agy for a script from a one-line concept. That is free and fast, but
it is also agy's idea of the film. Someone who has already written the shot list elsewhere
-- by hand, or by walking an AI through `prompts/40s-paper-cutout-script.md` -- is holding
exactly what agy would have produced, and should not have to talk it back into existence
one nudge at a time. So this takes the JSON straight in, and no agy turn happens at all.

Only the fields the board renders from survive. Per-beat `render` records, `canvas`
positions and `spend_seconds` are dropped on the way through, because the new directory
holds none of the files they refer to: a fingerprint with no clip beside it would have the
canvas calling a beat finished, and the render button would refuse to price it.
"""

from __future__ import annotations

import json
import re

from . import board as board_mod, config

# Only here so a mis-paste fails loudly instead of quietly building a board nobody wants.
# The authoring prompt tops out at eight beats; anything near this is a mistake.
MAX_BEATS = 40


class BadScript(ValueError):
    """The text is not a script a board can be built from. The message is user-facing."""


def parse(text: str) -> dict:
    """JSON out of whatever was pasted.

    Models told "return JSON only" still wrap it in a markdown fence often enough that
    rejecting it would be pedantry: the fence is not a mistake the author made.
    """
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[A-Za-z]*[ \t]*\r?\n?", "", body)
        body = re.sub(r"```\s*$", "", body).strip()
    if not body:
        raise BadScript("nothing to import")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as bad:
        raise BadScript(f"that is not valid JSON: {bad.msg} (line {bad.lineno})") from bad
    if not isinstance(data, dict):
        raise BadScript("expected a JSON object with title, style_bible and beats")
    return data


def normalise(data: dict) -> dict:
    """A board document, from a script that may be missing or over-supplying anything."""
    raw_beats = data.get("beats")
    if not isinstance(raw_beats, list) or not raw_beats:
        raise BadScript("the script has no beats")
    if len(raw_beats) > MAX_BEATS:
        raise BadScript(f"{len(raw_beats)} beats is past the {MAX_BEATS} this can hold")

    beats: list[dict] = []
    for index, raw in enumerate(raw_beats, start=1):
        if not isinstance(raw, dict):
            raise BadScript(f"beat {index} is not an object")
        action = str(raw.get("action") or "").strip()
        if not action:
            raise BadScript(f"beat {index} has no action -- that is the line that renders")
        source = raw.get("source")
        beats.append({
            # Renumbered from the array order rather than trusted: `n` in a hand-written
            # script is often wrong after a beat was cut out of the middle, and every path
            # below -- chaining, file names, the canvas -- is keyed on it being 1..N.
            "n": index,
            "scene": str(raw.get("scene") or "").strip(),
            "action": action,
            "asset_prompt": str(raw.get("asset_prompt") or "").strip(),
            "seconds": config.snap_seconds(
                raw.get("seconds") or data.get("seconds") or config.BEAT_LENGTHS[-1]
            ),
            # The first beat has nothing before it to continue from, whatever the script
            # says. Later beats default to chaining, which is the free join.
            "source": (
                board_mod.SOURCE_ASSET if index == 1
                else source if source in board_mod.SOURCES
                else board_mod.SOURCE_CHAIN
            ),
        })

    title = str(data.get("title") or "").strip()
    concept = str(data.get("concept") or "").strip()
    if not title and not concept:
        raise BadScript("the script needs a title (or a concept) to name the reel by")

    return {
        "title": title or concept[:60],
        "concept": concept,
        "style_bible": str(data.get("style_bible") or "").strip(),
        "caption": str(data.get("caption") or "").strip(),
        "beats": beats,
        # How the film is rendered is the board's business, not the script's -- the two
        # knobs live on the canvas, and a script that guessed at them would override a
        # choice the user made there.
        "seconds": config.snap_seconds(data.get("seconds") or config.BEAT_LENGTHS[-1]),
        "steps": config.DEFAULT_STEPS,
        "seed": 1101,
    }


def notes(plan: dict) -> list[str]:
    """What is thin about an adopted script, said once at import rather than discovered later.

    None of these are refusals: an incomplete script is still worth having on the canvas,
    where every one of them can be fixed for free.
    """
    found = []
    if not plan.get("style_bible"):
        found.append(
            "no style_bible: it goes into every image and video prompt, so without it each "
            "shot is a fresh reading of its own prompt and the beats will not look like one "
            "production."
        )

    blind = [b["n"] for b in plan["beats"]
             if b["source"] == board_mod.SOURCE_ASSET and not b["asset_prompt"]]
    if blind:
        found.append(
            f"beat {', '.join(map(str, blind))} -- opens a new shot with no asset_prompt: "
            "generate has nothing to work from. Upload a still there, or write the prompt."
        )

    # A bridge is the one join that needs its asset_prompt to describe the LAST frame, so a
    # missing one is worth its own note rather than being folded in with the cuts above.
    landless = [b["n"] for b in plan["beats"]
                if b["source"] == board_mod.SOURCE_BRIDGE and not b["asset_prompt"]]
    if landless:
        found.append(
            f"beat {', '.join(map(str, landless))} -- continues from the beat before and is "
            "meant to land on a still of its own, but has no asset_prompt for it. Upload the "
            "frame it should end on, write the prompt, or make it a plain continuation."
        )

    unpromotable = [b["n"] for b in plan["beats"]
                    if b["source"] == board_mod.SOURCE_CHAIN and not b["asset_prompt"]]
    if unpromotable:
        found.append(
            f"beat {', '.join(map(str, unpromotable))} -- continues from the beat before with "
            "no asset_prompt: if that hand-off degrades there is nothing to promote it to its "
            "own shot from."
        )
    return found


def free_slug(base: str) -> str:
    """A reel directory that is not already somebody else's.

    Importing must never land on an existing board. A script is often a rewrite of a film
    that already has paid renders in it, and Board.create would replace the storyboard
    while leaving the old clips and stills in place -- beats claiming to be finished
    versions of scenes that no longer exist.
    """
    root = board_mod.reels_dir()
    if not (root / base).exists():
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if not (root / candidate).exists():
            return candidate
    raise BadScript(f"too many reels are already called {base}")


def adopt(data: dict, *, slug: str | None = None,
          manual_stills: bool = False) -> board_mod.Board:
    """Build a board from a supplied script. Costs nothing and calls nothing.

    `manual_stills` says the opening frames are the author's own work too, so nothing on
    this board may spend image quota. Worth deciding at import: the first thing an imported
    script offers otherwise is a button that generates the stills it just described.
    """
    plan = normalise(data)
    plan["manual_stills"] = manual_stills
    cuts = [b["n"] for b in plan["beats"] if b["source"] == board_mod.SOURCE_ASSET]
    # Every beat that needs an image of its own, which is not the same list: a bridge needs
    # one too, as the frame it lands on rather than the one it opens on.
    stills = [b["n"] for b in plan["beats"] if board_mod.uses_asset(b["source"])]
    total = sum(b["seconds"] for b in plan["beats"])
    # One turn, so the panel opens with the shape of what arrived rather than empty, and so
    # agy's next turn replays a transcript that says where the board came from.
    plan["chat"] = [{
        "role": "studio",
        "text": (
            f'Imported "{plan["title"]}": {len(plan["beats"])} beats, {total:.0f}s, '
            f'{len(cuts)} shot{"" if len(cuts) == 1 else "s"}. '
            + ("Every beat opens on its own still, so nothing is chained."
               if len(cuts) == len(plan["beats"]) else
               f'Stills needed for beat {", ".join(map(str, stills))}; the others continue '
               "from the beat before them.")
            + (" Image generation is off -- those stills are yours to supply."
               if manual_stills else "")
        ),
        "ops": [{"op": "set_script", "summary": "adopted a script written outside the studio"}],
    }]
    # Named after the title, not the concept: a supplied script has a title the author chose,
    # where agy's path only has the one-line concept to go on when the directory is made.
    return board_mod.Board.create(
        slug or free_slug(board_mod.slugify(plan["title"] or plan["concept"])), plan
    )
