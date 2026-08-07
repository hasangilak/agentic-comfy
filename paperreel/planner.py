"""Writing the script: one drafting pass, then one pass where the model marks its own work.

Both passes are handed the same document the user would paste into an outside AI --
`prompts/40s-paper-cutout-script.md`, verbatim. That file is the real specification for what
a script for this pipeline has to be: the beat grid, the four joins, the anti-AI-slop rules,
the style-bible contract, and a 22-point self-check. There is deliberately no second, shorter
copy of those rules in this module. There used to be, and a summary that drifts from the
document is worse than no summary: the two paths into a board -- import a script written
outside, or ask the local model for one -- would quietly be writing to different briefs.

Only section 0 is replaced. The template opens by interviewing the director about beat
structure and shot count, which the studio has already asked for on the way in, so that
section becomes a block of answers and the model writes on its first turn instead of its
second.

Both passes run on the local model (see `qwen.py`), so neither costs anything. That is the
whole reason the second one exists. Under the Antigravity CLI every turn came out of a plan
quota that refreshed on a ~5 hour window, so asking the model to re-read its own draft meant
spending a slot to look for faults that might not be there. Locally it is seconds, and the
faults it finds are the ones that are invisible on the page and unmissable in the render.

Nothing here generates an image. Stills come from Papercut Studio next door -- see
`stills.py` for the loop that renders them and then looks at them.
"""

from __future__ import annotations

import json
from typing import Callable

from . import board as board_mod
from . import config, qwen

