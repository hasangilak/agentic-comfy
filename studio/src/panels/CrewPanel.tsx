import { useEffect, useState } from "react";
import { api } from "../api";
import type { AgentInfo, AgentRoster, CrewPlan, Job, PhasePlan, StagePlan } from "../types";
import { useStudio } from "../useStudio";
import { prettyKey } from "../ui";
import { ActivityTimeline } from "./ActivityTimeline";

/**
 * Who is working on this reel, what they are doing, and what they handed back.
 *
 * A stage is a cast rather than one agent (`paperreel/crew.py`), and storyboard/assets are
 * further sliced into gated phases so the director can approve the named roster, the
 * panels, the sheets and the seams before the next specialists run. Default buttons run
 * the next phase; "skip gates" is the escape hatch that burns through a whole stage the
 * way this panel used to.
 *
 * Three things are shown and they are deliberately three rather than one panel of everything:
 *
 *   the cast     who works each remaining stage, grouped by phase, with checkers marked by lens
 *   the live one which member of which cast is thinking, off `Job.phase`
 *   what landed  every agent's reply in the board transcript -- see `AgentTurns`
 *
 * Nothing here can start a render.
 */
export function CrewPanel() {
  const studio = useStudio();
  const board = studio.board;
  const [roster, setRoster] = useState<AgentRoster | null>(null);
  const [plan, setPlan] = useState<CrewPlan | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<string | null>(null);
  const [askText, setAskText] = useState("");

  useEffect(() => {
    void api.agents().then(setRoster).catch(() => setRoster(null));
  }, []);
  // Staging, panels and the crew cursor all move the plan; beats/assets_needed alone missed
  // the mid-storyboard gates.
  useEffect(() => {
    if (!board) return setPlan(null);
    void api
      .crewPlan(board.slug)
      .then(setPlan)
      .catch(() => setPlan(null));
  }, [
    board?.slug,
    board?.medium,
    board?.beats.length,
    board?.assets_needed.length,
    board?.staging.length,
    board?.beats.map((beat) => beat.panel ?? "").join("|"),
    board?.crew?.awaiting,
    board?.crew?.done?.join(","),
  ]);

  if (!board || !roster) return null;

  const job = studio.activeJob?.slug === board.slug ? studio.activeJob : null;
  const running = job && (job.kind === "crew" || job.kind === "agent") ? job : null;
  const byName = new Map(roster.agents.map((agent) => [agent.name, agent]));
  const awaiting = plan?.awaiting ?? board.crew?.awaiting ?? null;

  async function runAgent(name: string) {
    const brief = askText.trim();
    if (!brief) return;
    setBusy(true);
    try {
      await studio.guard(() => api.runAgent(board!.slug, name, brief));
      setAskText("");
      setAsking(null);
    } finally {
      setBusy(false);
    }
  }

  async function runNext(stage?: string, phase?: string) {
    setBusy(true);
    try {
      await studio.guard(() =>
        api.runCrew(board!.slug, {
          ...(stage ? { stage } : {}),
          ...(phase ? { phase } : awaiting ? { phase: awaiting } : {}),
        }),
      );
    } finally {
      setBusy(false);
    }
  }

  async function runUngated(stage?: string) {
    setBusy(true);
    try {
      await studio.guard(() =>
        api.runCrew(board!.slug, { ...(stage ? { stage } : {}), ungated: true }),
      );
    } finally {
      setBusy(false);
    }
  }

  const empty = plan !== null && plan.plan.length === 0 && !awaiting;

  return (
    <div className="mt-4 space-y-0.5 px-3">
      <div className="flex items-baseline gap-2 px-2.5 pb-1">
        <span className="text-[11px] font-medium text-zinc-400">Crew</span>
        <span className="text-[10px] capitalize text-zinc-300">{prettyKey(board.medium)}</span>
        {!running && !empty ? (
          <>
            <button
              onClick={() => void runNext()}
              disabled={busy}
              className="ml-auto rounded-lg px-1.5 py-0.5 text-[10px] text-zinc-700
                transition-colors hover:bg-hover disabled:opacity-40"
              title="run the next gated phase and stop for approval"
            >
              {awaiting ? `run ${awaiting}` : "run next"}
            </button>
            <button
              onClick={() => void runUngated()}
              disabled={busy}
              className="rounded-lg px-1.5 py-0.5 text-[10px] text-zinc-400
                transition-colors hover:bg-hover hover:text-zinc-800 disabled:opacity-40"
              title="burn through every remaining stage without pausing at gates"
            >
              skip gates
            </button>
          </>
        ) : null}
      </div>

      {running ? <LiveCrew job={running} /> : null}

      {plan === null ? (
        <p className="px-2.5 py-1 text-[10px] text-zinc-400">…</p>
      ) : empty ? (
        <p className="px-2.5 py-1 text-[10px] leading-relaxed text-zinc-400">
          Nothing left for the crew. What remains is the render, which no agent can start.
        </p>
      ) : (
        plan.plan.map((entry) => (
          <StageBlock
            key={entry.stage}
            entry={entry}
            byName={byName}
            running={running}
            busy={busy}
            open={open}
            onToggle={setOpen}
            asking={asking}
            askText={askText}
            onAsk={setAsking}
            onAskText={setAskText}
            onSubmitAsk={runAgent}
            onRunPhase={(phase) => void runNext(entry.stage, phase)}
            onRunUngated={() => void runUngated(entry.stage)}
          />
        ))
      )}
    </div>
  );
}

