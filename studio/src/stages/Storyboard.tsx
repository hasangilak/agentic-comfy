import { useState } from "react";
import { api } from "../api";
import { Design, KIND_LOOK } from "../canvas/StagingPanel";
import { JOIN_LOOK } from "../joins";
import { AgentTurns } from "../panels/CrewPanel";
import { nextBinding, sceneList } from "../staging";
import type { Beat, Board, StageEntry, StageKind } from "../types";
import { useBusy, useDraft, useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";
import { StagePage, WaitingOn } from "./parts";
import { BoardMedium } from "./NewReel";

/**
 * Stage two: named roster, then how each shot is framed, then the sheets those shots are of.
 *
 * The crew walks this stage in gated phases — extract (mise names the roster) → panels
 * (storyboard) → sheets (character + environment) → seams (blocking + continuity) → lock
 * (mise looks at the panels next to the sheets) — so the director can approve each lock
 * before anyone draws a still. Manual design / panel controls stay as secondary paths:
 * stages stay separable.
 */
export function Storyboard() {
  const studio = useStudio();
  const board = studio.board!;
  const [sheet, setSheet] = useState(false);
  const [crewBusy, setCrewBusy] = useState(false);

  const writingPanels = useBusy("panel_write", () => true);
  const drawingPanels = useBusy("panel_draw", () => true);
  const crewJob = useBusy("crew", () => true);
  const agentJob = useBusy("agent", () => true);
  const crewRunning = crewJob || agentJob;

  const withText = board.beats.filter((beat) => beat.panel?.trim());
  const written = withText.length;
  const drawn = board.beats.filter((beat) => beat.panel_url).length;
  const total = board.beats.length;
  const done = new Set(board.crew?.done ?? []);
  // Prefer the persisted cursor. Boards that never gated and still lack panels start at extract;
  // once panels exist (manual or crew), do not invent an extract CTA over a finished storyboard.
  const awaiting =
    board.crew?.awaiting ??
    (total && !written && !done.has("extract") ? "extract" : null);
  const step = storyboardStep(done, awaiting, written, total);
  const atExtractGate = done.has("extract") && awaiting === "panels";
  const atSheetsGate = done.has("sheets") && awaiting === "seams";
  const atSeamGate = done.has("seams") && awaiting === "lock";
  const atLockGate =
    awaiting === "lock" ||
    (written === total &&
      total > 0 &&
      done.has("panels") &&
      done.has("sheets") &&
      !done.has("lock") &&
      done.size > 0);

  const picked = board.staging.find((entry) => entry.id === studio.stagingPick) ?? null;

  const writeShots = () => void studio.guard(() => api.writePanels(board.slug));
  const drawPanels = () =>
    void studio.guard(() =>
      api.drawPanels(board.slug, drawn ? withText.map((beat) => beat.n) : undefined),
    );

  const runPhase = (phase: string) => {
    setCrewBusy(true);
    void studio
      .guard(() => api.runCrew(board.slug, { stage: "storyboard", phase }))
      .finally(() => setCrewBusy(false));
  };

  const busy = crewBusy || crewRunning;

  return (
    <StagePage
      stage="storyboard"
      title="Storyboard"
      blurb={`${board.staging.length} designs · ${written}/${total} shots written · ${drawn}/${total} drawn · free`}
      waiting={
        !total ? (
          <WaitingOn>Nothing to storyboard yet — write the script first.</WaitingOn>
        ) : (
          <div className="space-y-2.5">
            <PhaseStepper step={step} done={done} />
            <StoryboardWaiting
              step={step}
              awaiting={awaiting}
              written={written}
              drawn={drawn}
              busy={busy}
              writingPanels={writingPanels}
              drawingPanels={drawingPanels}
              atExtractGate={atExtractGate}
              atSheetsGate={atSheetsGate}
              atSeamGate={atSeamGate}
              atLockGate={atLockGate}
              done={done}
              runPhase={runPhase}
              writeShots={writeShots}
              drawPanels={drawPanels}
            />
          </div>
        )
      }
    >
      <div className="mb-5">
        <BoardMedium />
      </div>
      {atExtractGate ? (
        <ExtractGate board={board} onRerun={() => runPhase("extract")} busy={busy} />
      ) : null}
      {atSheetsGate ? (
        <SheetsGate board={board} onRerun={() => runPhase("sheets")} busy={busy} />
      ) : null}
      {atSeamGate ? <SeamGate board={board} onRerun={() => runPhase("seams")} busy={busy} /> : null}

      <CastStrip board={board} picked={picked} />

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Button onClick={writeShots} disabled={writingPanels || !total}>
              {writingPanels ? "writing…" : written ? "✎ rewrite the shots" : "✎ write the shots"}
            </Button>
            <Button onClick={drawPanels} disabled={drawingPanels || !written}>
              {drawingPanels ? "drawing…" : drawn ? "▦ redraw the panels" : "▦ draw the panels"}
            </Button>
            <div className="ml-auto flex gap-1 rounded-full bg-ink p-0.5 text-[11px]">
              {(
                [
                  [false, "▦ grid"],
                  [true, "🗇 sheet"],
                ] as const
              ).map(([option, label]) => (
                <button
                  key={label}
                  onClick={() => setSheet(option)}
                  className={`rounded-full px-2.5 py-1 transition-colors ${
                    sheet === option ? "bg-solid text-white" : "text-zinc-500 hover:text-zinc-800"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {picked ? (
            <p className="mb-2.5 text-[11px] text-warm">
              Tick the scenes {picked.name} is in — {sceneList(board, picked.id)} so far.{" "}
              <button
                onClick={() => studio.setStagingPick(null)}
                className="underline hover:text-zinc-700"
              >
                done
              </button>
            </p>
          ) : null}

          {sheet ? <ContactSheet board={board} /> : <Grid board={board} picked={picked} />}
        </div>

        <div className="rounded-2xl border border-edge bg-panel">
          {picked ? (
            <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl">
              <Design board={board} entry={picked} layout="column" />
            </div>
          ) : (
            <AddDesign board={board} />
          )}
        </div>
      </div>
    </StagePage>
  );
}

const STORYBOARD_STEPS = [
  { id: "extract", label: "Cast" },
  { id: "panels", label: "Panels" },
  { id: "sheets", label: "Sheets" },
  { id: "seams", label: "Seams" },
  { id: "lock", label: "Lock" },
] as const;

type StoryboardStepId = (typeof STORYBOARD_STEPS)[number]["id"] | "done";

function storyboardStep(
  done: Set<string>,
  awaiting: string | null,
  written: number,
  total: number,
): StoryboardStepId {
  if (awaiting && STORYBOARD_STEPS.some((step) => step.id === awaiting)) {
    return awaiting as (typeof STORYBOARD_STEPS)[number]["id"];
  }
  for (const step of STORYBOARD_STEPS) {
    if (!done.has(step.id)) return step.id;
  }
  if (total && written === total && done.has("lock")) return "done";
  return "extract";
}

function PhaseStepper({ step, done }: { step: StoryboardStepId; done: Set<string> }) {
  return (
    <ol className="flex flex-wrap items-center gap-1 text-[10px]">
      {STORYBOARD_STEPS.map((entry, index) => {
        const here = step === entry.id;
        const finished = done.has(entry.id) && !here;
        return (
          <li key={entry.id} className="flex items-center gap-1">
            {index ? <span className="text-zinc-300">→</span> : null}
            <span
              className={`rounded-full px-2 py-0.5 ${
                here
                  ? "bg-solid text-white"
                  : finished
                    ? "bg-soft text-zinc-500"
                    : "text-zinc-400"
              }`}
            >
              {entry.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function StoryboardWaiting({
  step,
  awaiting,
  written,
  drawn,
  busy,
  writingPanels,
  drawingPanels,
  atExtractGate,
  atSheetsGate,
  atSeamGate,
  atLockGate,
  done,
  runPhase,
  writeShots,
  drawPanels,
}: {
  step: StoryboardStepId;
  awaiting: string | null;
  written: number;
  drawn: number;
  busy: boolean;
  writingPanels: boolean;
  drawingPanels: boolean;
  atExtractGate: boolean;
  atSheetsGate: boolean;
  atSeamGate: boolean;
  atLockGate: boolean;
  done: Set<string>;
  runPhase: (phase: string) => void;
  writeShots: () => void;
  drawPanels: () => void;
}) {
  const studio = useStudio();

  if (atExtractGate) {
    return (
      <WaitingOn
        action={
          <Button tone="primary" onClick={() => runPhase("panels")} disabled={busy}>
            {busy ? "working…" : "Approve cast & continue"}
          </Button>
        }
      >
        The roster is named. Check the chips below, then storyboard against those names —
        sheets are drawn after the panels.
      </WaitingOn>
    );
  }
  if (atSheetsGate) {
    return (
      <WaitingOn
        action={
          <Button tone="primary" onClick={() => runPhase("seams")} disabled={busy}>
            {busy ? "working…" : "Approve sheets & continue"}
          </Button>
        }
      >
        Character and environment sheets are ready. Check them below, then continue to
        blocking and continuity.
      </WaitingOn>
    );
  }
  if (atSeamGate) {
    return (
      <WaitingOn
        action={
          <Button tone="primary" onClick={() => runPhase("lock")} disabled={busy}>
            {busy ? "working…" : "Approve seams & lock"}
          </Button>
        }
      >
        Blocking and seam fixes are on the board. Mise then looks at the panels next to the
        sheets.
      </WaitingOn>
    );
  }
  if (step === "extract" || awaiting === "extract" || (!done.has("extract") && !written)) {
    return (
      <WaitingOn
        action={
          <Button tone="primary" onClick={() => runPhase("extract")} disabled={busy}>
            {busy ? "working…" : "Extract the cast"}
          </Button>
        }
      >
        Name every recurring character and place. Sheets come after the storyboard. You can
        still add designs by hand below.
      </WaitingOn>
    );
  }
  if (step === "panels" || awaiting === "panels") {
    return (
      <WaitingOn
        action={
          <Button tone="primary" onClick={() => runPhase("panels")} disabled={busy}>
            {busy ? "working…" : "Run panels crew"}
          </Button>
        }
      >
        Write and draw the panels against that named roster.
      </WaitingOn>
    );
  }
  if (written && drawn < written && step !== "sheets" && step !== "seams" && step !== "lock") {
    return (
      <WaitingOn
        action={
          <Button tone="primary" onClick={drawPanels} disabled={drawingPanels}>
            {drawingPanels ? "drawing…" : "▦ draw the panels"}
          </Button>
        }
      >
        {written - drawn} shot{written - drawn === 1 ? "" : "s"} written but not sketched. A
        panel conditions the still, not the clip, so this changes no scene&apos;s render state.
      </WaitingOn>
    );
  }
  if (step === "sheets" || awaiting === "sheets") {
    return (
      <WaitingOn
        action={
          <Button tone="primary" onClick={() => runPhase("sheets")} disabled={busy}>
            {busy ? "working…" : "Draw character & environment sheets"}
          </Button>
        }
      >
        Draw character and environment sheets from the names mise minted.
      </WaitingOn>
    );
  }
  if (step === "seams" || awaiting === "seams") {
    return (
      <WaitingOn
        action={
          <Button tone="primary" onClick={() => runPhase("seams")} disabled={busy}>
            {busy ? "working…" : "Run seams crew"}
          </Button>
        }
      >
        Block each frame against the sheets, then a continuity pass on every chain and
        bridge.
      </WaitingOn>
    );
  }
  if (atLockGate || step === "lock") {
    return (
      <WaitingOn
        action={
          <Button tone="primary" onClick={() => runPhase("lock")} disabled={busy}>
            {busy ? "working…" : "Lock cast against panels"}
          </Button>
        }
      >
        Mise looks at the panels next to the sheets — a flock that became one bird is
        caught here, not after the still.
      </WaitingOn>
    );
  }
  if (written && done.has("lock")) {
    return (
      <WaitingOn
        tone="quiet"
        action={<Button onClick={() => studio.goStage("assets")}>→ Assets</Button>}
      >
        Cast and sets are locked. Next is the still each shot opens on.
      </WaitingOn>
    );
  }
  if (written) {
    return (
      <WaitingOn
        tone="quiet"
        action={<Button onClick={() => studio.goStage("assets")}>→ Assets</Button>}
      >
        Every shot is written and drawn. Next is the still each one opens on.
      </WaitingOn>
    );
  }
  return (
    <WaitingOn
      action={
        <Button tone="primary" onClick={writeShots} disabled={writingPanels}>
          {writingPanels ? "writing…" : "✎ write the shots"}
        </Button>
      }
    >
      No shot grammar yet. The model writes the shot size, angle and camera move for every
      scene at once — it has to see them together to vary them.
    </WaitingOn>
  );
}

const EXTRACT_AGENTS = [
  "style-paper-cutout",
  "style-paper-craft",
  "style-claymation",
  "mise-en-scene",
];
const SHEET_AGENTS = ["character-sheet", "set-designer"];
const SEAM_AGENTS = ["mise-en-scene", "coherence", "continuity"];

/** Review the named roster after extract — before panels. Sheets are not drawn yet. */
function ExtractGate({
  board,
  onRerun,
  busy,
}: {
  board: Board;
  onRerun: () => void;
  busy: boolean;
}) {
  const studio = useStudio();
  const total = board.beats.length || 1;
  return (
    <div className="mb-5 space-y-3 rounded-2xl border border-warm/40 bg-warm/5 p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-warm">
          Cast gate
        </span>
        <span className="text-[11px] text-zinc-500">
          Approve the named roster before the storyboard. Sheets come after the panels.
        </span>
        <button
          onClick={onRerun}
          disabled={busy}
          className="ml-auto rounded-lg px-2 py-1 text-[10px] text-zinc-500
            transition-colors hover:bg-hover hover:text-zinc-800 disabled:opacity-40"
        >
          re-run extract
        </button>
      </div>
      <p className="text-[10px] leading-snug text-zinc-400">
        The style bible is written for the medium above. After a change, re-run extract so
        the matching artist rewrites the bible.
      </p>
      <div className="flex flex-wrap gap-2">
        {board.staging.map((entry) => {
          const bound = board.beats.filter((beat) => (beat.staging ?? []).includes(entry.id)).length;
          return (
            <button
              key={entry.id}
              onClick={() => studio.setStagingPick(entry.id)}
              className="flex items-center gap-2 rounded-full border border-edge bg-panel py-1
                pl-1 pr-3 text-left transition-colors hover:border-zinc-400"
            >
              <span className="flex h-7 w-7 items-center justify-center overflow-hidden rounded-full bg-ink text-[12px] text-zinc-400">
                {KIND_LOOK[entry.kind].icon}
              </span>
              <span>
                <span className="block text-[11px] font-medium text-zinc-800">{entry.name}</span>
                <span className="block text-[10px] text-zinc-400">
                  {KIND_LOOK[entry.kind].label} · bound {bound}/{total}
                </span>
              </span>
            </button>
          );
        })}
        {!board.staging.length ? (
          <p className="text-[11px] text-zinc-500">No designs yet — add one by hand or re-run.</p>
        ) : null}
      </div>
      <AgentTurns names={EXTRACT_AGENTS} />
    </div>
  );
}

/** Review sheets and bindings after the sheets phase — before seams run. */
function SheetsGate({
  board,
  onRerun,
  busy,
}: {
  board: Board;
  onRerun: () => void;
  busy: boolean;
}) {
  const studio = useStudio();
  const total = board.beats.length || 1;
  return (
    <div className="mb-5 space-y-3 rounded-2xl border border-warm/40 bg-warm/5 p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-warm">
          Sheets gate
        </span>
        <span className="text-[11px] text-zinc-500">
          Approve the sheets drawn from the named roster before blocking.
        </span>
        <button
          onClick={onRerun}
          disabled={busy}
          className="ml-auto rounded-lg px-2 py-1 text-[10px] text-zinc-500
            transition-colors hover:bg-hover hover:text-zinc-800 disabled:opacity-40"
        >
          re-run sheets
        </button>
      </div>
      <div className="flex flex-wrap gap-3">
        {board.staging.map((entry) => {
          const bound = board.beats.filter((beat) => (beat.staging ?? []).includes(entry.id)).length;
          return (
            <button
              key={entry.id}
              onClick={() => studio.setStagingPick(entry.id)}
              className="flex w-36 flex-col overflow-hidden rounded-xl border border-edge bg-panel
                text-left transition-colors hover:border-zinc-400"
            >
              <div className="aspect-square bg-ink">
                {entry.sheet ? (
                  <img src={entry.sheet} alt="" className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full items-center justify-center text-[20px] text-zinc-300">
                    {KIND_LOOK[entry.kind].icon}
                  </div>
                )}
              </div>
              <div className="space-y-0.5 px-2 py-1.5">
                <div className="truncate text-[11px] font-medium text-zinc-800">{entry.name}</div>
                <div className="text-[10px] text-zinc-400">
                  {KIND_LOOK[entry.kind].label} · bound {bound}/{total}
                </div>
              </div>
            </button>
          );
        })}
        {!board.staging.length ? (
          <p className="text-[11px] text-zinc-500">No designs yet — add one by hand or re-run.</p>
        ) : null}
      </div>
      <AgentTurns names={SHEET_AGENTS} />
    </div>
  );
}

/** Review joins, scene identity and blocking after the seams phase — before lock. */
function SeamGate({
  board,
  onRerun,
  busy,
}: {
  board: Board;
  onRerun: () => void;
  busy: boolean;
}) {
  const studio = useStudio();
  return (
    <div className="mb-5 space-y-3 rounded-2xl border border-warm/40 bg-warm/5 p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-warm">Seam gate</span>
        <span className="text-[11px] text-zinc-500">
          Chain and bridge rows are where a restart jolt shows up — check those first.
        </span>
        <button
          onClick={onRerun}
          disabled={busy}
          className="ml-auto rounded-lg px-2 py-1 text-[10px] text-zinc-500
            transition-colors hover:bg-hover hover:text-zinc-800 disabled:opacity-40"
        >
          re-run seams
        </button>
      </div>
      <div className="space-y-1.5">
        {board.beats.map((beat) => {
          const look = JOIN_LOOK[beat.source];
          const seam = beat.source === "chain" || beat.source === "bridge";
          return (
            <button
              key={beat.n}
              onClick={() => studio.setExpanded(beat.n)}
              className={`flex w-full gap-3 rounded-xl border px-3 py-2 text-left transition-colors
                ${seam ? "border-warm/50 bg-panel" : "border-edge bg-panel hover:border-zinc-400"}`}
            >
              <span className="w-8 shrink-0 text-[11px] font-medium text-zinc-400">{beat.n}</span>
              <span
                className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] ${look.tone}`}
                title={look.hint}
              >
                {look.short}
              </span>
              <div className="min-w-0 flex-1 space-y-0.5">
                <p className="truncate text-[11px] text-zinc-700">
                  <span className="text-zinc-400">scene </span>
                  {beat.scene || "—"}
                </p>
                <p className="truncate text-[11px] text-zinc-600">
                  <span className="text-zinc-400">action </span>
                  {beat.action || "—"}
                </p>
                {beat.blocking ? (
                  <p className="truncate text-[11px] text-zinc-500">
                    <span className="text-zinc-400">in frame </span>
                    {beat.blocking}
                  </p>
                ) : null}
              </div>
            </button>
          );
        })}
      </div>
      <AgentTurns names={SEAM_AGENTS} />
    </div>
  );
}

