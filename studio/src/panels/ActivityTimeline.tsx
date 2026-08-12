import { useState } from "react";
import type { ActivityEvent } from "../types";

/**
 * What agents and tools did during a turn — live from SSE or persisted on the chat turn.
 *
 * Kept narrow on purpose: one column of cards, no graph framework. The server writes structured
 * events; this reads them. A tool chip and an agent block are different kinds, not different
 * components reinvented per stage.
 */
export function ActivityTimeline({
  events,
  live = false,
  defaultOpen = false,
}: {
  events: ActivityEvent[];
  live?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen || live);
  if (!events.length) return null;

  const failed = events.some((event) => event.status === "failed");

  return (
    <div className="mb-2">
      <button
        onClick={() => setOpen((was) => !was)}
        className={`flex w-full items-center gap-2 rounded-xl border px-2.5 py-1.5 text-left
          transition-colors ${
            failed
              ? "border-stale/30 bg-stale/5 hover:bg-stale/10"
              : "border-edge bg-ink hover:bg-hover"
          }`}
      >
        {live ? (
          <span className="inline-block h-2 w-2 shrink-0 rounded-full bg-warm live-dot" />
        ) : (
          <span className="text-[11px] text-live">⚡</span>
        )}
        <span className="text-[11px] text-zinc-600">
          {live ? "working…" : "activity"}{" "}
          <span className="text-zinc-400">{events.length}</span>
        </span>
        <span className="ml-auto text-[11px] text-zinc-300">{open ? "▾" : "›"}</span>
      </button>
      {open ? (
        <div className="mt-1.5 space-y-1 border-l border-edge pl-3">
          {events.map((event) => (
            <ActivityRow key={event.id} event={event} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ActivityRow({ event }: { event: ActivityEvent }) {
  const label = labelFor(event);
  const running = event.status === "running";
  const failed = event.status === "failed";
  return (
    <div className="flex gap-2 text-[10px] leading-relaxed">
      <span
        className={`mt-0.5 w-3 shrink-0 ${
          running ? "text-warm" : failed ? "text-stale" : "text-live"
        }`}
      >
        {running ? "◐" : failed ? "✕" : "✓"}
      </span>
      <div className="min-w-0 flex-1">
        <div className={`font-medium ${failed ? "text-stale" : "text-zinc-600"}`}>{label}</div>
        {event.summary ? (
          <div className={`${failed ? "text-stale/80" : "text-zinc-500"}`}>{event.summary}</div>
        ) : null}
      </div>
    </div>
  );
}

function labelFor(event: ActivityEvent): string {
  if (event.kind === "tool_call" && event.tool) {
    return event.agent ? `${event.agent} · ${event.tool}` : event.tool;
  }
  if (event.kind === "agent_start" && event.agent) return `${event.agent} started`;
  if (event.kind === "agent_failed" && event.agent) return `${event.agent} failed`;
  if (event.kind === "round" && event.agent) return `${event.agent} · ${event.summary ?? "round"}`;
  if (event.agent) return event.agent;
  return event.kind.replace("_", " ");
}
