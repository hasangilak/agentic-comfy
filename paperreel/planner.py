"""Writing the script: one drafting pass, then one pass where the model marks its own work.

Both passes are handed the same document the user would paste into an outside AI --
`prompts/40s-stop-motion-script.md`, verbatim. That file is the real specification for what
a script for this pipeline has to be: the beat grid, the four joins, the anti-AI-slop rules,
the style-bible contract, and a 22-point self-check. There is deliberately no second, shorter
copy of those rules in this module. There used to be, and a summary that drifts from the
document is worse than no summary: the two paths into a board -- import a script written
outside, or ask the model for one -- would quietly be writing to different briefs.

Only section 0 is replaced. The template opens by interviewing the director about beat
structure and shot count, which the studio has already asked for on the way in, so that
section becomes a block of answers and the model writes on its first turn instead of its
second.

Both passes run on Gemini (see `gemini.py`). The second one exists because it is cheap
rather than free: under the Antigravity CLI every turn came out of a plan quota that
refreshed on a ~5 hour window, so asking the model to re-read its own draft meant spending a
slot to look for faults that might not be there. A flash review turn costs a fraction of one
of the images this pipeline spends without hesitating, and the faults it finds are the ones
that are invisible on the page and unmissable in the render.

Nothing here generates an image. Stills come from Papercut Studio next door -- see
`stills.py` for the loop that renders them and then looks at them.
"""

from __future__ import annotations

import json
from typing import Callable

from . import board as board_mod
from . import config, gemini

# The authoring prompt, shared with the human path. `script.py` imports what this produces,
# so the two are in sync by construction rather than by discipline.
TEMPLATE_PATH = config.ROOT / "prompts" / "40s-stop-motion-script.md"

# The joins a script may ask for -- now all four, where "reference" used to be excluded because
# it conditioned only on photographs the user had to upload. It is the default cut instead: its
# still is generated from the same `asset_prompt` every other cut uses, so a script that emits it
# produces a beat that renders. The template is where the choice between it and "asset" is
# explained at length; both are cuts, and they differ in whether the opening frame is exact.
WRITABLE_SOURCES = [board_mod.SOURCE_REFERENCE, board_mod.SOURCE_CHAIN,
                    board_mod.SOURCE_BRIDGE, board_mod.SOURCE_ASSET]

PLAN_SCHEMA = {
    "type": "object",
    "required": ["title", "concept", "style_bible", "beats"],
    "properties": {
        "title": {"type": "string", "description": "short title, 2-5 words"},
        "concept": {"type": "string", "description": "one sentence describing the film"},
        "style_bible": {
            "type": "string",
            "description": (
                "One dense paragraph (section 6): medium and construction; every recurring "
                "character in forensic detail (species/build, materials and colours of each "
                "part, eyes, joints, garments, identifying marks -- wording two artists would "
                "build the same puppet from); the world's fixed elements; 5-7 named colours; "
                "one light rig; vertical 9:16 framing. Prepended verbatim to every image and "
                "video prompt as the identity lock across stitched 5s/10s clips -- look only, "
                "never motion, never story, never a specific moment. Refuse a thin or generic "
                "bible."
            ),
        },
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "scene", "action", "asset_prompt", "source"],
                "properties": {
                    "n": {"type": "integer", "description": "1-based, consecutive, no gaps"},
                    "scene": {
                        "type": "string",
                        "description": (
                            "One short line: where and when, and at what scale. Setting or "
                            "framing only -- never motion, never story. Every beat of one "
                            "continuous shot must carry identical text word for word."
                        ),
                    },
                    "action": {
                        "type": "string",
                        "description": (
                            "Motion only for a locked-off camera: visible actions in playback "
                            "order, not emotions; one primary motion that fits the duration "
                            "(5 s is a single gesture; 10 s can breathe) and a named ending "
                            "pose the clip arrives at and holds. Do NOT add a pan, tilt, "
                            "push, or dialogue. Do NOT restate materials, colours, markings, "
                            "or construction -- those live in style_bible and reference "
                            "pictures; restating them with drift invents a second puppet "
                            "mid-clip. On a same-shot continuation (reference after the "
                            "opening beat, or chain/bridge), open with a continuity phrase "
                            "and pick up the exact end-state of the beat before; on bridge, "
                            "finish in the state asset_prompt describes."
                        ),
                    },
                    "asset_prompt": {
                        "type": "string",
                        "description": (
                            "Layered still description (section 7), about 150-250 words, with "
                            "FOREGROUND / MIDGROUND / BACKGROUND / UPPER THIRD / LIGHT / "
                            "COMPOSITION labels. Required and non-empty on EVERY beat including "
                            "chained ones. Character look restated word-for-word from "
                            "style_bible. Describes the beat's first frame -- except on a "
                            "bridge, where it describes the frame the beat ends on. Beat 1 must "
                            "show every recurring character full, unobstructed, clearly lit. "
                            "Refuse a single-sentence or label-free prompt."
                        ),
                    },
                    "source": {
                        "type": "string",
                        "enum": WRITABLE_SOURCES,
                        "description": (
                            "Joins are a consistency tool across stitched clips. reference = "
                            "ref2va with stills, sheets and poses -- use for a new shot AND "
                            "for a long take (previous clip held as Video 1 once poses exist). "
                            "chain = pixel-exact last-frame handoff, no pictures. bridge = "
                            "that handoff AND must arrive at its own still. asset = "
                            "exact-keyframe cut with no cast ref through the clip -- rare. "
                            "Beat 1 is always reference. Prefer reference, reference, "
                            "reference over reference, chain, chain, bridge."
                        ),
                    },
                    "camera": {
                        "type": "string",
                        "enum": list(config.CAMERA_ANGLES),
                        "description": (
                            "Locked-off camera angle for this take: eye (straight-on, the "
                            "default), low (camera below looking up so the subject looms), "
                            "high (camera above looking down), overhead (looking straight "
                            "down), dutch (horizon off-level). A chain or bridge beat MUST "
                            "copy the camera of the shot it continues. This reaches the still "
                            "and the clip -- do not bury the angle in scene or action instead."
                        ),
                    },
                },
            },
        },
    },
}