/**
 * Every design in the reel, as one row of chips.
 *
 * First on the page because it answers the question every panel below assumes: who is in this
 * film. Selecting one is the binding gesture — see `Grid`.
 */
function CastStrip({ board, picked }: { board: Board; picked: StageEntry | null }) {
  const studio = useStudio();
  if (!board.staging.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-[10px] uppercase tracking-wide text-zinc-400">cast &amp; sets</span>
      {board.staging.map((entry) => {
        const on = entry.id === picked?.id;
        return (
          <button
            key={entry.id}
            onClick={() => studio.setStagingPick(on ? null : entry.id)}
            title={`${entry.role} — ${sceneList(board, entry.id)}. Click to bind it across the reel`}
            className={`flex items-center gap-1.5 rounded-full py-0.5 pl-0.5 pr-2.5 text-[11px]
              transition-colors ${on ? "bg-solid text-white" : "bg-soft text-zinc-700 hover:bg-softer"}`}
          >
            <span className="flex h-5 w-5 shrink-0 items-center justify-center overflow-hidden rounded-full bg-ink text-[10px]">
              {entry.sheet ? (
                <img src={entry.sheet} alt="" className="h-full w-full object-cover" />
              ) : (
                KIND_LOOK[entry.kind].icon
              )}
            </span>
            {entry.name}
          </button>
        );
      })}
      <button
        onClick={() => studio.setStagingPick(null)}
        title="free: this makes an entry, it does not draw anything"
        className="rounded-full bg-soft px-2.5 py-1 text-[11px] text-zinc-500 transition-colors hover:bg-softer"
      >
        ＋
      </button>
    </div>
  );
}

