"""Text-only audit: catch action / blocking / asset_prompt / design fights before stills.

The alligator reel's walk-in-place and idle door were written into the board long before H3
ran. Continuity owns seams on `scene`/`action`/`source` and is forbidden from touching
blocking or asset prompts; mise owns space but not motion; style and set sheets are told
"look only" and still smuggled hinged adjectives into FIXED SETS. Nobody owned the join.

This module finds those fights. It writes nothing. The coherence agent (or a director) reads
the findings and fixes them with the ordinary board tools.

Deterministic checks run first and cost nothing. When they find nothing, one structured flash
call looks for soft cases regex cannot catch -- optional emptiness, not a second opinion on
what was already named.
"""

from __future__ import annotations

import re
from typing import Any

from . import board as board_mod
from . import config, llm as llm_mod

# Kinds the agent (and the dry-run) can key on. Short, stable, no prose.
KIND_LOOK_MOTION = "look_motion"
KIND_LEAVE_ROOM = "leave_room"
KIND_TRAVEL_SET = "travel_set"
KIND_BRIDGE_LAND = "bridge_land"
KIND_AMBIENT_PROP = "ambient_prop"
KIND_MULTI_MOVER = "multi_mover"
KIND_SOFT = "soft"

FINDING_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "field", "problem", "fix"],
                "properties": {
                    "beat": {
                        "type": "integer",
                        "description": "1-based beat number when the finding is about a beat; omit for bible/design.",
                    },
                    "design": {
                        "type": "string",
                        "description": "Staging design id when the finding is about a sheet; omit otherwise.",
                    },
                    "field": {
                        "type": "string",
                        "description": (
                            "Which stored field to edit: action, blocking, asset_prompt, "
                            "style_bible, note, draw."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": [
                            KIND_LOOK_MOTION,
                            KIND_LEAVE_ROOM,
                            KIND_TRAVEL_SET,
                            KIND_BRIDGE_LAND,
                            KIND_AMBIENT_PROP,
                            KIND_MULTI_MOVER,
                            KIND_SOFT,
                        ],
                    },
                    "problem": {
                        "type": "string",
                        "description": "What fights the model, in one sentence.",
                    },
                    "fix": {
                        "type": "string",
                        "description": "Concrete rewrite direction, specific enough to act on.",
                    },
                },
            },
        },
    },
}

# Look-only fields that still smuggle capability/motion into conditioning ("opening door").
# "opening" as a participle on a prop is the measured failure; bare nouns stay.
_LOOK_MOTION_RE = re.compile(
    r"\b("
    r"opening|closing|swinging|swaying|walking|moving|sliding|rotating|pivoting|"
    r"fluttering|drifting|bobbing|waving|creaking"
    r")\b",
    re.IGNORECASE,
)

# Action asks the subject to travel across the locked frame. Lateral travel (a pull) is
# `config.is_travel`; this leftover is the broader "someone is going somewhere" used to
# skip hinged-prop findings when travel is already the primary.
_TRAVEL_RE = re.compile(
    r"\b("
    r"walk(?:s|ing)?|cross(?:es|ing)?|traverse|travel(?:s|ing)?|"
    r"left[\s-]?to[\s-]?right|right[\s-]?to[\s-]?left|"
    r"across the (?:path|frame|shot|street|road)|"
    r"slide(?:s|ing)? (?:left|right|across)|"
    r"move(?:s|ing)? (?:left|right|across|forward|ahead)|"
    r"keep walking|steadily ahead|procession"
    r")\b",
    re.IGNORECASE,
)

# Ground / set pieces that must SLIDE on a pull, not be named still.
_GROUND_RE = re.compile(
    r"\b(cabbage|trellis|fence|cloud|soil|ground|garden|background|set|"
    r"leaves?|panels?|reeds?|trees?|path)\b",
    re.IGNORECASE,
)

