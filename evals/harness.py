# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow", "httpx", "numpy", "scipy"]
# ///
"""Golden-board eval for the crew harness. Calls no model and spends no GPU.

    uv run evals/harness.py
    make harness

Loads every skill (placeholders, tools, schemas), checks `next_stage` on three fixture
boards, dry-runs the next phase's prompt without sending it, asserts that naming then clearing
an envelope / act leaves fingerprints byte-identical, asserts the ref2va prompt is MiniMax's
six-part format and the keyframe concatenation is unchanged, asserts Direct this shot's
system prompt holds the H3 action rules and that building its user turn does not throw,
asserts pose_need is 1/2/3 (pin 9 still fills), asserts writer copy no longer teaches a
nine-pose fill, asserts character retention names the sheet note, restores a persisted
render job, and asserts that an ungated stage whose every member 429s does not stamp phases
done.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paperreel import agent, board as board_mod, config, crew, llm as llm_mod, panels, planner, runtime, skills


FIXTURES = Path(__file__).resolve().parent / "harness"

EXPECT_STAGE = {
    "golden-draft": "script",
    "golden-scripted": "storyboard",
    "golden-ready": None,
}


def load_fixture(name: str) -> board_mod.Board:
    path = FIXTURES / name / "storyboard.json"
    return board_mod.Board(slug=name, path=path, data=json.loads(path.read_text()))


def fail(message: str) -> None:
    print(f"FAIL  {message}", file=sys.stderr)
    raise SystemExit(1)


def check_skills() -> None:
    rows = skills.catalogue()
    if not rows:
        fail(f"no skills in {skills.directory()}")
    bad = [row for row in rows if "error" in row]
    if bad:
        fail("; ".join(f"{row['name']}: {row['error']}" for row in bad))
    for row in rows:
        runtime.build(row["name"])
    print(f"ok    {len(rows)} skills load and build")


def check_stages() -> None:
    for name, expected in EXPECT_STAGE.items():
        board = load_fixture(name)
        got = crew.next_stage(board)
        if got != expected:
            fail(f"{name}: next_stage is {got!r}, expected {expected!r}")
        summary = crew.plan_summary(board)
        if summary["stage"] != expected:
            fail(f"{name}: plan_summary.stage is {summary['stage']!r}, expected {expected!r}")
        print(f"ok    {name}: waiting on {got or 'nothing (render only)'}")


def check_dry_run() -> None:
    board = load_fixture("golden-scripted")
    phase = crew.awaiting_phase(board) or "extract"
    names = crew.cast_for_phase(phase, board)
    for name in names:
        built = runtime.build(name, board=board)
        text = runtime.preview(built, "dry-run", prelude=crew.prelude(board))
        if "{{" in built.skill.system:
            fail(f"{name}: unresolved placeholder in system prompt")
        if "THE BOARD AS IT IS RIGHT NOW" not in text:
            fail(f"{name}: dry-run prompt is missing the board prelude")
    print(f"ok    dry-run {phase} ({', '.join(names)}) prints, unsent")


def check_fingerprints() -> None:
    board = load_fixture("golden-scripted")
    beat = board.beat(1)
    before = board.own_fingerprint(beat)
    render_before = board.render_fingerprint(beat)
    digest_before = agent.board_digest(board)

    config.write_envelope(board.data, config.DEFAULT_ENVELOPE)
    if board.own_fingerprint(beat) != before or board.render_fingerprint(beat) != render_before:
        fail("writing envelope=reel changed a fingerprint")
    config.write_envelope(board.data, config.ENVELOPE_FILM)
    if board.own_fingerprint(beat) != before or board.render_fingerprint(beat) != render_before:
        fail("writing envelope=film changed a fingerprint -- it must not, it is not a render input")
    config.write_envelope(board.data, config.DEFAULT_ENVELOPE)

    entry = board.add_act("Open")
    board.bind_act(1, entry["id"])
    board.data["continuity_notes"] = "the frog has not yet spoken"
    if board.own_fingerprint(beat) != before or board.render_fingerprint(beat) != render_before:
        fail("acts / continuity notes changed a fingerprint")
    if agent.board_digest(board) == digest_before:
        fail("a board with acts should change the digest even though fingerprints stay put")
    board.bind_act(1, None)
    board.data.pop("acts", None)
    board.data.pop("continuity_notes", None)
    if board.own_fingerprint(beat) != before:
        fail("clearing acts did not restore the fingerprint")
    if agent.board_digest(board) != digest_before:
        fail("clearing acts/notes did not restore the digest")
    print("ok    envelope/acts/notes are digest-visible and fingerprint-invisible")


def check_compact_digest() -> None:
    board = load_fixture("golden-scripted")
    data = copy.deepcopy(board.data)
    for index in range(5, config.DIGEST_BEAT_DETAIL + 2):
        data["beats"].append({
            "n": index,
            "scene": f"Beat {index} pond, locked-off.",
            "action": "Nothing moves.",
            "source": "reference",
            "seconds": 5.0,
        })
    fat = board_mod.Board(slug=board.slug, path=board.path, data=data)
    digest = agent.board_digest(fat)
    if "summarised" not in digest:
        fail("a board past DIGEST_BEAT_DETAIL did not compact")
    if "  scene:" in digest:
        fail("a compacted digest still spelled every scene line")
    print(f"ok    digest compacts at {config.DIGEST_BEAT_DETAIL + 1} beats")


class DeadLLM:
    """Raises on every model call. `tool` still has to speak Gemini's dialect so build works."""

    def chat(self, *args, **kwargs):
        raise llm_mod.LLMError("credits depleted (eval stub)")

    def structured(self, *args, **kwargs):
        raise llm_mod.LLMError("credits depleted (eval stub)")

    def text(self, *args, **kwargs):
        raise llm_mod.LLMError("credits depleted (eval stub)")

    def tool(self, *args, **kwargs):
        from paperreel import gemini
        return gemini.tool(*args, **kwargs)

    def calls_of(self, message):
        return []

    def answered(self, message, results):
        return []

    def encode(self, path):
        return ""

    def health(self):
        return None

    def available(self):
        return False