/** Mint one design. Shown in the right column whenever nothing is selected. */
function AddDesign({ board }: { board: Board }) {
  const studio = useStudio();
  const [kind, setKind] = useState<StageKind>("character");
  const [name, setName] = useState("");
  const full = board.staging.length >= board.max_staging;

  const add = () => {
    const trimmed = name.trim();
    if (!trimmed || full) return;
    setName("");
    void studio.guard(async () => {
      const created = await api.addStage(board.slug, { kind, name: trimmed });
      // Selected straight away, because the next thing anyone does is say what it looks like.
      studio.setStagingPick(created.id);
      return created;
    });
  };

  return (
    <div className="space-y-2.5 p-3">
      <p className="text-[10px] uppercase tracking-wide text-zinc-400">design something</p>
      <div className="flex gap-1 rounded-full bg-ink p-0.5 text-[10px]">
        {board.stage_kinds.map((option) => (
          <button
            key={option}
            onClick={() => setKind(option)}
            title={KIND_LOOK[option].hint}
            className={`flex-1 rounded-full px-1.5 py-1 transition-colors ${
              kind === option ? "bg-solid text-white" : "text-zinc-500 hover:text-zinc-800"
            }`}
          >
            {KIND_LOOK[option].label}
          </button>
        ))}
      </div>
      <input
        className={inputClass}
        value={name}
        onChange={(event) => setName(event.target.value)}
        onKeyDown={(event) => event.key === "Enter" && add()}
        placeholder={kind === "environment" ? "the clearing" : "Vera"}
      />
      <Button tone="primary" className="w-full" onClick={add} disabled={full || !name.trim()}>
        {full ? `${board.max_staging} is the ceiling` : "add"}
      </Button>
      <p className="text-[10px] leading-relaxed text-zinc-400">
        The name is what the prompts call it, so make it the one the action lines use. A design
        bound to a scene reaches its clip as a numbered picture and its still as the same image —
        so the wolf in scene 2 and the wolf in scene 6 are one wolf rather than two readings of
        the same sentence.
      </p>
    </div>
  );
}

