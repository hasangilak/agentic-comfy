"""A skill: one agent's system prompt and its settings, as a file rather than as code.

`agent.SYSTEM` is a module constant because it is part of a loop that was written around it.
The three agents in `tools.py` are the opposite case -- the loop is generic and the prompt is
the whole of what makes one agent different from another -- so the prompt lives in
`skills/<name>/SKILL.md` and is read off disk. The payoff is that changing what the
storyboarder is told does not need a Python edit, and with `studio.py` being a long-lived
process, does not need a restart either.

A file is:

    ---
    name: storyboarder
    description: Designs the cast and sets, then sketches a panel per shot.
    model: gemini-3.7-flash
    think: false
    temperature: 0.4
    max_rounds: 12
    tools: [read_board, add_design, draw_design, write_panels, draw_panels]
    ---
    You are the storyboard artist for ...

    {{MEDIUM}}

Two things about that shape are decisions rather than conveniences:

- **The frontmatter is read by 40 lines here, not by PyYAML.** The schema below is closed and
  flat -- scalars plus one list -- so a general parser would buy only the ability to write
  frontmatter this schema then rejects. Against that: four PEP-723 entry points would each
  resolve and download PyYAML on every `uv run`. The cost is stated rather than hidden: no
  nested structures, which is why `schema` names a dotted path instead of inlining one.
- **Nothing per-run goes in the body.** The board digest, the beat list, the director's note
  are all handed to `runtime.run` as the user turn, which is the rule `agent.turn` already
  follows -- `SYSTEM` is static there and the board goes in the question. That is what makes
  caching a rendered skill on its mtime sound: two runs against different boards share a
  system prompt because the system prompt says nothing about a board.

Placeholders are resolved from the constants that already exist. A skill that restated the
rules of the medium in its own words would be the drift `agent.MEDIUM`'s comment and
`planner.py`'s docstring both exist to prevent, so `{{MEDIUM}}` splices the one copy in.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import config


class SkillError(RuntimeError):
    """A skill file that cannot be used, said in a sentence a director can act on.

    Carries `.status` like `PanelsError` and `StagingError` do, so an API route answers with
    the sentence and the right code rather than a 500 and a traceback. A skill named by a
    request that does not exist is the user's typo (404); a skill that exists and is malformed
    is the studio's problem (500).
    """

    def __init__(self, message: str, status: int = 500) -> None:
        super().__init__(message)
        self.status = status


# The placeholder table, as callables rather than values. `{{BRIEF}}` reads a 20 KB file off
# disk and `planner.template()` raises `NoTemplate` when it is missing -- deferring that to
# render time means an absent brief is an error about the skill that wanted it, at the moment
# it wanted it, rather than an import failure in a module that never uses it.
def _placeholders() -> dict[str, Callable[[], str]]:
    from . import agent, board as board_mod, panels, planner

    return {
        "MEDIUM": lambda: agent.MEDIUM,
        "MENTION_NOTE": lambda: config.MENTION_NOTE,
        "BRIEF": planner.template,
        "SHOT_GRAMMAR": lambda: panels.SHOT_GRAMMAR,
        "SOURCES": lambda: ", ".join(board_mod.SOURCES),
        "STAGE_KINDS": lambda: ", ".join(config.STAGE_KINDS),
        "BEAT_LENGTHS": lambda: " or ".join(f"{n:g}" for n in config.BEAT_LENGTHS),
        "MAX_REF_IMAGES": lambda: str(config.MAX_REF_IMAGES),
        "MAX_STILL_REFS": lambda: str(config.MAX_STILL_REFS),
        "MAX_STAGE_SHEETS": lambda: str(config.MAX_STAGE_SHEETS),
        "CHAR_SHEET": lambda: config.CHAR_SHEET_LAYOUT,
    }


# Every key a SKILL.md may set, with the type it is read as. A key outside this table is an
# error rather than an ignored line: a typo in `max_rounds` that silently kept the default is
# the kind of fault that only shows up as a loop stopping early on a long run.
_SCHEMA: dict[str, str] = {
    "name": "str",
    "description": "str",
    "model": "str",
    "think": "bool",
    "temperature": "float",
    "max_rounds": "int",
    "tools": "list",
    "schema": "str",
}
_REQUIRED = ("name", "description")


@dataclass(frozen=True)
class Skill:
    """One agent's prompt and settings, rendered and validated."""

    name: str
    description: str
    system: str
    model: str | None
    think: bool
    temperature: float | None
    max_rounds: int
    tools: tuple[str, ...]
    schema: dict | None
    path: Path


