import { useEffect, useState } from "react";
import { api } from "../api";
import type { AgentInfo, AgentRoster, CrewPlan, Job } from "../types";
import { useStudio } from "../useStudio";
import { ActivityTimeline } from "./ActivityTimeline";

/**
 * Who is working on this reel, what they are doing, and what they handed back.
 *
 * A stage is a cast rather than one agent (`paperreel/crew.py`), which is the whole reason this
 * panel exists: with one agent per stage the rail's stage row said everything there was to say.
 * With three or four specialists in sequence, "the storyboard stage is running" is no longer an
 * answer to "what is happening" -- and neither is the job log, which says what a tool did
 * without saying who called it.
 *
 * Three things are shown and they are deliberately three rather than one panel of everything:
 *
 *   the cast     who works each stage that is left, in the order they work it, with the
 *                checkers marked by their lens. Free -- one GET, no model call.
 *   the live one which member of which cast is thinking, off `Job.phase`. The server writes
 *                that field per member and per round; nothing here is inferred.
 *   what landed  every agent's reply and its edits, which are already in the board's own
 *                transcript under the agent's name. This panel does not hold a second copy --
 *                see `AgentTurns`, which reads `studio.chat`.
 *
 * Nothing here can start a render. The crew has no cast for the studio stage and no route that
 * could reach one; the two buttons below submit `crew` and `agent` jobs, which are the only two
 * kinds this panel knows about.
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

  // The roster is the skills on disk and changes only when a SKILL.md does, so it is fetched
  // once. The plan is derived from the board and is refetched whenever the board announces a
  // change -- which is the same rule every other derived readout in this studio follows.
  useEffect(() => {
    void api.agents().then(setRoster).catch(() => setRoster(null));
  }, []);
  useEffect(() => {
    if (!board) return setPlan(null);
    void api
      .crewPlan(board.slug)
      .then(setPlan)
      .catch(() => setPlan(null));
  }, [board?.slug, board?.medium, board?.beats.length, board?.assets_needed.length]);

  if (!board || !roster) return null;

  const job = studio.activeJob?.slug === board.slug ? studio.activeJob : null;
  const running = job && (job.kind === "crew" || job.kind === "agent") ? job : null;
  const byName = new Map(roster.agents.map((agent) => [agent.name, agent]));

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

  async function runCrew(stage?: string) {
    setBusy(true);
    try {
      await studio.guard(() => api.runCrew(board!.slug, stage ? { stage } : {}));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 space-y-0.5 px-3">
      <div className="flex items-baseline gap-2 px-2.5 pb-1">
        <span className="text-[11px] font-medium text-zinc-400">Crew</span>
        <span className="text-[10px] text-zinc-300">{board.medium}</span>
        {plan?.plan.length && !running ? (
          <button
            onClick={() => void runCrew()}
            disabled={busy}
            className="ml-auto rounded-lg px-1.5 py-0.5 text-[10px] text-zinc-500
              transition-colors hover:bg-hover hover:text-zinc-800 disabled:opacity-40"
            title="run every stage that is left, and stop where money starts"
          >
            run all
          </button>
        ) : null}
      </div>

      {running ? <LiveCrew job={running} /> : null}

      {plan === null ? (
        <p className="px-2.5 py-1 text-[10px] text-zinc-400">…</p>
      ) : plan.plan.length === 0 ? (
        <p className="px-2.5 py-1 text-[10px] leading-relaxed text-zinc-400">
          Nothing left for the crew. What remains is the render, which no agent can start.
        </p>
      ) : (
        plan.plan.map((entry) => (
          <div key={entry.stage} className="mb-1">
            <div className="flex items-center gap-2 px-2.5 py-1">
              <span className="text-[10px] uppercase tracking-wide text-zinc-400">
                {entry.stage}
              </span>
              {!running ? (
                <button
                  onClick={() => void runCrew(entry.stage)}
                  disabled={busy}
                  className="ml-auto rounded-lg px-1.5 py-0.5 text-[10px] text-zinc-400
                    transition-colors hover:bg-hover hover:text-zinc-800 disabled:opacity-40"
                  title={`run this stage's cast and stop`}
                >
                  run
                </button>
              ) : null}
            </div>
            {entry.cast.map((member, index) => (
              <CastRow
                key={`${entry.stage}-${member.agent}-${index}`}
                agent={byName.get(member.agent)}
                name={member.agent}
                lens={member.lens}
                order={index + 1}
                live={Boolean(running?.phase?.startsWith(`${entry.stage} · ${member.agent}`))}
                open={open === `${entry.stage}-${index}`}
                onToggle={() =>
                  setOpen((was) =>
                    was === `${entry.stage}-${index}` ? null : `${entry.stage}-${index}`,
                  )
                }
                asking={asking === member.agent}
                onAsk={() => {
                  setAsking((was) => (was === member.agent ? null : member.agent));
                  setAskText("");
                }}
                askText={asking === member.agent ? askText : ""}
                onAskText={setAskText}
                onSubmitAsk={() => void runAgent(member.agent)}
                askBusy={busy}
                canAsk={!running}
              />
            ))}
          </div>
        ))
      )}
    </div>
  );
}

/**
 * One member of a cast. The number is its place in the order, which is load-bearing rather than
 * decorative -- the style artist mints the designs mise-en-scène then binds, and the panels come
 * last because a panel names the designs a beat binds.
 */
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
            className={`min-w-0 flex-1 truncate text-[12px] ${
              live ? "font-medium text-zinc-900" : "text-zinc-700"
            }`}
          >
            {name}
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
            placeholder={`what should ${name} do?`}
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

/**
 * The one line that says what is happening right now.
 *
 * `Job.phase` is written by the server per cast member and per round -- "storyboard ·
 * mise-en-scene" while a stage picks its next member, then "mise-en-scene · round 2" inside the
 * loop. Rendering it verbatim is deliberate: a label table here would be a second vocabulary for
 * something the server already says in words, and it would go stale the moment a skill is added.
 */
function LiveCrew({ job }: { job: Job }) {
  const studio = useStudio();
  const activity = studio.liveActivity[job.id] ?? job.activity ?? [];
  return (
    <div className="mb-1.5 space-y-1.5 rounded-xl border border-warm/30 bg-warm/5 px-2.5 py-1.5">
      <div className="flex items-center gap-2">
        <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-warm live-dot" />
        <span className="min-w-0 flex-1 truncate text-[11px] text-warm">
          {job.phase || (job.kind === "crew" ? "the crew is starting" : "the agent is starting")}
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
 * This is the answer to "what did it return to the main model to proceed with", and the reason
 * it needs no new state is the server's design: an agent writes its reply and its edits into the
 * board's own `chat` array under its skill name, beside the director's turns and the chat
 * panel's. `runtime.remember` does it for the same reason `agent.revise` does -- an agent that
 * rewrote five beats and left no trace is a board that changed for no reason the next turn can
 * see. So this reads `studio.chat` and filters; there is no second store to keep in step.
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

/**
 * Every role in `ChatTurn` that is an agent rather than the director, the chat panel or the
 * board speaking for itself. A set rather than a check against the roster, because a transcript
 * outlives the skill that wrote it: a turn from an agent since renamed still has to render.
 */
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
  "style-claymation",
]);

function AgentTurn({ role, text, ops }: { role: string; text: string; ops: { summary: string }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-2xl border border-edge bg-panel px-3 py-2.5">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-[10px] font-medium uppercase tracking-wide text-zinc-500">
          {role}
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