# Opening still already parks the subject where travel would end — classic walk-in-place.
_PLANTED_RE = re.compile(
    r"\b("
    r"stands? in the (?:right|left|middle|center|centre)(?:[- ]?\w+)? third|"
    r"stands? near the (?:right|left)(?:[- ]?\w+)? (?:frame )?edge|"
    r"occupies the (?:right|left|middle|center|centre)|"
    r"sits in the (?:right|left|middle|center|centre)"
    r")\b",
    re.IGNORECASE,
)

_EMPTY_SPACE_RE = re.compile(
    r"\b("
    r"leave .{0,40}open|empty (?:path|space|side|third|water|road)|"
    r"open (?:path|space|side|third) (?:to|on|at) the|"
    r"room (?:to|for) (?:the )?(?:right|left|walk|travel)|"
    r"whole (?:left|right) side open|stacked (?:near|at) the (?:left|right)"
    r")\b",
    re.IGNORECASE,
)

# Hinged / ambient set pieces that steal motion when they are not the primary action.
_HINGED_RE = re.compile(
    r"\b(door|gate|hatch|flap|shutter|window|lid)\b",
    re.IGNORECASE,
)

_STILL_CLAUSE_RE = re.compile(
    r"\b("
    r"remain(?:s|ing)? (?:completely |entirely )?(?:still|motionless|frozen|static|stationary)|"
    r"stays? (?:shut|closed|still|motionless|frozen|stationary)|"
    r"perfectly still|completely (?:still|frozen|stationary)|entirely (?:still|motionless)|"
    r"no other elements move|environment stays|set remains|"
    r"surrounding (?:set|paper|reeds|trees).{0,20}(?:still|motionless|stationary|frozen)"
    r")\b",
    re.IGNORECASE,
)

# Rough multi-mover: several named subjects each getting a motion verb in one action line.
_MOVER_CHUNK_RE = re.compile(
    r"(?:alligator|gator|puppet|character|figure|child|friend)s?\b[^.;]{0,80}?"
    r"\b(?:walk|stumble|turn|raise|lift|rotate|slide|reach|pull|cheer|accept|hold)\w*",
    re.IGNORECASE,
)

_SOFT_SYSTEM = (
    "You audit a stop-motion reel's stored text for places that will fight the video model. "
    "The opening still is frame one of the action, not the climax. Lateral travel on 9:16 "
    "is a background pull (puppet holds its third, set slides opposite) -- freezing the "
    "garden on a chase produces walk-in-place. Look-only fields "
    "(style bible, design note/draw) must never name motion or hinged capability. "
    "Report only real fights. Pass silently when nothing fights — an empty findings list is "
    "correct. Do not invent preference notes."
)


def audit(board: board_mod.Board, *, deep: bool = True,
          llm: llm_mod.LLM | None = None) -> list[dict[str, Any]]:
    """Every coherence finding on this board, deterministic first.

    When the deterministic pass is empty and `deep` is true, one structured call looks for
    soft cases. When deterministic already named problems, that call is skipped: a second
    opinion on the same fights is noise the agent then has to dedupe.
    """
    found = _deterministic(board)
    if found or not deep:
        return found
    return _soft(board, llm or llm_mod.provider())


def format_report(findings: list[dict[str, Any]]) -> str:
    """Prose the tool returns to the agent."""
    if not findings:
        return "coherence audit: clean — no action/blocking/still/design fights found."
    lines = [f"coherence audit: {len(findings)} finding(s)"]
    for index, item in enumerate(findings, start=1):
        where = _where(item)
        lines.append(
            f"{index}. [{item.get('kind', KIND_SOFT)}] {where} {item.get('field', '?')}: "
            f"{item.get('problem', '').strip()}"
        )
        fix = (item.get("fix") or "").strip()
        if fix:
            lines.append(f"   fix: {fix}")
    return "\n".join(lines)


def _where(item: dict[str, Any]) -> str:
    if item.get("beat") is not None:
        return f"beat {item['beat']}"
    if item.get("design"):
        return f"design {item['design']}"
    return "reel"