def directory() -> Path:
    return config.SKILLS_DIR


def names() -> list[str]:
    """Every skill on disk, alphabetically. A directory without a SKILL.md is not one."""
    root = directory()
    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.iterdir()
                  if entry.is_dir() and (entry / "SKILL.md").is_file())


# Cached on (path, mtime) rather than on the name alone. The whole reason the prompt is a file
# is so it can be edited against a running studio; a cache keyed on the name would make that
# need a restart, and no cache at all would re-read and re-render the 20 KB brief on every run.
_CACHE: dict[Path, tuple[float, Skill]] = {}


def load(name: str) -> Skill:
    """The skill by that name, read, rendered and validated.

    Tool names are NOT checked here. `skills.py` does not know what a tool is -- `tools.py`
    imports this module and not the other way round -- so the toolbox check belongs to
    `runtime.build`, which is the one place a skill and a toolbox meet.
    """
    clean = (name or "").strip()
    if not clean or "/" in clean or clean.startswith("."):
        raise SkillError(f"{name!r} is not a skill name.", status=404)
    path = directory() / clean / "SKILL.md"
    if not path.is_file():
        known = ", ".join(names()) or "none"
        raise SkillError(f"there is no skill called {clean!r}. Known: {known}.", status=404)
    stamp = path.stat().st_mtime
    cached = _CACHE.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    skill = _parse(path, clean)
    _CACHE[path] = (stamp, skill)
    return skill


def catalogue() -> list[dict]:
    """Every skill, for `crew.py --list` and `GET /api/agents`.

    Loads each one, which means a broken file is reported by the cheapest command there is
    rather than by the first run that needed it. A single bad skill does not hide the others.
    """
    found = []
    for name in names():
        try:
            skill = load(name)
        except SkillError as bad:
            found.append({"name": name, "error": str(bad)})
            continue
        found.append({"name": skill.name, "description": skill.description,
                      "model": skill.model or config.TEXT_MODEL, "think": skill.think,
                      "temperature": skill.temperature, "max_rounds": skill.max_rounds,
                      "tools": list(skill.tools), "path": str(skill.path)})
    return found


def render(body: str, extra: dict[str, str] | None = None) -> str:
    """Substitute every `{{NAME}}` from the placeholder table.

    An unknown placeholder is an error naming it and listing what exists. Leaving it in place
    is the worse answer by a distance: a model handed a literal `{{CAST}}` reads the braces as
    something it is supposed to fill in, and answers about a variable rather than about a reel.
    """
    table: dict[str, Callable[[], str]] = dict(_placeholders())
    for key, value in (extra or {}).items():
        table[key] = (lambda held=value: held)
    out: list[str] = []
    rest = body
    while True:
        start = rest.find("{{")
        if start < 0:
            out.append(rest)
            break
        end = rest.find("}}", start)
        if end < 0:
            out.append(rest)
            break
        out.append(rest[:start])
        key = rest[start + 2:end].strip()
        maker = table.get(key)
        if maker is None:
            raise SkillError(
                f"unknown placeholder {{{{{key}}}}}. Available: "
                f"{', '.join(sorted(table))}."
            )
        out.append(str(maker()))
        rest = rest[end + 2:]
    return "".join(out)


def _parse(path: Path, expected: str) -> Skill:
    text = path.read_text()
    fields, body = _split(path, text)
    for key in _REQUIRED:
        if not str(fields.get(key) or "").strip():
            raise SkillError(f"{path}: missing required key {key!r}.")
    if fields["name"] != expected:
        raise SkillError(
            f"{path}: name is {fields['name']!r} but the directory is {expected!r}. "
            "They have to agree -- the directory is how the skill is addressed."
        )
    if not body.strip():
        raise SkillError(f"{path}: the body is empty, so this skill has no system prompt.")
    rounds = int(fields.get("max_rounds") or config.AGENT_MAX_ROUNDS)
    if rounds < 1:
        raise SkillError(f"{path}: max_rounds is {rounds}; a loop needs at least one round.")
    return Skill(
        name=str(fields["name"]),
        description=str(fields["description"]),
        system=render(body).strip(),
        model=str(fields["model"]) if fields.get("model") else None,
        think=bool(fields.get("think") or False),
        temperature=(float(fields["temperature"])
                     if fields.get("temperature") is not None else None),
        max_rounds=rounds,
        tools=tuple(str(name) for name in (fields.get("tools") or [])),
        schema=_schema(path, fields.get("schema")),
        path=path,
    )