# The authoring prompt, shared with the human path. `script.py` imports what this produces,
# so the two are in sync by construction rather than by discipline.
TEMPLATE_PATH = config.ROOT / "prompts" / "40s-paper-cutout-script.md"

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
                "The single dense paragraph from section 6 of the brief: medium and "
                "construction, every recurring character in forensic detail, the world's "
                "fixed elements, the 5-7 named colours, the lighting rig, the framing. "
                "Prepended to every image and video prompt, so it must describe look only -- "
                "never motion, never story, never a specific moment."
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
                            "One line: where and when this beat happens, and at what scale. "
                            "It is rendered, so setting only -- never motion. Beats in one "
                            "shot carry identical text."
                        ),
                    },
                    "action": {
                        "type": "string",
                        "description": (
                            "What MOVES, for a locked-off camera, and what stays perfectly "
                            "still. One primary motion. On a chain or bridge beat it opens "
                            "with a continuity phrase and picks up in the exact end-state of "
                            "the beat before."
                        ),
                    },
                    "asset_prompt": {
                        "type": "string",
                        "description": (
                            "The layered still description from section 7, 150-250 words, "
                            "required and non-empty on EVERY beat including chained ones. "
                            "Describes the beat's first frame -- except on a bridge, where it "
                            "describes the frame the beat ends on."
                        ),
                    },
                    "source": {
                        "type": "string",
                        "enum": WRITABLE_SOURCES,
                        "description": (
                            "reference = this beat begins a new shot, a cut. chain = it "
                            "continues the previous beat unbroken. bridge = it continues the "
                            "previous beat AND must arrive at its own still. asset = a cut "
                            "whose opening frame must be exact, which is rare -- see section 2. "
                            "Beat 1 is always reference."
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
# `changes` is LAST, and that ordering is load-bearing. Ollama constrains the decode in schema
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


def template() -> str:
    """The brief, without the note to the human about where to paste it.

    Everything above the first horizontal rule is instructions for the operator ("paste
    everything below the line into the AI"), which is not addressed to the model.
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
    return (body or text).strip()


def brief(concept: str, beats: int, seconds: float) -> str:
    """The authoring prompt with the interview answered and the concept filled in."""
    body = template().replace("<<<CONCEPT>>>", concept.strip())
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
# them: that the interview already happened, and that the film is not necessarily 40 seconds.
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
   choose: do not vary it, and do not add or drop a beat to make a rhythm work. Everywhere
   below that says the film is 40 seconds, read {total} seconds instead -- that total is the
   only thing about the brief that has changed, and the beat-length rules in section 1 that
   are about *choosing* between 5s and 10s do not apply.
2. **Shots.** Yours to choose, inside the rules of section 2 -- and this is the decision that
   matters most, so spend the thought on it rather than on the lengths. Decide where the cuts
   go and where the take carries on unbroken.
3. **The cast.** Yours to invent. Nothing is locked yet, so write the style_bible from
   scratch in the detail section 6 asks for.
4. **Tone and ending.** Yours to choose, from the concept.

---

"""


def plan(concept: str, beats: int, seconds: float, *,
         log: Callable[[str], None] = print) -> dict:
    """A script from a one-line concept: draft, then review.

    `log` is how the review pass reports itself, and it matters that the corrections are
    visible: the model is editing words the user never saw, and a silent rewrite of a draft
    is indistinguishable from a model that writes well.
    """
    instructions = brief(concept, beats, seconds)
    log(f"[plan] briefing {config.QWEN_MODEL} from {TEMPLATE_PATH.name}")
    draft = _numbered(qwen.structured(
        [{"role": "user", "content": instructions}], PLAN_SCHEMA,
        think=config.PLAN_THINK, temperature=config.PLAN_TEMPERATURE,
    ))
    if not draft.get("beats"):
        raise qwen.OllamaError(f"{config.QWEN_MODEL} returned a script with no beats.")
    if len(draft["beats"]) != beats:
        # Not fatal, and not silently corrected either: the canvas can add or remove a beat
        # for free, and dropping one here would take a scene of the story with it.
        log(f'[plan] asked for {beats} beats and got {len(draft["beats"])}')
    if config.PLAN_REVIEW:
        draft = review(draft, instructions, log=log)
    return draft


def review(draft: dict, instructions: str, *,
           log: Callable[[str], None] = print) -> dict:
    """Run the brief's own self-check against the draft and return the corrected script.

    The whole brief goes back in, not a summary of it. Section 11 is a numbered list of 22
    checks written against the rest of the document, and a reviewer holding only the checks
    marks the draft against its own idea of what they mean.

    A failed review is not a failed plan. The draft is already usable and every fault the
    review would have caught is fixable for free on the canvas, so a model that answers with
    something unparseable loses its correction, not the user's script.
    """
    prompt = (
        "Below is the brief you were given, and then the script that was written from it.\n\n"
        "Work through the self-check in section 11, item by item, against that script. Fix "
        "everything that fails and change nothing that passes -- you are correcting this "
        "script, not rewriting the film, so keep the same story and the same number of beats.\n\n"
        "Items 1, 2, 3 and 12 are already settled and are NOT yours to fix: the director fixed "
        "the beat count and gave every beat the same length in section 0, so the lengths cannot "
        "vary and the total is whatever that comes to. Do not change a single beat length, and "
        "do not add or remove a beat.\n\n"
        "Spend the effort on the checks that are invisible on the page and obvious in the "
        "finished render: a chained beat whose action does not pick up in the exact end-state "
        "of the beat before it, a chained beat whose scene line differs from its shot's first "
        "beat, a character description in an asset_prompt that has drifted from the style "
        "bible, an empty or thin asset_prompt, a shot running past 20 seconds, a third chain "
        "with no bridge or cut before it, no genuine close-up or no genuine wide anywhere in "
        "the film, no beat that is nearly motionless, and any banned word from section 5.9.\n\n"
        "Return the corrected script in full, then the changelog. Return JSON only.\n\n"
        f"===== THE BRIEF =====\n{instructions}\n\n"
        f"===== THE SCRIPT TO CHECK =====\n{_as_json(draft)}"
    )
    try:
        reviewed = qwen.structured(
            [{"role": "user", "content": prompt}], REVIEW_SCHEMA,
            # Thinking on, so the reasoning has somewhere to go that is not the changelog.
            think=config.PLAN_THINK, temperature=config.QWEN_TEMPERATURE,
        )
    except qwen.OllamaError as failed:
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
    return _numbered({**draft, **{k: v for k, v in reviewed.items() if k != "changes"}})


def _numbered(plan: dict) -> dict:
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