def _finding(*, kind: str, field: str, problem: str, fix: str,
             beat: int | None = None, design: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "kind": kind,
        "field": field,
        "problem": problem,
        "fix": fix,
    }
    if beat is not None:
        item["beat"] = beat
    if design is not None:
        item["design"] = design
    return item


def _deterministic(board: board_mod.Board) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    found.extend(_look_motion_findings(board))
    for beat in board.beats:
        n = int(beat["n"])
        action = (beat.get("action") or "").strip()
        blocking = (beat.get("blocking") or "").strip()
        asset = (beat.get("asset_prompt") or "").strip()
        source = board.source_for(beat)
        found.extend(_leave_room_findings(n, action, blocking, asset))
        found.extend(_travel_set_findings(n, action))
        found.extend(_ambient_prop_findings(n, action, blocking, asset))
        found.extend(_multi_mover_findings(n, action))
        if source == board_mod.SOURCE_BRIDGE:
            found.extend(_bridge_land_findings(n, action, asset))
    return found


def _look_motion_findings(board: board_mod.Board) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    bible = (board.data.get("style_bible") or "").strip()
    for match in _LOOK_MOTION_RE.finditer(bible):
        word = match.group(1)
        found.append(_finding(
            kind=KIND_LOOK_MOTION,
            field="style_bible",
            problem=(
                f"Style bible uses motion/capability word {word!r}; look-only fields must "
                "describe state, never what a prop can do mid-clip."
            ),
            fix=(
                f"Replace hinged/motion adjectives with a static state (e.g. 'shut door' "
                f"not '{word} door'). Style bible must stay true of every frame."
            ),
        ))
        break  # one bible finding is enough; the agent rewrites the paragraph once
    for entry in board.staging:
        entry_id = str(entry.get("id") or "")
        name = board.stage_name(entry)
        for field in ("note", "draw"):
            text = (entry.get(field) or "").strip()
            match = _LOOK_MOTION_RE.search(text)
            if not match:
                continue
            word = match.group(1)
            found.append(_finding(
                kind=KIND_LOOK_MOTION,
                field=field,
                design=entry_id,
                problem=(
                    f"Design {name!r} {field} uses motion/capability word {word!r}, which "
                    "conditions every still and clip that binds this sheet."
                ),
                fix=(
                    f"Rewrite {field} to a static state ('shut mustard-yellow door', not "
                    f"'{word} … door'). Capability is not look."
                ),
            ))
    return found


def _leave_room_findings(n: int, action: str, blocking: str, asset: str) -> list[dict[str, Any]]:
    if not action or not _TRAVEL_RE.search(action):
        return []
    # A pull holds the third; empty destination space is the locked-camera grammar and
    # is the wrong fix -- it is how the still then fights the sliding set.
    if config.is_travel(action):
        return []
    # Still + blocking already consume the path with no named empty travel space.
    planted = bool(_PLANTED_RE.search(blocking) or _PLANTED_RE.search(asset))
    room = bool(_EMPTY_SPACE_RE.search(blocking) or _EMPTY_SPACE_RE.search(asset))
    if not planted or room:
        return []
    return [_finding(
        kind=KIND_LEAVE_ROOM,
        beat=n,
        field="blocking",
        problem=(
            "Action asks for travel across the frame, but blocking/asset_prompt already park "
            "the subject mid-path or at the destination thirds with no empty travel space — "
            "H3 will animate legs in place."
        ),
        fix=(
            "Rewrite blocking and asset_prompt so the opening frame stacks the subject at the "
            "start edge with the destination side open; name the empty space. Keep action's "
            "end state for a later beat or the same beat's landing still on a bridge."
        ),
    )]