# The review pass returns a whole corrected script rather than a patch. A patch schema would
# be smaller, but it puts the model in the business of addressing beats by index while
# rewriting them, and an off-by-one there silently attaches beat 3's fix to beat 2. Returning
# the script entire cannot go wrong that way, and `changes` is what gets logged.
#
# `changes` is LAST, and that ordering is load-bearing. The decode is constrained in schema
# order, so a `changes` array declared first is written before the corrections exist -- and a
# model with nowhere else to think uses it as a scratchpad. Measured: 40 log lines of
# stream-of-consciousness, several of them contradicting each other, before a single beat was
# rewritten. Declared last, with thinking enabled so the reasoning has a channel of its own, it
# comes back as what it is meant to be: a changelog of work already done.
REVIEW_SCHEMA = {
    "type": "object",
    "required": ["title", "concept", "style_bible", "beats", "changes"],
    "properties": {
        **PLAN_SCHEMA["properties"],
        "changes": {
            "type": "array",
            "maxItems": 12,
            "description": (
                "A changelog of the corrections you made, one short sentence each, naming the "
                "beat and the numbered self-check item it failed. Not your reasoning, and not "
                "a list of checks that passed. Empty if the draft passed everything -- do not "
                "invent changes to fill it."
            ),
            "items": {"type": "string"},
        },
    },
}


class NoTemplate(RuntimeError):
    """The authoring prompt is missing, and there is deliberately no fallback copy of it."""


def template(medium_key: str | None = None, envelope: str | None = None) -> str:
    """The brief, without the note to the human about where to paste it.

    Everything above the first horizontal rule is instructions for the operator ("paste
    everything below the line into the AI"), which is not addressed to the model.

    Three of its passages are medium-bound and live in `config.MEDIUMS` rather than in the file:
    the opening sentence about what the films are made of, section 4 (the physics), and section
    6(a) (the construction the style bible must lock down). Two more -- the opening length
    sentence and the duration menu in section 0 -- fork on the authoring envelope (`reel` vs
    `film`) the same way, so a 4-minute board is not handed a 40-second menu. Everything else
    is pipeline and is word-for-word correct in any medium, which is why the file was forked
    at seams rather than copied.

    Those three are not a find-and-replace of "paper" for "clay". Paper's whole grammar is that a
    shape is SWAPPED for another shape and clay's is that a shape BECOMES one, so section 4
    inverts rather than translates.
    """
    try:
        text = TEMPLATE_PATH.read_text()
    except OSError as missing:
        raise NoTemplate(
            f"the script-authoring prompt is missing from {TEMPLATE_PATH}. It is the only "
            "specification of what a script for this pipeline has to be, so nothing here "
            "writes one without it."
        ) from missing
    _, _, body = text.partition("\n---\n")
    look = config.medium(medium_key)
    length, duration = config.length_copy(envelope)
    return ((body or text)
            .replace("<<<OPENING>>>", look.opening)
            .replace("<<<PHYSICS>>>", look.physics)
            .replace("<<<CONSTRUCTION>>>", look.construction)
            .replace("<<<LENGTH>>>", length)
            .replace("<<<DURATION>>>", duration)
            .strip())


