import { useEffect, useState } from "react";
import { api, clock, money } from "../api";
import type { Estimate } from "../types";
import { useStudio } from "../useStudio";
import { Button } from "../ui";

const CONTAINER_LOOK = {
  cold: { dot: "bg-zinc-600", label: "cold", hint: "no GPU running, nothing billing" },
  deploying: { dot: "bg-[#d99a4e] live-dot", label: "starting", hint: "billing has begun" },
  warm: { dot: "bg-[#4ade80] live-dot", label: "warm", hint: "GPU running and billing" },
  stopping: { dot: "bg-[#d99a4e]", label: "stopping", hint: "tearing the container down" },
};

/**
 * The money bar. Every element here answers "am I paying right now, and how much" -- which
 * is the question this whole tool exists to keep answerable.
 */
export function TopBar() {
  const studio = useStudio();
  const board = studio.board;
  const job = studio.activeJob;
  const look = CONTAINER_LOOK[studio.container.state];
  const pending = board?.pending ?? [];
  const busy = Boolean(job);
  const selected = board
    ? studio.renderSelection.filter((n) => board.beats.some((beat) => beat.n === n))
    : [];
  const [selectedEstimate, setSelectedEstimate] = useState<Estimate | null>(null);
  const [selectedDraftEstimate, setSelectedDraftEstimate] = useState<Estimate | null>(null);

  useEffect(() => {
    let current = true;
    if (!board || selected.length === 0) {
      setSelectedEstimate(null);
      setSelectedDraftEstimate(null);
      return () => {
        current = false;
      };
    }
    void Promise.all([
      api.estimate(board.slug, selected),
      api.estimate(board.slug, selected, true),
    ])
      .then(([estimate, draftEstimate]) => {
        if (current) {
          setSelectedEstimate(estimate);
          setSelectedDraftEstimate(draftEstimate);
        }
      })
      .catch((problem) => {
        if (current) studio.setError(String(problem));
      });
    return () => {
      current = false;
    };
  }, [board?.slug, board?.beats, selected.join(","), studio.setError]);

  const renderBeats = selected.length ? selected : undefined;
  const renderCount = selected.length
    ? (selectedEstimate?.beats?.length ?? selected.length)
    : pending.length;
  const renderCost = selected.length
    ? selectedEstimate?.predicted_cost
    : board?.pending_cost.predicted_cost;
  const renderSeconds = selected.length
    ? selectedEstimate?.predicted_seconds
    : board?.pending_cost.predicted_seconds;
  const canRender = Boolean(board && renderCount > 0 && !busy);

  const startRender = (draft: boolean) => {
    if (!board) return;
    void studio.guard(async () => {
      await api.render(board.slug, renderBeats, draft);
      studio.setRenderSelection([]);
    });
  };

  return (
    <div className="flex h-13 shrink-0 items-center gap-3 border-b border-[#26262e] bg-[#16161b] px-3">
      <span className="text-sm">🎞</span>
      <span className="max-w-56 truncate text-sm text-zinc-200">
        {board?.title ?? "Paper Reel Studio"}
      </span>

      {/* Container state and the clock that matches Modal's billing window: it starts at
          deploy, not at the first sampling step. */}
      <div
        className="ml-2 flex items-center gap-2 rounded bg-[#0d0d10] px-2.5 py-1"
        title={look.hint}
      >
        <span className={`h-2 w-2 rounded-full ${look.dot}`} />
        <span className="text-[11px] text-zinc-400">{look.label}</span>
        {studio.container.state !== "cold" ? (
          <span className="font-mono text-[11px] text-zinc-300">{clock(studio.liveSeconds)}</span>
        ) : null}
      </div>

      <span className="text-[11px] text-zinc-500" title="this session, estimated from container time">
        {money(studio.sessionCost)} session
      </span>

      {job ? <Phases job={job} /> : null}

      <div className="ml-auto flex items-center gap-2">
        {canRender ? (
          <>
            <Button
              tone="quiet"
              onClick={() => startRender(true)}
              title={
                selected.length
                  ? `draft the ${renderCount} selected/dependent scenes`
                  : `a cheap approval pass at ${board!.draft_cost.video_seconds.toFixed(0)}s total`
              }
            >
              draft {selected.length
                ? selectedDraftEstimate
                  ? money(selectedDraftEstimate.predicted_cost)
                  : "…"
                : money(board!.draft_cost.predicted_cost)}
            </Button>
            <Button
              tone="primary"
              onClick={() => startRender(false)}
              title={`${renderCount} beats including chained dependencies, about ${
                renderSeconds === undefined ? "…" : clock(renderSeconds)
              }`}
            >
              ▶ render{" "}
              {selected.length
                ? `${selected.length} selected`
                : `${renderCount} ${renderCount === 1 ? "beat" : "beats"}`} ·{" "}
              {renderCost === undefined ? "…" : money(renderCost)}
            </Button>
            {selected.length ? (
              <button
                onClick={() => studio.setRenderSelection([])}
                className="text-[10px] text-zinc-600 hover:text-zinc-300"
                title="clear render selection"
              >
                clear
              </button>
            ) : null}
          </>
        ) : null}

        {board && renderCount === 0 && !busy ? (
          <span className="text-[11px] text-zinc-600">nothing to render</span>
        ) : null}

        {job ? (
          <Button tone="quiet" onClick={() => void studio.guard(() => api.cancel(job.id))}>
            {job.cancelling ? "cancelling…" : "cancel"}
          </Button>
        ) : null}

        <Button
          tone="danger"
          onClick={() => void studio.guard(() => api.stopApp())}
          title="interrupt anything running and stop the GPU container immediately"
        >
          ■ stop
        </Button>
      </div>
    </div>
  );
}

/**
 * The phase strip. Stages differ in length by an order of magnitude, so one bar across all
 * of them would misrepresent progress; this shows which stage, and what already succeeded.
 */
function Phases({ job }: { job: import("../types").Job }) {
  if (job.kind !== "render") {
    const labels: Record<string, string> = {
      chat: "agy thinking",
      plan: "writing the script",
      asset: "generating a still",
      caption: "writing the caption",
    };
    return <span className="text-[11px] text-[#d99a4e]">{labels[job.kind] ?? job.phase}…</span>;
  }
  const stages = ["deploying", "booting", "rendering", "stitching"];
  const at = stages.indexOf(job.phase);
  return (
    <div className="flex items-center gap-1.5 text-[10px]">
      {stages.map((stage, index) => (
        <span
          key={stage}
          className={
            index < at
              ? "text-[#4ade80]"
              : index === at
                ? "text-[#d99a4e]"
                : "text-zinc-600"
          }
        >
          {index < at ? "✓" : index === at ? "◐" : "○"} {stage}
        </span>
      ))}
      {job.beat_total ? (
        <span className="text-zinc-400">
          beat {job.beat_index}/{job.beat_total}
          {job.step_max ? ` · step ${job.step}/${job.step_max}` : ""}
        </span>
      ) : null}
    </div>
  );
}