def _travel_set_findings(n: int, action: str) -> list[dict[str, Any]]:
    """Lateral travel whose action freezes the ground -- the measured treadmill."""
    if not action or not config.is_travel(action):
        return []
    if not _STILL_CLAUSE_RE.search(action) or not _GROUND_RE.search(action):
        return []
    return [_finding(
        kind=KIND_TRAVEL_SET,
        beat=n,
        field="action",
        problem=(
            "Action is lateral travel but names the set as still -- H3 will walk in place "
            "against a glued-down garden."
        ),
        fix=(
            "Rewrite the still-clause as a background pull: the set layers slide opposite "
            "the walk (same pieces, shifting in the frame). Name as still only props that "
            "are not the ground (a snail, a held lantern)."
        ),
    )]


def _ambient_prop_findings(n: int, action: str, blocking: str, asset: str) -> list[dict[str, Any]]:
    if not action:
        return []
    # Primary motion is already the hinged piece — fine.
    if _HINGED_RE.search(action) and _TRAVEL_RE.search(action) is None:
        # Door/flap as the named mover is intentional; still require a still-clause for the rest.
        return []
    hinged_in_set = (
        _HINGED_RE.search(blocking)
        or _HINGED_RE.search(asset)
        or _HINGED_RE.search(action)
    )
    if not hinged_in_set:
        return []
    # Action names the door as primary motion — leave it.
    if re.search(
        r"\b(door|gate|hatch|flap|shutter|lid)\b[^.;]{0,40}\b"
        r"(open|close|swing|slide|lift|rotate|pivot)",
        action,
        re.IGNORECASE,
    ) or re.search(
        r"\b(open|close|swing|slide)s?\b[^.;]{0,40}\b(door|gate|hatch|flap|shutter|lid)\b",
        action,
        re.IGNORECASE,
    ):
        return []
    if _STILL_CLAUSE_RE.search(action):
        # Named stillness exists, but if the hinged word is only in the set and the still
        # clause does not name the door, beat 7-style leaf holds still invent door fidget.
        if _HINGED_RE.search(action) and re.search(
            r"\b(door|gate|hatch|flap|shutter|lid)\b.{0,30}"
            r"(?:still|shut|closed|motionless|frozen|static)",
            action,
            re.IGNORECASE,
        ):
            return []
        if not _HINGED_RE.search(blocking or asset):
            return []
        # Stillness named, hinged prop only in set dressing — still a risk on hold beats.
        if re.search(r"\b(motionless|entirely motionless|nothing moves|five full seconds)\b",
                     action, re.IGNORECASE):
            return [_finding(
                kind=KIND_AMBIENT_PROP,
                beat=n,
                field="action",
                problem=(
                    "Hold / near-motionless beat stages a hinged set piece (door/gate/flap) "
                    "without naming it as shut and still — the model invents idle prop motion."
                ),
                fix=(
                    "Add an explicit still clause for the hinged piece ('the door stays shut') "
                    "or reframe so the door is out of frame. Do not leave hinged props unnamed "
                    "on a starved motion budget."
                ),
            )]
        return []
    # Hinged prop visible in set or mentioned, action neither uses it nor freezes it.
    if _HINGED_RE.search(blocking or asset) and not _HINGED_RE.search(action):
        return [_finding(
            kind=KIND_AMBIENT_PROP,
            beat=n,
            field="action",
            problem=(
                "Blocking/asset_prompt stage a hinged set piece that the action neither "
                "animates as primary motion nor names as still — ambient prop fidget risk."
            ),
            fix=(
                "Either make the hinged piece the one primary motion with a definite end "
                "state, or name it shut/still in the action. Scrub 'opening' from the design "
                "note if the door is only dressing."
            ),
        )]
    return []