def check_failed_phase_not_done() -> None:
    """A 429 must not stamp extract/panels/sheets/seams/lock done. Measured 2026-08-17."""
    slug = "evals-harness-fail"
    dest = board_mod.reels_dir() / slug
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "golden-scripted" / "storyboard.json", dest / "storyboard.json")
    try:
        board = board_mod.Board.load(slug)
        crew.stage("storyboard", board, llm=DeadLLM(), hooks=runtime.Hooks())
        board = board_mod.Board.load(slug)
        record = crew.crew_record(board)
        if record["done"]:
            fail(f"a stage whose every member failed was marked done: {record['done']}")
        if record["awaiting"] != "extract":
            fail(f"awaiting is {record['awaiting']!r}, expected extract")
        if crew.next_stage(board) != "storyboard":
            fail("next_stage moved off storyboard after a failed ungated run")
        print("ok    a failed ungated stage does not stamp phases done")
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def check_jobs_persist() -> None:
    import time

    from paperreel.jobs import Runner

    tmp = Path(tempfile.mkdtemp(prefix="paperreel-harness-"))
    original = config.JOBS_PATH
    config.JOBS_PATH = tmp / ".jobs.json"
    slug = "evals-harness-persist"
    dest = board_mod.reels_dir() / slug
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "golden-scripted" / "storyboard.json", dest / "storyboard.json")
    try:
        config.JOBS_PATH.write_text(json.dumps([{
            "id": "deadbeefcafe",
            "kind": "render",
            "slug": slug,
            "detail": {"beats": [1]},
            "queued_at": time.time(),
            "was": "queued",
        }]))
        run = Runner()
        run.register("render", lambda job, _runner: {"ok": True})
        n = run.restore()
        if n != 1:
            fail(f"restore returned {n}, expected 1")
        if "deadbeefcafe" not in run.jobs:
            fail("restored runner is missing the render job")
        print("ok    render jobs persist and restore")
    finally:
        config.JOBS_PATH = original
        shutil.rmtree(dest, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


def check_brief_envelope() -> None:
    from paperreel import planner

    reel = planner.template(None, config.ENVELOPE_REEL)
    film = planner.template(None, config.ENVELOPE_FILM)
    if "<<<LENGTH>>>" in reel or "<<<DURATION>>>" in reel:
        fail("reel brief still has unresolved length seams")
    if "20–60 seconds" not in reel:
        fail("reel brief lost its 20–60s envelope")
    if "2–10 minutes" not in film:
        fail("film brief lost its 2–10 min envelope")
    if "4 × 5s" not in reel:
        fail("reel duration menu missing")
    if "12 × 10s" not in film:
        fail("film duration menu missing")
    print("ok    brief forks on envelope")


def check_reference_prompt() -> None:
    """ref2va is MiniMax's six-part format; keyframe joins stay the old concatenation."""
    labels = (
        "subject_definitions:",
        "summary:",
        "retention_analysis:",
        "detailed_description:",
        "overall_soundscape:",
        "non_diegetic_music:",
    )
    cut = config.build_prompt(
        "The moth lifts a wing.",
        scene="a lamp post at dusk",
    )
    if config.OPEN_CUT not in cut:
        fail("keyframe cut lost OPEN_CUT")
    if "subject_definitions:" in cut:
        fail("keyframe cut grew subject_definitions")
    chain = config.build_prompt(
        "The moth lifts a wing.",
        scene="a lamp post at dusk",
        continues=True,
    )
    if config.OPEN_CONTINUATION not in chain:
        fail("keyframe chain lost OPEN_CONTINUATION")
    if "subject_definitions:" in chain:
        fail("keyframe chain grew subject_definitions")

    ref = config.build_prompt(
        "The moth lifts a wing.",
        scene="a lamp post at dusk",
        identity="a paper moth, ochre wings, one compound eye",
        refs=2,
        ref_notes=[
            "the composition this shot opens on: its set, its framing",
            "this reel's locked cast reference -- it fixes what the characters look like",
        ],
        opens_on=True,
        ref_kinds=[config.REF_KIND_OPENING, config.REF_KIND_CAST],
    )
    for label in labels:
        if label not in ref:
            fail(f"reference prompt missing {label}")
    if config.REF_SUMMARY_PREFIX not in ref:
        fail("reference prompt missing [reference generation]")
    if "opening composition" not in ref:
        fail("reference prompt missing Picture 1 opening exception")
    if "<Picture 1>" not in ref:
        fail("reference prompt lost <Picture 1>")
    if "Image 1" in ref:
        fail("reference prompt used RunDiffusion Image N tags")
    if "non_diegetic_music:\nN/A" not in ref:
        fail("reference prompt missing non_diegetic_music: N/A")

    weak = config.build_prompt(
        "The moth lifts a wing.",
        scene="a lamp post at dusk",
        refs=1,
        ref_notes=["the composition this shot opens on"],
        opens_on=True,
    )
    if "subject_definitions:" not in weak:
        fail("kinds-less reference prompt lost the six-part labels")
    if "<Subject 1>" in weak:
        fail("kinds-less reference prompt minted Subject IDs")

    character = config.build_prompt(
        "Vera raises the lantern.",
        scene="the clearing at dusk",
        refs=2,
        ref_notes=[
            "the composition this shot opens on",
            "Vera, one of this reel's characters -- appearance reference only",
        ],
        opens_on=True,
        ref_kinds=[config.REF_KIND_OPENING, config.REF_KIND_CHARACTER],
    )
    if "<Subject 1>" not in character:
        fail("character sheet did not mint a Subject")
    if "turnaround" not in character:
        fail("character sheet missing region map")
    retention = character.split("retention_analysis:", 1)[1].split(
        "detailed_description:", 1)[0]
    if "Vera" not in retention:
        fail("character sheet note missing from retention_analysis")
    if "Preserve" not in retention:
        fail("character retention missing Preserve")

    carry = config.build_prompt(
        "The moth keeps walking.",
        scene="a lamp post at dusk",
        refs=1,
        ref_notes=["the composition this shot opens on"],
        opens_on=True,
        ref_videos=1,
        hold_video=False,
    )
    if "voice" not in carry.lower() or "dialogue" not in carry.lower():
        fail("carry video missing the no-voice rule")
    if "+ audio reference" in carry:
        fail("prompt claimed audio reference while REF_VIDEO_WITH_AUDIO is off")

    muted = config.build_prompt(
        "The moth lifts a wing.",
        scene="a lamp post at dusk",
        refs=1,
        opens_on=True,
        mute=True,
    )
    if "overall_soundscape:" in muted or "non_diegetic_music:" in muted:
        fail("mute still emitted sound sections")
    print("ok    reference prompt is six-part; keyframe prompt is unchanged")


def check_direct_prompt() -> None:
    """Direct this shot holds the H3 action rules; building the user turn does not throw."""
    text = agent.DIRECT_SYSTEM_TEMPLATE
    for needle in (
        "playback order",
        "5 s",
        "10 s",
        "subject_definitions",
        "overall_soundscape",
        "non_diegetic_music",
        "do not add a pan",
        "The camera never pans",
        "another 5 s",
        "nothing changes",
        "Dialogue does not make",
    ):
        if needle not in text:
            fail(f"DIRECT_SYSTEM_TEMPLATE missing {needle!r}")
    board = load_fixture("golden-scripted")
    beat = board.beat(1)
    messages = agent._direct_messages(board, beat)
    if len(messages) != 2:
        fail(f"direct messages should be system + user, got {len(messages)}")
    user = messages[1]["content"]
    if "A paper frog sits on a pad" not in user:
        fail("direct user turn lost the current action")
    if "10s" not in user:
        fail("direct user turn lost the beat duration")
    agent.directable(board, 1)
    beat["action"] = beat["scene"] = ""
    beat.pop("blocking", None)
    beat.pop("panel", None)
    try:
        agent.directable(board, 1)
        fail("directable accepted a beat with nothing to direct from")
    except agent.DirectError:
        pass
    print("ok    Direct this shot prompt and user turn")


def check_pose_need() -> None:
    """Gemini keyframes follow beat complexity; H3 interpolates the rest."""
    if config.pose_need("The moth lifts a wing.", 5) != 1:
        fail("quiet 5s beat should need one keyframe")
    if config.pose_need("The moth lifts a wing.", 10) != 2:
        fail("10s beat should need opening + landing")
    travel = "she walks from the far left to the far right across the path"
    if not config.is_travel(travel):
        fail("travel fixture is not detected as travel")
    if config.pose_need(travel, 5) != 3:
        fail("travel beat should need three keyframes")
    if config.pose_need(travel, 10) != 3:
        fail("10s travel should stay three, not add a fourth")
    if config.sequence_length(0, wanted=9) != 9:
        fail("pin 9 should fill remaining sockets")
    if config.sequence_length(2, wanted=9) != 7:
        fail("pin 9 should leave room for reserved sheets")
    if config.PANEL_SEQUENCE != 1:
        fail(f"PANEL_SEQUENCE default is {config.PANEL_SEQUENCE}, expected 1")
    board = load_fixture("golden-ready")
    if board.sequence_count(1) != 2:
        fail(f"golden-ready beat 1 is 10s: sequence_count={board.sequence_count(1)}, expected 2")
    if board.sequence_count(2) != 1:
        fail(f"golden-ready beat 2 is 5s: sequence_count={board.sequence_count(2)}, expected 1")
    print("ok    pose_need is 1/2/3; pin 9 fills; golden-ready counts match")


def check_writer_copy() -> None:
    """Crew writers teach the H3 pack; panels stay locked-off; direct_shot is reachable."""
    if "fill the remaining image sockets" in agent.MEDIUM:
        fail("MEDIUM still teaches the nine-pose fill")
    for needle in ("playback order", "ending pose", "interpolates", "another 5 s"):
        if needle not in agent.MEDIUM:
            fail(f"MEDIUM missing {needle!r}")
    brief = planner.template()
    if "fills up to nine image sockets" in brief:
        fail("brief still teaches filling nine sockets")
    for needle in ("another 5 s", "nothing changes", "Dialogue does not make"):
        if needle not in brief:
            fail(f"brief missing {needle!r}")
    action = (planner.PLAN_SCHEMA["properties"]["beats"]["items"]["properties"]
               ["action"]["description"])
    for needle in ("playback order", "ending pose", "another 5 s", "nothing changes"):
        if needle not in action:
            fail(f"PLAN_SCHEMA action missing {needle!r}")
    if "slow push in" in panels.SHOT_GRAMMAR or "pan left" in panels.SHOT_GRAMMAR:
        fail("SHOT_GRAMMAR still invites a camera move")
    if "CAMERA: static" not in panels.SHOT_GRAMMAR:
        fail("SHOT_GRAMMAR lost the locked-off camera rule")
    for needle in ("not looking at the lens", "frame edge cuts", "cropping the base"):
        if needle not in panels.SHOT_GRAMMAR:
            fail(f"SHOT_GRAMMAR missing {needle!r}")
    for name in ("continuity", "director"):
        skill = skills.load(name)
        if "direct_shot" not in skill.tools:
            fail(f"{name} skill missing direct_shot")
    continuity = skills.load("continuity")
    if "split at script" not in continuity.system:
        fail("continuity lost the split-at-script rule for a third gesture")
    print("ok    writer copy is H3 pack; panels static; direct_shot on continuity and director")


def check_gpu_ban() -> None:
    """The crew layer still cannot reach the GPU, including after this feature."""
    import ast

    banned = {"render", "pipeline", "comfy", "modal"}
    for rel in ("paperreel/tools.py", "paperreel/runtime.py", "paperreel/crew.py"):
        tree = ast.parse((ROOT / rel).read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names = [part for part in module.split(".") if part]
                names += [alias.name.split(".")[0] for alias in node.names]
            hit = banned & set(names)
            if hit:
                fail(f"{rel} imports {sorted(hit)} -- GPU ban broken")
    print("ok    crew layer does not import the GPU")


def main() -> int:
    check_skills()
    check_stages()
    check_dry_run()
    check_fingerprints()
    check_compact_digest()
    check_brief_envelope()
    check_reference_prompt()
    check_direct_prompt()
    check_pose_need()
    check_writer_copy()
    check_gpu_ban()
    check_jobs_persist()
    check_failed_phase_not_done()
    print("harness eval: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
