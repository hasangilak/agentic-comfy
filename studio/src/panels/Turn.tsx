import { useState } from "react";
import type { ActivityEvent, ChatTurn } from "../types";
import { ActivityTimeline } from "./ActivityTimeline";

/**
 * One turn of the board's conversation. The user's is a bubble on the right; the model's is
 * plain text, because it is the one being read rather than the one being addressed. What it
 * *did* is a fold: a turn that rewrote six beats used to push its own sentence off the top.
 *
 * Its own file because two surfaces render the same transcript -- the rail-width `ChatPanel`
 * and the full-width conversation on the Script stage -- and a second copy of this would be a
 * second answer to "what does an edit look like".
 *
 * A turn from a crew agent is attributed and every other model turn is not, which is a real
 * distinction rather than decoration. "gemini" is the legacy chat role before the director
 * agent; "director" is the one voice the studio talks to now. An agent turn is a specialist
 * reporting back on work nobody watched -- so which specialist is the first thing worth knowing.
 */
const AGENT_ROLES = new Set([
  "director",
  "script-writer",
  "storyboarder",
  "asset-maker",
  "mise-en-scene",
  "character-sheet",
  "style-paper-cutout",
  "style-claymation",
]);

const ROLE_LABELS: Record<string, string> = {
  director: "director",
  gemini: "assistant",
};

export function Turn({
  turn,
  liveActivity,
}: {
  turn: ChatTurn;
  liveActivity?: ActivityEvent[];
}) {
  const [open, setOpen] = useState(false);
  const ops = turn.ops ?? [];
  const activity = liveActivity?.length ? liveActivity : (turn.activity ?? []);

  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-soft px-3 py-2 text-xs
          leading-relaxed text-zinc-800">
          {turn.selection?.length ? (
            <span className="mr-1.5 text-[10px] text-warm">beat {turn.selection.join(", ")}</span>
          ) : null}
          {turn.text}
        </div>
      </div>
    );
  }

  const showRole = AGENT_ROLES.has(turn.role);
  const roleLabel = ROLE_LABELS[turn.role] ?? turn.role;

  return (
    <div>
      {showRole ? (
        <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
          {roleLabel}
        </div>
      ) : null}
      <ActivityTimeline events={activity} live={Boolean(liveActivity?.length)} />
      {ops.length ? (
        <button
          onClick={() => setOpen((value) => !value)}
          className="mb-1.5 flex w-full items-center gap-2 rounded-xl px-1.5 py-1 text-left
            transition-colors hover:bg-hover"
        >
          <span className="text-[12px] text-live">✦</span>
          <span className="text-[12px] text-zinc-600">
            Edits <span className="text-zinc-400">{ops.length}</span>
          </span>
          <span className="ml-auto text-[11px] text-zinc-300">{open ? "▾" : "›"}</span>
        </button>
      ) : null}
      {open ? (
        <div className="mb-1.5 space-y-1 border-l border-edge pl-3">
          {ops.map((op, index) => (
            <div key={index} className="text-[11px] leading-relaxed text-zinc-500">
              {op.summary}
            </div>
          ))}
        </div>
      ) : null}
      <div className="whitespace-pre-wrap text-xs leading-relaxed text-zinc-700">{turn.text}</div>
    </div>
  );
}
