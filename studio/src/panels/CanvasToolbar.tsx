import { useEffect, useState } from "react";
import { api, clock, money } from "../api";
import type { Estimate, Job } from "../types";
import { useStudio } from "../useStudio";
import { Button } from "../ui";

/**
 * The money bar, floating over the work.
 *
 * It answers "am I paying right now, and how much" -- the question this whole tool exists to
 * keep answerable -- and it sits above the canvas rather than across the window because the
 * price it quotes is the price of the beats you can see. Reference design puts its toolbar in
 * the same place for the same reason: the controls belong to the thing under them.
 */
export function CanvasToolbar() {
  const studio = useStudio();
  const board = studio.board!;
  const job = studio.activeJob;
  const pending = board.pending ?? [];
  const busy = Boolean(job);
  const selected = studio.renderSelection.filter((n) => board.beats.some((beat) => beat.n === n));
  const [selectedEstimate, setSelectedEstimate] = useState<Estimate | null>(null);
  const [selectedDraftEstimate, setSelectedDraftEstimate] = useState<Estimate | null>(null);

  useEffect(() => {
    let current = true;
    if (selected.length === 0) {
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
  }, [board.slug, board.beats, selected.join(","), studio.setError]);

  const renderBeats = selected.length ? selected : undefined;
  const renderCount = selected.length
    ? (selectedEstimate?.beats?.length ?? selected.length)
    : pending.length;
  const renderCost = selected.length
    ? selectedEstimate?.predicted_cost
    : board.pending_cost.predicted_cost;
  const renderSeconds = selected.length
    ? selectedEstimate?.predicted_seconds
    : board.pending_cost.predicted_seconds;
  const canRender = renderCount > 0 && !busy;

  const startRender = (draft: boolean) => {
    void studio.guard(async () => {
      await api.render(board.slug, renderBeats, draft);
      studio.setRenderSelection([]);
    });
  };

  return (
    <div className="pointer-events-none absolute inset-x-0 top-4 z-10 flex justify-center">
      <div
        className="lift pointer-events-auto flex max-w-[calc(100%-2rem)] items-center gap-2
          rounded-full border border-edge bg-panel/95 px-2 py-1.5 backdrop-blur"
      >
        <span className="max-w-48 truncate px-2 text-[12px] font-medium text-zinc-800">
          {board.title}
        </span>
        <span className="text-zinc-200">|</span>

        {job ? <Phases job={job} /> : null}

        {canRender ? (
          <>
            <Button
              tone="ghost"
              onClick={() => startRender(true)}
              title={
                selected.length
                  ? `draft the ${renderCount} selected/dependent scenes`
                  : `a cheap approval pass at ${board.draft_cost.video_seconds.toFixed(0)}s total`
              }
            >
              draft{" "}
              {selected.length
                ? selectedDraftEstimate
                  ? money(selectedDraftEstimate.predicted_cost)
                  : "…"
                : money(board.draft_cost.predicted_cost)}
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
                : `${renderCount} ${renderCount === 1 ? "beat" : "beats"}`}{" "}
              · {renderCost === undefined ? "…" : money(renderCost)}
            </Button>
            {selected.length ? (
              <button
                onClick={() => studio.setRenderSelection([])}
                className="px-1.5 text-[11px] text-zinc-400 hover:text-zinc-700"
                title="clear render selection"
              >
                clear
              </button>
            ) : null}
          </>
        ) : null}

        {renderCount === 0 && !busy ? (
          <span className="px-2 text-[11px] text-zinc-400">nothing to render</span>
        ) : null}

        {job ? (
          <Button tone="quiet" onClick={() => void studio.guard(() => api.cancel(job.id))}>
            {job.cancelling ? "cancelling…" : "cancel"}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The phase strip. Stages differ in length by an order of magnitude, so one bar across all
 * of them would misrepresent progress; this shows which stage, and what already succeeded.
 */
function Phases({ job }: { job: Job }) {
  if (job.kind !== "render") {
    const labels: Record<string, string> = {
      chat: "qwen thinking",
      plan: "writing the script",
      asset: "generating a still",
      still_chat: "looking at the still",
      revise: "rewriting the line",
      ref_draw: "drawing a picture",
      ref_chat: "looking at the picture",
      caption: "writing the caption",
    };
    return (
      <span className="px-1 text-[11px] text-warm">{labels[job.kind] ?? job.phase}…</span>
    );
  }
  const stages = ["deploying", "booting", "rendering", "stitching"];
  const at = stages.indexOf(job.phase);
  return (
    <div className="flex items-center gap-1.5 px-1 text-[10px]">
      {stages.map((stage, index) => (
        <span
          key={stage}
          className={
            index < at ? "text-live" : index === at ? "text-warm" : "text-zinc-300"
          }
        >
          {index < at ? "✓" : index === at ? "◐" : "○"} {stage}
        </span>
      ))}
      {job.beat_total ? (
        <span className="text-zinc-500">
          beat {job.beat_index}/{job.beat_total}
          {job.step_max ? ` · step ${job.step}/${job.step_max}` : ""}
        </span>
      ) : null}
    </div>
  );
}
