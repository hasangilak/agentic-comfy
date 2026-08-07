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
                    "scene": {
                        "type": "string",
                        "description": (
                            "One line on where this beat happens and at what scale. Goes into "
                            "the video prompt with the action, so: setting only, never motion, "
                            "and identical wording for beats in one continuous shot."
                        ),
                    },
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
        "and reuse its exact wording inside every asset_prompt so the images match.\n\n"
        "Beat 1 opens on its own still. Every later beat opens on the final frame of the "
        "beat before it -- same place, same camera -- so each action must pick up from "
        "exactly where the previous action ended, as one continuous take. Do not write a "
        "later beat as though it starts from a fresh setup, and do not move the scene "
        "somewhere else between beats. Return JSON only."
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


def generate_asset(beat: dict, style_bible: str, out_path: Path, workdir: Path,
                   *, reference: Path | None = None) -> Path:
    """Generate one beat's opening still.

    `reference` is the board's character reference, passed to the image tool's `ImagePaths`
    so this comes out as a new SHOT of characters that already exist rather than a new
    reading of the description. Without it, a reel whose beats are all hard cuts redesigns
    its cast once per scene -- the same paragraph of text lands differently every time --
    which is the cross-scene inconsistency a style bible alone never fixed.
    """
    import time

    from PIL import Image

    since = time.time() - 1
    # The medium clause is shared with the papercut backend rather than written twice: two
    # generators wording the look differently produce two different-looking reels depending
    # on which one happened to be up when a still was made.
    look = f"{style_bible} {beat['asset_prompt']} {config.ASSET_STYLE_SUFFIX}"
    instructions = [
        "Use your generate_image tool to create exactly ONE image. Do not write code, do "
        "not call an external API, do not ask questions.",
        # AspectRatio defaults to 1:1 and the model was choosing landscape on its own, so
        # asking for 9:16 in the prompt text alone was not enough: a 1600x872 still gets
        # cover-cropped to the 768x1344 grid at render time, which keeps under a third of
        # the width. Composition, subject scale and framing then jumped between cuts
        # whatever the prompt said.
        f'Set AspectRatio to "9:16". The still is rendered on a tall '
        f"{config.GEN_WIDTH}x{config.GEN_HEIGHT} grid and anything squarer is centre-cropped "
        "down to it, throwing away most of the frame.",
    ]
    if reference is not None:
        instructions.append(
            f'Set ImagePaths to ["{reference.resolve()}"]. That image is this reel\'s locked '
            "reference. Reuse the characters in it exactly: same shapes, markings, colours, "
            "paper stock, cut style, outline weight and proportions, and the same art "
            "direction and palette. This is a new shot of characters that already exist, "
            "not a new design of them. Only the setting, framing and pose described below "
            "may differ."
        )
    instructions.append(f"Use this prompt verbatim:\n\n{look}")
    instructions.append("After the image is saved, print only its absolute path.")
    stdout = str(agy("\n\n".join(instructions), model=config.IMAGE_MODEL, schema=None,
                     cwd=workdir))
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