def _multi_mover_findings(n: int, action: str) -> list[dict[str, Any]]:
    if not action:
        return []
    # "the three … walk" is one primary (the line), not three — skip group-noun walks.
    if re.search(r"\b(?:the )?(?:three|two|both|all)\b[^.;]{0,40}\bwalk", action, re.IGNORECASE):
        # Still warn when the line also gives a second distinct beat of motion to another.
        if re.search(
            r"\bwhile\b.+\b(?:walk|stumble|turn|raise|rotate|slide|reach)\w*",
            action,
            re.IGNORECASE,
        ):
            return [_finding(
                kind=KIND_MULTI_MOVER,
                beat=n,
                field="action",
                problem=(
                    "Action gives a group walk plus a second concurrent motion — more than "
                    "one primary mover for a locked-off beat."
                ),
                fix=(
                    "Pick one primary motion (the line translating, or one stumble). Name "
                    "everyone else as still."
                ),
            )]
        return []
    movers = _MOVER_CHUNK_RE.findall(action)
    if len(movers) <= 1:
        return []
    return [_finding(
        kind=KIND_MULTI_MOVER,
        beat=n,
        field="action",
        problem=(
            "Action appears to animate more than one subject as primary motion in a single "
            "beat — the model will jitter everyone instead of committing one clear move."
        ),
        fix=(
            "Name one primary mover and its motion; list the others as perfectly still."
        ),
    )]


def _bridge_land_findings(n: int, action: str, asset: str) -> list[dict[str, Any]]:
    """Soft structural check: bridge must finish in a state the landing still can show.

    Full semantic agreement is the soft LLM pass; here we only catch the empty-still case
    and the obvious 'coming to rest' without a still prompt at all.
    """
    if not action:
        return []
    if not asset:
        return [_finding(
            kind=KIND_BRIDGE_LAND,
            beat=n,
            field="asset_prompt",
            problem=(
                "Bridge beat has an action but no asset_prompt — the landing still and the "
                "words cannot agree about where the beat ends."
            ),
            fix=(
                "Write an asset_prompt that is the end state the action arrives at (pose, "
                "positions, props), not a mid-motion frame."
            ),
        )]
    return []


def _soft(board: board_mod.Board, speaker: llm_mod.LLM) -> list[dict[str, Any]]:
    digest = _board_text(board)
    result = speaker.structured(
        [
            {"role": "system", "content": _SOFT_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Audit this reel for fights between action, blocking, asset_prompt, and "
                    "look-only fields (style bible, design note/draw). Focus on: lateral travel "
                    "that freezes the set (a pull must slide the ground); climbing/raising "
                    "without room at the start of that motion; motion words in look-only "
                    "fields; hinged props stealing motion on hold beats; bridge action "
                    "end-state vs asset_prompt disagreement.\n\n"
                    f"{digest}\n\nReturn JSON only."
                ),
            },
        ],
        FINDING_SCHEMA,
        think=False,
        temperature=0.1,
        model=config.TEXT_MODEL,
    )
    raw = result.get("findings") or []
    cleaned: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        problem = str(item.get("problem") or "").strip()
        if not problem:
            continue
        cleaned.append(_finding(
            kind=str(item.get("kind") or KIND_SOFT),
            field=str(item.get("field") or "action"),
            problem=problem,
            fix=str(item.get("fix") or "").strip(),
            beat=int(item["beat"]) if item.get("beat") is not None else None,
            design=str(item["design"]) if item.get("design") else None,
        ))
    return cleaned


def _board_text(board: board_mod.Board) -> str:
    lines = [
        f"TITLE: {board.data.get('title') or board.slug}",
        f"STYLE BIBLE: {(board.data.get('style_bible') or '').strip()}",
        "DESIGNS:",
    ]
    for entry in board.staging:
        lines.append(
            f"- {entry.get('id')} ({entry.get('kind')}) {board.stage_name(entry)}: "
            f"note={(entry.get('note') or '')}; draw={(entry.get('draw') or '')}"
        )
    lines.append("BEATS:")
    for beat in board.beats:
        n = beat["n"]
        lines.append(
            f"#{n} source={board.source_for(beat)}\n"
            f"  scene: {beat.get('scene') or ''}\n"
            f"  action: {beat.get('action') or ''}\n"
            f"  blocking: {beat.get('blocking') or ''}\n"
            f"  asset_prompt: {beat.get('asset_prompt') or ''}"
        )
    return "\n".join(lines)
