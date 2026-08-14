import { useEffect, useState } from "react";
import { api, clock, money } from "../api";
import type { Estimate } from "../types";
import { useStudio } from "../useStudio";
import { Button } from "../ui";
import { JobStrip } from "./JobStrip";

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
  // Same arm-then-confirm as AddPicture's join warning: the render API never 409s over
  // inspect, because `manual_stills`, imported boards and `reel.py` must still spend.
  const [pendingDraft, setPendingDraft] = useState<boolean | null>(null);

  const inspectDone = (board.crew?.done ?? []).includes("inspect");
  const failing = board.inspect_failing ?? [];
  const inspectWarn = !inspectDone || failing.length > 0;

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

  useEffect(() => {
    setPendingDraft(null);
  }, [board.slug, inspectDone, failing.length, selected.join(",")]);

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

  const spend = (draft: boolean) => {
    setPendingDraft(null);
    void studio.guard(async () => {
      await api.render(board.slug, renderBeats, draft);
      studio.setRenderSelection([]);
    });
  };

  const requestRender = (draft: boolean) => {
    if (inspectWarn) {
      setPendingDraft(draft);
      return;
    }
    spend(draft);
  };

  const inspectMessage = failing.length
    ? `Inspect failed on ${failing.length} still${failing.length === 1 ? "" : "s"}. This spend is not blocked.`
    : "Inspect has not run on these stills. This spend is not blocked.";

  return (
    <div className="pointer-events-none absolute inset-x-0 top-4 z-10 flex flex-col items-center gap-1.5">
      <div
        className="lift pointer-events-auto flex max-w-[calc(100%-2rem)] items-center gap-2
          rounded-full border border-edge bg-panel/95 px-2 py-1.5 backdrop-blur"
      >
        <span className="max-w-48 truncate px-2 text-[12px] font-medium text-zinc-800">
          {board.title}
        </span>
        <span className="text-zinc-200">|</span>

        {job ? <JobStrip job={job} /> : null}

        {canRender ? (
          <>
            <Button
              tone="ghost"
              onClick={() => requestRender(true)}
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
              onClick={() => requestRender(false)}
              title={`${renderCount} beats including chained dependencies, about ${
                renderSeconds === undefined ? "…" : clock(renderSeconds)
              }`}
            >
              ▶ render{" "}
              {selected.length
                ? `${selected.length} selected`
                : `${renderCount} ${renderCount === 1 ? "beat" : "beats"}`}{" "}
              · {renderCost === undefined ? "…" : money(renderCost)}
              {renderSeconds === undefined ? "" : ` · ~${clock(renderSeconds)}`}
            </Button>
            {/* What is NOT in that price. `pending` is every beat that needs rendering, so on a
                part-rendered board the number on the button is smaller than the reel and the
                difference is worth stating rather than leaving to be worked out. */}
            {!selected.length && renderCount < board.beats.length ? (
              <span className="px-1 text-[10px] text-zinc-400">
                of {board.beats.length} · the rest are already rendered
              </span>
            ) : null}
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
      {pendingDraft !== null ? (
        <div
          className="lift pointer-events-auto max-w-[calc(100%-2rem)] rounded-xl border
            border-warm/40 bg-panel/95 px-3 py-2 backdrop-blur"
        >
          <p className="text-[10px] leading-snug text-warm">⚠ {inspectMessage}</p>
          <div className="mt-1.5 flex items-center gap-2">
            <Button tone="primary" onClick={() => spend(pendingDraft)}>
              {pendingDraft ? "draft anyway" : "render anyway"}
            </Button>
            <button
              onClick={() => setPendingDraft(null)}
              className="px-1.5 text-[11px] text-zinc-400 hover:text-zinc-700"
            >
              cancel
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