def brief(concept: str, beats: int, seconds: float,
          medium_key: str | None = None, envelope: str | None = None) -> str:
    """The authoring prompt with the interview answered and the concept filled in."""
    body = template(medium_key, envelope).replace("<<<CONCEPT>>>", concept.strip())
    answers = ANSWERS.format(
        beats=beats, seconds=f"{seconds:.0f}", total=f"{beats * seconds:.0f}",
        plural="" if beats == 1 else "s",
    )
    # Splice over section 0 rather than deleting it, so the self-check's first item ("did you
    # ask the section 0 questions and get answers") reads as satisfied rather than as skipped.
    start, end = body.find("## 0."), body.find("## 1.")
    if 0 <= start < end:
        return body[:start] + answers + body[end:]
    return answers + "\n\n" + body


# Replaces section 0. Two things it has to settle, because the rest of the document assumes
# them: that the interview already happened, and that the film's total is the product of the
# form's beat count and length (not a fixed 40 seconds).
#
# The beat length is stated as fixed and not the model's to choose. That is a real constraint
# rather than a simplification: the studio asked the user for a per-beat length and showed
# them a price for it, so a model that varies the rhythm -- which section 1 otherwise asks
# for, with good reason -- would be quietly doubling the cost of half the beats.
ANSWERS = """## 0. The director has already answered

Do not ask any questions and do not wait for a reply. The answers are below and they are
final. Write the script now and return the JSON on this turn.

1. **Beat structure.** {beats} beat{plural}, every one of them exactly {seconds}.0 seconds
   long, so the film runs {total} seconds in total. The length is fixed and is not yours to
   choose: do not vary it, and do not add or drop a beat to make a rhythm work. That total
   is the only duration that matters; the beat-length rules in section 1 that are about
   *choosing* between 5s and 10s do not apply.
2. **Shots.** Yours to choose, inside the rules of section 2 -- and this is the decision that
   matters most, so spend the thought on it rather than on the lengths. Decide where the cuts
   go and where the take carries on unbroken.
3. **The cast.** Yours to invent. Nothing is locked yet, so write the style_bible from
   scratch in the detail section 6 asks for.
4. **Tone and ending.** Yours to choose, from the concept.

---

"""


def plan(concept: str, beats: int, seconds: float, *,
         medium_key: str | None = None,
         envelope: str | None = None,
         log: Callable[[str], None] = print) -> dict:
    """A script from a one-line concept: draft, then review.

    `log` is how the review pass reports itself, and it matters that the corrections are
    visible: the model is editing words the user never saw, and a silent rewrite of a draft
    is indistinguishable from a model that writes well.
    """
    instructions = brief(concept, beats, seconds, medium_key, envelope)
    log(f"[plan] briefing {config.TEXT_MODEL} from {TEMPLATE_PATH.name}")
    draft = numbered(gemini.structured(
        [{"role": "user", "content": instructions}], PLAN_SCHEMA,
        think=config.PLAN_THINK, temperature=config.PLAN_TEMPERATURE,
    ))
    if not draft.get("beats"):
        raise gemini.GeminiError(f"{config.TEXT_MODEL} returned a script with no beats.")
    if len(draft["beats"]) != beats:
        # Not fatal, and not silently corrected either: the canvas can add or remove a beat
        # for free, and dropping one here would take a scene of the story with it.
        log(f'[plan] asked for {beats} beats and got {len(draft["beats"])}')
    if config.PLAN_REVIEW:
        draft = review(draft, instructions, log=log)
    return draft


# What the review may not touch, on the path where a form settled it. The other path's copy is
# `develop.SETTLED_BY_INTERVIEW`, and the two are siblings about SCOPE rather than about the
# rules of the medium -- which are in the brief, once, and are not restated in either.
SETTLED_BY_FORM = (
    "Items 1, 2, 3 and 12 are already settled and are NOT yours to fix: the director fixed the "
    "beat count and gave every beat the same length in section 0, so the lengths cannot vary "
    "and the total is whatever that comes to. Do not change a single beat length, and do not "
    "add or remove a beat.\n\n"
)