def _split(path: Path, text: str) -> tuple[dict[str, Any], str]:
    """The frontmatter block and the body, from a file that has to open with `---`."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError(f"{path}: a skill has to open with a --- frontmatter fence.")
    try:
        closing = next(i for i, line in enumerate(lines[1:], start=1)
                       if line.strip() == "---")
    except StopIteration:
        raise SkillError(f"{path}: the frontmatter fence is never closed.") from None
    return _fields(path, lines[1:closing]), "\n".join(lines[closing + 1:])


def _fields(path: Path, lines: list[str]) -> dict[str, Any]:
    """`key: value` pairs, typed by the schema above.

    Not YAML, and the error messages say so. What is supported: a comment line, a blank line,
    `key: value`, `key: [a, b]`, and a block list of `  - item` lines under a bare `key:`.
    """
    fields: dict[str, Any] = {}
    key: str | None = None
    for offset, raw in enumerate(lines, start=2):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.lstrip().startswith("- "):
            if key is None or _SCHEMA.get(key) != "list":
                raise SkillError(f"{path}:{offset}: a '- ' item with no list key above it.")
            fields.setdefault(key, []).append(_scalar(line.lstrip()[2:].strip()))
            continue
        if ":" not in line:
            raise SkillError(
                f"{path}:{offset}: {line.strip()!r} is not `key: value`, `key: [a, b]` or "
                "a `  - item` line."
            )
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        kind = _SCHEMA.get(key)
        if kind is None:
            raise SkillError(
                f"{path}:{offset}: unknown key {key!r}. Known: {', '.join(_SCHEMA)}."
            )
        if kind == "list":
            fields[key] = _list(path, offset, value)
            continue
        if not value:
            raise SkillError(f"{path}:{offset}: {key!r} has no value.")
        fields[key] = _typed(path, offset, key, kind, value)
        key = None
    return fields


def _list(path: Path, offset: int, value: str) -> list:
    if not value:
        return []  # a block list follows; the `- ` lines append to it
    if not (value.startswith("[") and value.endswith("]")):
        raise SkillError(
            f"{path}:{offset}: a list is either `[a, b]` on this line or `  - item` lines "
            "below it."
        )
    inner = value[1:-1].strip()
    return [_scalar(part.strip()) for part in inner.split(",") if part.strip()]


def _typed(path: Path, offset: int, key: str, kind: str, value: str) -> Any:
    plain = _scalar(value)
    if kind == "bool":
        if isinstance(plain, bool):
            return plain
        raise SkillError(f"{path}:{offset}: {key!r} wants true or false, got {value!r}.")
    if kind == "int":
        try:
            return int(str(plain))
        except ValueError:
            raise SkillError(f"{path}:{offset}: {key!r} wants a whole number, "
                             f"got {value!r}.") from None
    if kind == "float":
        try:
            return float(str(plain))
        except ValueError:
            raise SkillError(f"{path}:{offset}: {key!r} wants a number, "
                             f"got {value!r}.") from None
    return str(plain)


def _scalar(value: str) -> Any:
    """One value, unquoted and typed as far as a flat frontmatter can go."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "none", "~"):
        return None
    return value


def _schema(path: Path, dotted: Any) -> dict | None:
    """The JSON Schema a skill names, imported rather than inlined.

    A path and not an inline object because `planner.PLAN_SCHEMA` and `panels.PANEL_SCHEMA`
    carry a property-ORDER lesson in their comments -- a structured decode follows the order
    the properties are declared in, and `REVIEW_SCHEMA` moved a field to the end because of it.
    A second copy in frontmatter is exactly the drift `planner.py`'s docstring exists to stop.
    """
    if not dotted:
        return None
    text = str(dotted)
    module_name, _, attribute = text.partition(":")
    if not attribute:
        raise SkillError(f"{path}: schema {text!r} needs the form 'package.module:NAME'.")
    import importlib

    try:
        module = importlib.import_module(module_name)
        found = getattr(module, attribute)
    except (ImportError, AttributeError) as missing:
        raise SkillError(f"{path}: cannot resolve schema {text!r}: {missing}") from missing
    if not isinstance(found, dict):
        raise SkillError(f"{path}: schema {text!r} is a {type(found).__name__}, "
                         "not a JSON Schema object.")
    return found


def warm() -> float:
    """Load every skill and return how long it took. `crew.py --list` is this plus printing."""
    started = time.monotonic()
    for name in names():
        load(name)
    return time.monotonic() - started