/** The panels as cards, and — while a design is selected — as a checklist for it. */
function Grid({ board, picked }: { board: Board; picked: StageEntry | null }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {board.beats.map((beat) => (
        <ShotCard key={beat.n} beat={beat} board={board} picked={picked} />
      ))}
      {!board.beats.length ? (
        <p className="text-[12px] leading-relaxed text-zinc-400">No scenes yet.</p>
      ) : null}
    </div>
  );
}

function ShotCard({
  beat,
  board,
  picked,
}: {
  beat: Beat;
  board: Board;
  picked: StageEntry | null;
}) {
  const studio = useStudio();
  const look = JOIN_LOOK[beat.source];
  const bound = beat.staging ?? [];
  const drawing = useBusy(
    "panel_draw",
    (detail) => !Array.isArray(detail.beats) || detail.beats.includes(beat.n),
  );
  const writing = useBusy(
    "panel_write",
    (detail) => !Array.isArray(detail.beats) || detail.beats.includes(beat.n),
  );

  const shot = useDraft(beat.panel ?? "", (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { panel: next })),
  );

  const binding = Boolean(picked);
  const on = picked ? bound.includes(picked.id) : false;

  return (
    <div
      className={`overflow-hidden rounded-2xl border bg-panel transition-colors ${
        binding && on ? "border-solid" : "border-edge"
      }`}
    >
      <button
        onClick={() =>
          picked
            ? void studio.guard(() =>
                api.bindStage(board.slug, beat.n, nextBinding(bound, picked.id)),
              )
            : studio.setExpanded(beat.n)
        }
        className="relative block w-full"
        title={
          picked
            ? on
              ? `take ${picked.name} out of scene ${beat.n}`
              : `put ${picked.name} in scene ${beat.n}`
            : "open the whole scene"
        }
      >
        {beat.panel_url ? (
          // object-contain: the framing IS the content of a panel, so cropping one to fill a
          // tile throws away the thing it was drawn to show.
          <img
            src={beat.panel_url}
            alt={`panel ${beat.n}`}
            className="h-52 w-full bg-ink object-contain"
          />
        ) : (
          <div className="flex h-52 w-full items-center justify-center border-b border-dashed border-edge bg-ink text-[11px] text-zinc-400">
            {drawing ? "drawing…" : beat.panel?.trim() ? "not drawn yet" : "no shot written"}
          </div>
        )}
        {binding ? (
          <span
            className={`absolute left-2 top-2 flex h-6 w-6 items-center justify-center rounded-full
              text-[12px] ${on ? "bg-solid text-white" : "bg-panel/90 text-zinc-400"}`}
          >
            {on ? "✓" : "＋"}
          </span>
        ) : null}
      </button>

      <div className="space-y-1.5 px-3 py-2">
        <div className="flex items-center gap-2 text-[10px] text-zinc-400">
          <span className="font-medium text-zinc-600">scene {beat.n}</span>
          <span>{beat.actual_seconds.toFixed(1)}s</span>
          <span className={look.tone} title={look.hint}>
            {look.short}
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            <button
              onClick={() => {
                shot.flush();
                void studio.guard(() => api.drawPanel(board.slug, beat.n));
              }}
              disabled={drawing || writing || !shot.draft.trim()}
              title={
                shot.draft.trim()
                  ? beat.panel_url
                    ? "draw this panel again"
                    : "draw this panel"
                  : "write the shot first — there is nothing for a sketch to be a drawing of"
              }
              className="hover:text-warm disabled:opacity-30"
            >
              ✦
            </button>
            {/* One shot rewritten rather than the whole reel. The default stays all-of-them,
                because the sizes only mean anything judged against each other — but one wrong
                panel should not cost a re-roll of the film. */}
            <button
              onClick={() => void studio.guard(() => api.writePanels(board.slug, [beat.n]))}
              disabled={writing || drawing}
              title="have the model rewrite just this shot's grammar"
              className="hover:text-warm disabled:opacity-30"
            >
              ✎
            </button>
            {beat.panel_url ? (
              <button
                onClick={() => void studio.guard(() => api.removePanel(board.slug, beat.n))}
                disabled={drawing}
                title="throw this panel away. Nothing is conditioned on it"
                className="hover:text-red-600 disabled:opacity-30"
              >
                ✕
              </button>
            ) : null}
            <button
              onClick={() => studio.setExpanded(beat.n)}
              title="open the whole scene"
              className="hover:text-zinc-700"
            >
              ⤢
            </button>
          </span>
        </div>

        <textarea
          className={`${inputClass} thin h-14 leading-relaxed`}
          value={shot.draft}
          onChange={(event) => shot.change(event.target.value)}
          onBlur={shot.flush}
          placeholder="medium shot, eye level, locked off — the subject at frame left"
          title="shot size, angle, camera move. It conditions the still, not the clip, so editing
            it marks nothing stale."
        />

        {bound.length ? (
          <div className="flex flex-wrap gap-1">
            {bound.map((id) => {
              const entry = board.staging.find((candidate) => candidate.id === id);
              if (!entry) return null;
              return (
                <button
                  key={id}
                  onClick={() =>
                    void studio.guard(() =>
                      api.bindStage(board.slug, beat.n, nextBinding(bound, id)),
                    )
                  }
                  title={`${entry.role} — click to take it out of this scene`}
                  className="rounded-full bg-soft px-2 py-0.5 text-[10px] text-zinc-600 transition-colors hover:bg-softer"
                >
                  {entry.name}
                </button>
              );
            })}
            {/* The short form of the two sentences `StagingBind` prints. The still's cap is the
                feature — a set that does not fit reaches it as prose — so it must not quietly
                disappear at grid scale. */}
            <span className="w-full text-[10px] leading-snug text-zinc-400">
              clip: {beat.staging_refs} of {bound.length} as pictures · still:{" "}
              {beat.staging_still_refs}
              {beat.staging_still_text ? ", the rest as words" : ""}
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The whole film read at once.
 *
 * Built from the individual panels rather than from `board.panel_sheet`, so a redrawn panel
 * updates immediately — and so it is clickable. The PNG stays as what it is: a file you can send
 * someone. Five medium shots in a row is invisible in a column and unmissable in a grid, which
 * is the only reason a contact sheet exists.
 */
function ContactSheet({ board }: { board: Board }) {
  const studio = useStudio();
  const drawn = board.beats.filter((beat) => beat.panel_url);
  if (!drawn.length) {
    return (
      <p className="text-[12px] leading-relaxed text-zinc-400">
        No panels drawn yet — there is nothing to read as a sheet.
      </p>
    );
  }
  return (
    <div>
      <div className="grid grid-cols-3 gap-2 rounded-2xl border border-edge bg-panel p-3">
        {board.beats.map((beat) => (
          <button
            key={beat.n}
            onClick={() => studio.setExpanded(beat.n)}
            className="text-left"
            title={beat.panel || beat.scene}
          >
            {beat.panel_url ? (
              <img
                src={beat.panel_url}
                alt={`panel ${beat.n}`}
                className="aspect-[9/16] w-full rounded-lg bg-ink object-contain"
              />
            ) : (
              <div className="flex aspect-[9/16] w-full items-center justify-center rounded-lg border border-dashed border-edge bg-ink text-[10px] text-zinc-300">
                —
              </div>
            )}
            <div className="mt-1 text-center text-[10px] text-zinc-400">
              {beat.n} · {beat.actual_seconds.toFixed(0)}s ·{" "}
              <span className={JOIN_LOOK[beat.source].tone}>
                {JOIN_LOOK[beat.source].short}
              </span>
            </div>
          </button>
        ))}
      </div>
      {board.panel_sheet ? (
        <button
          onClick={() => window.open(board.panel_sheet!, "_blank")}
          className="mt-2 text-[11px] text-zinc-400 underline hover:text-zinc-700"
          title="every panel on one numbered PNG — a file you can send someone"
        >
          the sheet as one file
        </button>
      ) : null}
    </div>
  );
}