function StageBlock({
  entry,
  byName,
  running,
  busy,
  open,
  onToggle,
  asking,
  askText,
  onAsk,
  onAskText,
  onSubmitAsk,
  onRunPhase,
  onRunUngated,
}: {
  entry: StagePlan;
  byName: Map<string, AgentInfo>;
  running: Job | null;
  busy: boolean;
  open: string | null;
  onToggle: (key: string | null) => void;
  asking: string | null;
  askText: string;
  onAsk: (name: string | null) => void;
  onAskText: (text: string) => void;
  onSubmitAsk: (name: string) => void;
  onRunPhase: (phase: string) => void;
  onRunUngated: () => void;
}) {
  const phases = entry.phases?.length
    ? entry.phases
    : [{ id: entry.stage, agents: entry.cast, status: "pending" as const }];
  return (
    <div className="mb-1">
      <div className="flex items-center gap-2 px-2.5 py-1">
        <span className="text-[10px] uppercase tracking-wide text-zinc-400">{entry.stage}</span>
        {!running ? (
          <>
            <button
              onClick={onRunUngated}
              disabled={busy}
              className="ml-auto rounded-lg px-1.5 py-0.5 text-[10px] text-zinc-400
                transition-colors hover:bg-hover hover:text-zinc-800 disabled:opacity-40"
              title="run this whole stage without pausing at gates"
            >
              skip gates
            </button>
          </>
        ) : null}
      </div>
      {phases.map((phase, phaseIndex) => (
        <div key={phase.id}>
          {phaseIndex > 0 ? (
            <div className="mx-2.5 my-1 flex items-center gap-2">
              <div className="h-px flex-1 bg-edge" />
              <span className="text-[9px] uppercase tracking-wide text-zinc-300">gate</span>
              <div className="h-px flex-1 bg-edge" />
            </div>
          ) : null}
          <PhaseHeader
            phase={phase}
            canRun={!running && phase.status !== "done"}
            busy={busy}
            onRun={() => onRunPhase(phase.id)}
          />
          {phase.agents.map((member, index) => (
            <CastRow
              key={`${entry.stage}-${phase.id}-${member.agent}-${index}`}
              agent={byName.get(member.agent)}
              name={member.agent}
              lens={member.lens}
              order={index + 1}
              live={Boolean(
                running?.phase?.includes(member.agent) &&
                  (running.phase?.includes(phase.id) || running.phase?.includes(entry.stage)),
              )}
              open={open === `${entry.stage}-${phase.id}-${index}`}
              onToggle={() =>
                onToggle(
                  open === `${entry.stage}-${phase.id}-${index}`
                    ? null
                    : `${entry.stage}-${phase.id}-${index}`,
                )
              }
              asking={asking === member.agent}
              onAsk={() => {
                onAsk(asking === member.agent ? null : member.agent);
                onAskText("");
              }}
              askText={asking === member.agent ? askText : ""}
              onAskText={onAskText}
              onSubmitAsk={() => onSubmitAsk(member.agent)}
              askBusy={busy}
              canAsk={!running}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function PhaseHeader({
  phase,
  canRun,
  busy,
  onRun,
}: {
  phase: PhasePlan;
  canRun: boolean;
  busy: boolean;
  onRun: () => void;
}) {
  const tone =
    phase.status === "done"
      ? "text-zinc-300"
      : phase.status === "awaiting"
        ? "text-warm"
        : "text-zinc-500";
  return (
    <div className="flex items-center gap-2 px-2.5 py-0.5">
      <span className={`text-[10px] font-medium ${tone}`}>
        {phase.id}
        {phase.status === "awaiting" ? " · approve next" : phase.status === "done" ? " · done" : ""}
      </span>
      {canRun && phase.status === "awaiting" ? (
        <button
          onClick={onRun}
          disabled={busy}
          className="ml-auto rounded-lg bg-solid px-1.5 py-0.5 text-[10px] text-white
            disabled:opacity-40"
          title={`run the ${phase.id} phase and stop`}
        >
          run
        </button>
      ) : canRun ? (
        <button
          onClick={onRun}
          disabled={busy}
          className="ml-auto rounded-lg px-1.5 py-0.5 text-[10px] text-zinc-400
            transition-colors hover:bg-hover hover:text-zinc-800 disabled:opacity-40"
          title={`re-run the ${phase.id} phase`}
        >
          re-run
        </button>
      ) : null}
    </div>
  );
}

function CastRow({
  agent,
  name,
  lens,
  order,
  live,
  open,
  onToggle,
  asking,
  onAsk,
  askText,
  onAskText,
  onSubmitAsk,
  askBusy,
  canAsk,
}: {
  agent: AgentInfo | undefined;
  name: string;
  lens: string | null;
  order: number;
  live: boolean;
  open: boolean;
  onToggle: () => void;
  asking: boolean;
  onAsk: () => void;
  askText: string;
  onAskText: (text: string) => void;
  onSubmitAsk: () => void;
  askBusy: boolean;
  canAsk: boolean;
}) {
  return (
    <div>
      <div className="flex items-center gap-1">
        <button
          onClick={onToggle}
          className={`flex min-w-0 flex-1 items-center gap-2 rounded-xl px-2.5 py-1.5 text-left
            transition-colors ${open ? "bg-soft" : "hover:bg-hover"}`}
          title={agent?.description ?? agent?.error ?? name}
        >
          <span className="w-4 shrink-0 text-center text-[10px] text-zinc-300">
            {live ? (
              <span className="inline-block h-2 w-2 rounded-full bg-warm live-dot" />
            ) : (
              order
            )}
          </span>
          <span
            className={`min-w-0 flex-1 truncate text-[12px] capitalize ${
              live ? "font-medium text-zinc-900" : "text-zinc-700"
            }`}
          >
            {prettyKey(name)}
          </span>
          {lens ? <span className="shrink-0 text-[10px] text-live">{lens}</span> : null}
          {agent?.error ? <span className="shrink-0 text-[10px] text-danger">broken</span> : null}
        </button>
        {canAsk ? (
          <button
            onClick={onAsk}
            title="talk to this specialist directly, bypassing the director"
            className={`shrink-0 rounded-lg px-1.5 py-1 text-[10px] transition-colors ${
              asking ? "bg-soft text-zinc-800" : "text-zinc-400 hover:bg-hover hover:text-zinc-700"
            }`}
          >
            ask
          </button>
        ) : null}
      </div>
      {asking ? (
        <div className="mb-1 space-y-1 px-2.5">
          <textarea
            className="thin w-full resize-none rounded-xl border border-edge bg-ink px-2 py-1.5
              text-[11px] leading-relaxed text-zinc-800 outline-none placeholder:text-zinc-400"
            rows={2}
            value={askText}
            onChange={(event) => onAskText(event.target.value)}
            placeholder={`what should ${prettyKey(name)} do?`}
          />
          <button
            onClick={onSubmitAsk}
            disabled={askBusy || !askText.trim()}
            className="rounded-lg bg-solid px-2 py-1 text-[10px] text-white disabled:opacity-40"
          >
            send
          </button>
        </div>
      ) : null}
      {open ? (
        <div className="mb-1 space-y-1 border-l border-edge pl-3.5 pr-2 pt-0.5">
          {agent?.error ? (
            <p className="text-[10px] leading-relaxed text-danger">{agent.error}</p>
          ) : (
            <>
              <p className="text-[10px] leading-relaxed text-zinc-500">{agent?.description}</p>
              <p className="text-[10px] leading-relaxed text-zinc-400">
                {agent?.model}
                {agent?.think ? " · thinking" : ""}
                {agent?.max_rounds ? ` · up to ${agent.max_rounds} rounds` : ""}
              </p>
              <p className="text-[10px] leading-relaxed text-zinc-400">
                {(agent?.tools ?? []).join(", ")}
              </p>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

function LiveCrew({ job }: { job: Job }) {
  const studio = useStudio();
  const activity = studio.liveActivity[job.id] ?? job.activity ?? [];
  return (
    <div className="mb-1.5 space-y-1.5 rounded-xl border border-warm/30 bg-warm/5 px-2.5 py-1.5">
      <div className="flex items-center gap-2">
        <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-warm live-dot" />
        <span className="min-w-0 flex-1 truncate text-[11px] text-warm">
          {prettyKey(
            job.phase || (job.kind === "crew" ? "the crew is starting" : "the agent is starting"),
          )}
        </span>
        <button
          onClick={() => void studio.guard(() => api.cancel(job.id))}
          className="shrink-0 rounded-lg px-1.5 py-0.5 text-[10px] text-zinc-400
            transition-colors hover:bg-hover hover:text-danger"
          title="stop after this round"
        >
          stop
        </button>
      </div>
      {activity.length ? <ActivityTimeline events={activity} live defaultOpen /> : null}
    </div>
  );
}

/**
 * What the agents handed back, as a transcript.
 *
 * Reads `studio.chat` and filters; there is no second store to keep in step.
 */
export function AgentTurns({ names }: { names?: string[] }) {
  const studio = useStudio();
  const turns = studio.chat
    .map((turn, index) => ({ turn, index }))
    .filter(({ turn }) => AGENT_ROLES.has(turn.role) && (!names || names.includes(turn.role)));
  if (!turns.length) return null;
  return (
    <div className="space-y-2">
      {turns.map(({ turn, index }) => (
        <AgentTurn key={index} role={turn.role} text={turn.text} ops={turn.ops ?? []} />
      ))}
    </div>
  );
}

const AGENT_ROLES = new Set([
  "director",
  "script-writer",
  "storyboarder",
  "asset-maker",
  "mise-en-scene",
  "character-sheet",
  "set-designer",
  "continuity",
  "style-paper-cutout",
  "style-paper-craft",
  "style-claymation",
]);

function AgentTurn({ role, text, ops }: { role: string; text: string; ops: { summary: string }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-2xl border border-edge bg-panel px-3 py-2.5">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[10px] font-medium uppercase tracking-wide text-zinc-500">
          {prettyKey(role)}
        </span>
        {ops.length ? (
          <button
            onClick={() => setOpen((was) => !was)}
            className="ml-auto rounded-lg px-1.5 py-0.5 text-[10px] text-zinc-400
              transition-colors hover:bg-hover hover:text-zinc-700"
          >
            {ops.length} edit{ops.length === 1 ? "" : "s"} {open ? "▾" : "›"}
          </button>
        ) : null}
      </div>
      {open ? (
        <div className="mb-1.5 space-y-1 border-l border-edge pl-3">
          {ops.map((op, index) => (
            <div key={index} className="text-[10px] leading-relaxed text-zinc-500">
              {op.summary}
            </div>
          ))}
        </div>
      ) : null}
      <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-zinc-700">{text}</p>
    </div>
  );
}
