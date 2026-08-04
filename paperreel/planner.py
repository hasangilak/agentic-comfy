"""Script and asset generation through the Antigravity CLI (`agy`).

These calls bill against your Google plan quota, not a metered API key -- there is no
per-token or per-image charge, but there are hard rate limits that refresh on a ~5 hour
window. Image generation has its own quota pool, separate from agent requests, and it
is the scarcer of the two: expect roughly five images per window.

That scarcity is why chaining matters. A chained reel needs exactly one image no matter
how many beats it has.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import config

PLAN_SCHEMA = {
    "type": "object",
    "required": ["title", "style_bible", "beats"],
    "properties": {
        "title": {"type": "string", "description": "short title for the reel"},
        "style_bible": {
            "type": "string",
            "description": (
                "One paragraph fixing the visual identity: the paper-cutout medium, the "
                "exact character (species, colour, markings, proportions), and the palette. "
                "Pasted into every asset prompt so all beats match. Look only, never motion."
            ),
        },
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["n", "scene", "action", "asset_prompt"],
                "properties": {
                    "n": {"type": "integer"},
                    "scene": {"type": "string", "description": "one line on where this beat happens"},
                    "action": {
                        "type": "string",
                        "description": (
                            "What MOVES in this shot, for a locked-off camera. One continuous "
                            "action, no cuts, no camera moves, no new characters entering."
                        ),
                    },
                    "asset_prompt": {
                        "type": "string",
                        "description": (
                            "Prompt for the still opening frame. Vertical 9:16, character "
                            "fully visible, no text or watermarks."
                        ),
                    },
                },
            },
        },
    },
}


class QuotaExhausted(RuntimeError):
    """agy refused because the image or agent quota is spent."""


def agy(prompt: str, *, model: str, schema: dict | None = None,
        cwd: Path, timeout: str = "10m") -> dict | str:
    """Run one non-interactive agy turn; return structured output, or raw text."""
    command = ["agy", "-p", prompt, "--model", model, "--print-timeout", timeout]
    if schema is not None:
        schema_path = cwd / ".agy_schema.json"
        schema_path.write_text(json.dumps(schema))
        command += ["--json-schema", str(schema_path), "--output-format", "json"]
    else:
        command += ["--dangerously-skip-permissions"]

    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"agy failed ({result.returncode}):\n{result.stderr[-2000:]}")
    if schema is None:
        return result.stdout

    payload = json.loads(result.stdout)
    if payload.get("status") != "SUCCESS":
        raise RuntimeError(f"agy returned {payload.get('status')}:\n{result.stdout[-2000:]}")
    return payload["structured_output"]


def plan(concept: str, beats: int, seconds: float, workdir: Path) -> dict:
    prompt = (
        f"Write a {beats}-beat Instagram Reel script. Each beat is one continuous "
        f"{seconds:.0f}-second shot from a locked-off camera, so the whole reel runs about "
        f"{beats * seconds:.0f} seconds.\n\n"
        f"CONCEPT: {concept}\n\n"
        "The medium is handcrafted layered paper-cutout stop-motion. Constraints that make "
        "or break this: the camera never moves or cuts within a beat; only one thing is "
        "animated at a time; no dialogue, no on-screen text; the same character appears in "
        "every beat and must be described identically each time. Write the style_bible first "
        "and reuse its exact wording inside every asset_prompt so the images match. "
        "Return JSON only."
    )
    return agy(prompt, model=config.PLANNER_MODEL, schema=PLAN_SCHEMA, cwd=workdir)


def find_generated_image(stdout: str, since: float) -> Path | None:
    """Locate whatever agy just wrote.

    agy ignores the working directory and picks its own destination, which varies
    between `scratch/` and `brain/<conversation-id>/`. It does print the absolute
    path, so trust that first and fall back to a timestamped sweep.
    """
    suffixes = {".png", ".jpg", ".jpeg", ".webp"}
    for line in reversed(stdout.strip().splitlines()):
        candidate = Path(line.strip().strip("`'\""))
        if candidate.is_absolute() and candidate.suffix.lower() in suffixes and candidate.is_file():
            return candidate
    fresh = [
        p for p in config.AGY_HOME.rglob("*")
        if p.is_file() and p.suffix.lower() in suffixes and p.stat().st_mtime >= since
    ]
    return max(fresh, key=lambda p: p.stat().st_mtime) if fresh else None


def generate_asset(beat: dict, style_bible: str, out_path: Path, workdir: Path) -> Path:
    import time

    from PIL import Image

    since = time.time() - 1
    prompt = (
        "Use your generate_image tool to create exactly ONE image. Do not write code, do not "
        "call an external API, do not ask questions. Use this prompt verbatim:\n\n"
        f"{style_bible} {beat['asset_prompt']} Vertical 9:16 portrait composition, "
        "handcrafted layered paper-cutout art, visible paper grain, soft contact shadows, "
        "no text, no watermarks, no signature.\n\n"
        "After the image is saved, print only its absolute path."
    )
    stdout = str(agy(prompt, model=config.IMAGE_MODEL, schema=None, cwd=workdir))
    found = find_generated_image(stdout, since)
    if found is None:
        lowered = stdout.lower()
        if "resource_exhausted" in lowered or "quota" in lowered or "429" in lowered:
            raise QuotaExhausted(
                f"image quota exhausted (refreshes on a ~5 hour window). Supply "
                f"{out_path.name} by hand, or use chain mode, which needs only beat 1."
            )
        raise RuntimeError(f"agy produced no image file. Its reply was:\n{stdout[-800:]}")

    # agy names its output .png but often writes JPEG bytes, and sometimes .jpg.
    # Normalise, because the compositor reads pixel formats, not extensions.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(found) as raw:
        raw.convert("RGB").save(out_path)
    return out_path
