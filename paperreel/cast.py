"""Text-only audit: who is in the film, and whether a beat silently dropped them.

The flock reel wrote "a flock" into beats 1–2 and "a single crane" into beats 3–4, minted one
character sheet whose note said "single", then blocked and inspected against that beat text --
so a still of one bird passed. Continuity owns seams, coherence owns motion fights, and
mise-en-scène owned *where* things stand, not *whether the same subjects are still the film*.
Nobody owned the roster. This module does.

Findings only. It writes nothing. Mise-en-scène (and coherence, which already re-audits after
fixes) read the report and repair with the ordinary board tools.

Deterministic checks run first and cost nothing. When they find nothing, one structured flash
call looks for soft cases regex cannot catch -- optional emptiness, not a second opinion on
what was already named.
"""

from __future__ import annotations

import re
from typing import Any

from . import board as board_mod
from . import config, llm as llm_mod

KIND_SUBJECT_DROPPED = "subject_dropped"
KIND_SHEET_VS_SCRIPT = "sheet_vs_script"
KIND_EMPTY_BINDS = "empty_binds"
KIND_UNBROKEN_TAKE = "unbroken_take"
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
                        "description": (
                            "1-based beat number when the finding is about a beat; omit for "
                            "bible/design."
                        ),
                    },
                    "design": {
                        "type": "string",
                        "description": (
                            "Staging design id when the finding is about a sheet; omit otherwise."
                        ),
                    },
                    "field": {
                        "type": "string",
                        "description": (
                            "Which stored field to edit: action, blocking, asset_prompt, "
                            "scene, panel, note, style_bible, source."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": [
                            KIND_SUBJECT_DROPPED,
                            KIND_SHEET_VS_SCRIPT,
                            KIND_EMPTY_BINDS,
                            KIND_UNBROKEN_TAKE,
                            KIND_SOFT,
                        ],
                    },
                    "problem": {
                        "type": "string",
                        "description": "What dropped or disagreed, in one sentence.",
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

# A group of identical puppets, named as a group. "the three" is a count, not a flock word,
# and is handled with the remainder clause rather than here.
_GROUP_RE = re.compile(
    r"\b("
    r"flock|swarm|herd|pack|school|gaggle|drove|bevy|murmuration|"
    r"chorus of|host of|group of|crowd of|line of|"
    r"several|many |multiple |copies of"
    r")\b",
    re.IGNORECASE,
)

# A beat that has replaced the group with one individual, without saying it is a member of
# the same group. "a close-up of one crane from the flock" keeps the remainder; "a single
# crane" does not.
_SINGULAR_RE = re.compile(
    r"\b("
    r"a single|the single|a lone|the lone|one lone|"
    r"a solitary|the solitary"
    r")\b",
    re.IGNORECASE,
)

# The rest of the group is still accounted for -- off-frame, remaining, named as the
# same flock. A close-up of one member is then coverage, not a new protagonist.
# "behind it" is ordinary blocking for scenery (clouds behind the bird) and is not an
# account of the rest of the flock -- that false remainder hid the drop on beat 3 of
# the flock reel.
_REMAINDER_RE = re.compile(
    r"\b("
    r"rest of (?:the )?(?:flock|pack|herd|swarm|group|line)|"
    r"other (?:birds?|cranes?|members?|ones)|"
    r"remaining|off[- ]frame|behind them|"
    r"from the (?:flock|pack|herd|swarm|group)|"
    r"of the same (?:flock|pack|herd|swarm|group)|"
    r"copies (?:still |remain)"
    r")\b",
    re.IGNORECASE,
)

# Interview / concept language for one continuous take across the whole reel.
_ONE_TAKE_RE = re.compile(
    r"\b("
    r"unbroken(?: chained)? take|"
    r"one long (?:unbroken |chained )?take|"
    r"1 long unbroken|"
    r"chained take for the entire|"
    r"entire film"
    r")\b",
    re.IGNORECASE,
)

_SOFT_SYSTEM = (
    "You audit a stop-motion reel for cast and set continuity across beats. The same "
    "recurring subjects persist for the whole film. A close-up is coverage of a member of "
    "a group already in the film, not a replacement protagonist. A design sheet of one "
    "puppet plus a count in blocking is how identical multiples work -- never a sheet note "
    "that says 'single' when the script's subject is a flock. Report only real drops. Pass "
    "silently when the roster holds -- an empty findings list is correct."
)


def audit(board: board_mod.Board, *, deep: bool = True,
          llm: llm_mod.LLM | None = None) -> list[dict[str, Any]]:
    """Every roster finding on this board, deterministic first.

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
        return "cast audit: clean — the roster holds across every beat."
    lines = [f"cast audit: {len(findings)} finding(s)"]
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


def _beat_text(beat: dict) -> str:
    return " ".join(
        str(beat.get(key) or "")
        for key in ("scene", "action", "blocking", "asset_prompt", "panel")
    )


def _deterministic(board: board_mod.Board) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    found.extend(_sheet_vs_script_findings(board))
    found.extend(_empty_bind_findings(board))
    found.extend(_subject_dropped_findings(board))
    found.extend(_unbroken_take_findings(board, found))
    return found


def _sheet_vs_script_findings(board: board_mod.Board) -> list[dict[str, Any]]:
    """A character note that says 'single' while the film's subject is a group."""
    reel_text = " ".join((
        str(board.data.get("concept") or ""),
        str(board.data.get("style_bible") or ""),
        *(_beat_text(beat) for beat in board.ordered_beats()),
    ))
    if not _GROUP_RE.search(reel_text):
        return []
    found: list[dict[str, Any]] = []
    for entry in board.staging:
        if board.stage_kind(entry) != config.STAGE_CHARACTER:
            continue
        note = " ".join(str(board.stage_field(entry, "note")).split())
        if not re.search(r"\bsingle\b", note, re.IGNORECASE):
            continue
        name = board.stage_name(entry)
        found.append(_finding(
            kind=KIND_SHEET_VS_SCRIPT,
            field="note",
            design=str(entry.get("id") or ""),
            problem=(
                f"Design {name!r} note says 'single' while the concept, bible or beats name "
                "a group of that character -- a flock that the sheet has already forbidden."
            ),
            fix=(
                f"Rewrite the note as look-only identity for {name}: one puppet, reused. "
                "Count and arrangement belong in blocking ('five copies in the upper-right "
                "third'), never in the sheet."
            ),
        ))
    return found


def _empty_bind_findings(board: board_mod.Board) -> list[dict[str, Any]]:
    """Blocking names a designed character or set, but the beat binds nothing."""
    if not board.staging:
        return []
    found: list[dict[str, Any]] = []
    for beat in board.ordered_beats():
        n = int(beat["n"])
        text = _beat_text(beat)
        if not text.strip():
            continue
        bound = {str(entry.get("id")) for entry in board.bound_staging(n)}
        named: list[str] = []
        for entry in board.staging:
            entry_id = str(entry.get("id") or "")
            name = board.stage_name(entry)
            if not name or name == "an unnamed design":
                continue
            if _names_design(text, name, entry_id) and entry_id not in bound:
                named.append(name)
        # The wipe this module exists to catch: the beat talks about the cast and binds
        # nobody, even when the name match is fuzzy (a "flock" with no sheet name in frame).
        if not bound and _GROUP_RE.search(text) and any(
            board.stage_kind(entry) == config.STAGE_CHARACTER for entry in board.staging
        ):
            found.append(_finding(
                kind=KIND_EMPTY_BINDS,
                beat=n,
                field="staging",
                problem=(
                    "This beat names a group in blocking or action but binds no design -- "
                    "bind_designs was called with an empty list, so the sheets never reach "
                    "the still or the clip."
                ),
                fix=(
                    "Call bind_designs with every character and set this shot still needs. "
                    "An empty list wipes prior binds and is almost never what you want."
                ),
            ))
            continue
        if named:
            found.append(_finding(
                kind=KIND_EMPTY_BINDS,
                beat=n,
                field="staging",
                problem=(
                    f"This beat's prose names {', '.join(named)} but does not bind "
                    f"{'that design' if len(named) == 1 else 'those designs'} -- the render "
                    "is told in words when it could have been shown the sheet."
                ),
                fix=(
                    "bind_designs replaces the list, so send every design this shot still "
                    f"needs, including {', '.join(named)}."
                ),
            ))
    return found


def _names_design(text: str, name: str, entry_id: str) -> bool:
    if re.search(rf"@stage:{re.escape(entry_id)}\b", text, re.IGNORECASE):
        return True
    pattern = re.escape(name)
    return bool(re.search(rf"\b{pattern}\b", text, re.IGNORECASE))


def _subject_dropped_findings(board: board_mod.Board) -> list[dict[str, Any]]:
    """A later beat replaces a group subject with one individual of the same cast."""
    beats = list(board.ordered_beats())
    group_beats = [int(beat["n"]) for beat in beats if _GROUP_RE.search(_beat_text(beat))]
    if not group_beats:
        return []
    first_group = min(group_beats)
    found: list[dict[str, Any]] = []
    for beat in beats:
        n = int(beat["n"])
        if n <= first_group:
            continue
        text = _beat_text(beat)
        if not _SINGULAR_RE.search(text):
            continue
        if _REMAINDER_RE.search(text) or _GROUP_RE.search(text):
            continue
        found.append(_finding(
            kind=KIND_SUBJECT_DROPPED,
            beat=n,
            field="blocking",
            problem=(
                f"Beat {first_group} names a flock/group as the subject, but beat {n} names "
                "a single individual of that cast with no remaining copies and no off-frame "
                "account -- a different film, not a closer camera."
            ),
            fix=(
                "Keep the group in frame, or block this as a close-up of one member of the "
                "same group with the rest still bound and named off-frame ('the rest of the "
                "flock holds in the upper third'). Do not invent a new protagonist. Bind the "
                "same character sheet; put the count in blocking."
            ),
        ))
    return found


def _unbroken_take_findings(board: board_mod.Board,
                            already: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A one-take request plus a mid-reel cut that also changes the subject."""
    if not _asked_one_take(board):
        return []
    dropped = {item.get("beat") for item in already
               if item.get("kind") == KIND_SUBJECT_DROPPED}
    found: list[dict[str, Any]] = []
    for beat in board.ordered_beats():
        n = int(beat["n"])
        if n == 1:
            continue
        source = board.source_for(beat)
        if source not in (board_mod.SOURCE_REFERENCE, board_mod.SOURCE_ASSET):
            continue
        if n not in dropped:
            continue
        found.append(_finding(
            kind=KIND_UNBROKEN_TAKE,
            beat=n,
            field="source",
            problem=(
                "The director asked for one unbroken chained take, but this beat is a cut "
                "that also changes the subject -- a new protagonist and a new setup in a "
                "film that was supposed to be one shot."
            ),
            fix=(
                "Keep the same subjects and continue the take (chain or bridge), or keep "
                "the cut only as coverage of a member of the same group, with the rest of "
                "the group still in the film. Continuity owns the join; mise owns who is "
                "in frame."
            ),
        ))
    return found


def _asked_one_take(board: board_mod.Board) -> bool:
    chunks = [str(board.data.get("concept") or "")]
    for turn in board.data.get("chat") or []:
        if not isinstance(turn, dict):
            continue
        chunks.append(str(turn.get("text") or ""))
        for question in turn.get("questions") or []:
            if isinstance(question, dict):
                chunks.append(str(question.get("prompt") or ""))
    return bool(_ONE_TAKE_RE.search(" ".join(chunks)))


def _soft(board: board_mod.Board, speaker: llm_mod.LLM) -> list[dict[str, Any]]:
    digest = _board_text(board)
    result = speaker.structured(
        [
            {"role": "system", "content": _SOFT_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Audit this reel for cast and set drops across beats. Focus on: a flock "
                    "or group that becomes one individual; a character sheet that says "
                    "'single' while the script's subject is plural; a beat that names a "
                    "design but binds none; a one-take request broken by a cut that also "
                    "changes who the film is about.\n\n"
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
            field=str(item.get("field") or "blocking"),
            problem=problem,
            fix=str(item.get("fix") or "").strip(),
            beat=int(item["beat"]) if item.get("beat") is not None else None,
            design=str(item["design"]) if item.get("design") else None,
        ))
    return cleaned


def _board_text(board: board_mod.Board) -> str:
    lines = [
        f"TITLE: {board.data.get('title') or board.slug}",
        f"CONCEPT: {(board.data.get('concept') or '').strip()}",
        f"STYLE BIBLE: {(board.data.get('style_bible') or '').strip()}",
        "DESIGNS:",
    ]
    for entry in board.staging:
        lines.append(
            f"- {entry.get('id')} ({entry.get('kind')}) {board.stage_name(entry)}: "
            f"note={(entry.get('note') or '')}"
        )
    lines.append("BEATS:")
    for beat in board.ordered_beats():
        n = beat["n"]
        bound = ", ".join(board.stage_name(entry) for entry in board.bound_staging(n)) or "none"
        lines.append(
            f"#{n} source={board.source_for(beat)} binds={bound}\n"
            f"  scene: {beat.get('scene') or ''}\n"
            f"  action: {beat.get('action') or ''}\n"
            f"  blocking: {beat.get('blocking') or ''}\n"
            f"  asset_prompt: {beat.get('asset_prompt') or ''}\n"
            f"  panel: {beat.get('panel') or ''}"
        )
    return "\n".join(lines)