def review(draft: dict, instructions: str, *,
           log: Callable[[str], None] = print,
           settled: str = SETTLED_BY_FORM) -> dict:
    """Run the brief's own self-check against the draft and return the corrected script.

    The whole brief goes back in, not a summary of it. Section 11 is a numbered list of 22
    checks written against the rest of the document, and a reviewer holding only the checks
    marks the draft against its own idea of what they mean.

    A failed review is not a failed plan. The draft is already usable and every fault the
    review would have caught is fixable for free on the canvas, so a model that answers with
    something unparseable loses its correction, not the user's script.

    `settled` is the one paragraph that differs between the two ways a script gets written --
    see `SETTLED_BY_FORM`. Everything else about this pass is identical on both, deliberately.
    """
    prompt = (
        "Below is the brief you were given, and then the script that was written from it.\n\n"
        "Work through the self-check in section 11, item by item, against that script. Fix "
        "everything that fails and change nothing that passes -- you are correcting this "
        "script, not rewriting the film, so keep the same story and the same number of beats.\n\n"
        f"{settled}"
        "Spend the effort on the checks that are invisible on the page and obvious in the "
        "finished render: a thin or generic style_bible that could not lock the cast across "
        "stitched clips; an empty, single-sentence, or label-free asset_prompt (missing "
        "FOREGROUND/MIDGROUND/BACKGROUND/LIGHT/COMPOSITION); an action line that redesigns "
        "appearance (materials, colours, markings) instead of describing motion only; a "
        "continuation whose action lacks a continuity phrase or does not pick up the "
        "exact end-state of the beat before; a same-shot beat whose scene line differs from "
        "its shot's first beat; a long take written as chain when reference would keep the "
        "sheets; a character description in an asset_prompt that has drifted "
        "from the style bible; a shot running past 20 seconds; a third chain with no bridge "
        "or cut before it; no genuine close-up or no genuine wide anywhere in the film; no "
        "beat that is nearly motionless; and any banned word from section 5.9.\n\n"
        "Return the corrected script in full, then the changelog. Return JSON only.\n\n"
        f"===== THE BRIEF =====\n{instructions}\n\n"
        f"===== THE SCRIPT TO CHECK =====\n{_as_json(draft)}"
    )
    try:
        reviewed = gemini.structured(
            [{"role": "user", "content": prompt}], REVIEW_SCHEMA,
            # Thinking on, so the reasoning has somewhere to go that is not the changelog.
            think=config.PLAN_THINK, temperature=config.LLM_TEMPERATURE,
        )
    except gemini.GeminiError as failed:
        log(f"[plan] review skipped: {failed}")
        return draft

    # A review that came back with a different number of beats has rewritten the film rather
    # than corrected it, which is not what was asked for and not what the user will be shown.
    if len(reviewed.get("beats") or []) != len(draft["beats"]):
        log("[plan] review changed the beat count, so the draft was kept as written")
        return draft

    changes = [str(line).strip() for line in reviewed.get("changes") or [] if str(line).strip()]
    for line in changes:
        log(f"[plan] fixed: {line}")
    if not changes:
        log("[plan] self-check found nothing to fix")
    return numbered({**draft, **{k: v for k, v in reviewed.items() if k != "changes"}})


def numbered(plan: dict) -> dict:
    """Beat numbers from the array order rather than from what the model wrote.

    `script.normalise` does this again on the way into a board, but the review prompt shows
    the numbers to the model and its `changes` lines refer to them, so they have to be right
    before the draft is ever quoted back.
    """
    beats = [beat for beat in (plan.get("beats") or []) if isinstance(beat, dict)]
    for index, beat in enumerate(beats, start=1):
        beat["n"] = index
    plan["beats"] = beats
    return plan


def _as_json(plan: dict) -> str:
    """Only the fields the review may touch.

    A draft that arrived carrying anything else -- canvas positions, a render record -- must
    not be able to smuggle it into the review prompt and back out into the board.
    """
    return json.dumps({
        "title": plan.get("title", ""),
        "concept": plan.get("concept", ""),
        "style_bible": plan.get("style_bible", ""),
        "beats": [
            {key: beat.get(key, "")
             for key in ("n", "scene", "action", "asset_prompt", "source")}
            for beat in plan.get("beats") or []
        ],
    }, indent=2)
