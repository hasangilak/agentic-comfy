import { Handle, Position } from "@xyflow/react";
import { api } from "../api";
import { useStudio } from "../useStudio";

/**
 * The head of the board: what this film is, in one card, and the one structural control.
 *
 * It used to hold the title, the style bible, the cast reference and the bulk still upload —
 * every reel-wide decision, on a node in the middle of a chain of scenes. Those are decisions
 * about the whole film rather than about the sequence, so they moved to the stages that are
 * about the whole film: the bible to Script, the cast reference and the stills to Assets, the
 * design bible to Storyboard. Nothing was removed, and each of them is one click away.
 *
 * `＋ add scene at end` stays, because that is structure, and structure is what this stage is.
 */
export function ScriptNode() {
  const studio = useStudio();
  const board = studio.board!;
  const structureBusy = Object.values(studio.jobs).some(
    (job) =>
      job.slug === board.slug && (job.state === "queued" || job.state === "running"),
  );

  const totalFrames = board.beats.reduce((sum, beat) => sum + beat.frames, 0);
  const seconds = board.beats.reduce((sum, beat) => sum + beat.actual_seconds, 0);

  return (
    <div className="lift w-72 rounded-2xl border border-edge bg-panel">
      <div className="flex items-center gap-2 border-b border-edge px-3 py-2">
        <span className="text-sm">📄</span>
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">script</span>
        <span className="ml-auto text-[10px] text-zinc-500">
          {board.beats.length} beats · {seconds.toFixed(1)}s
        </span>
      </div>

      <div className="space-y-2 p-3">
        <p className="truncate text-[13px] font-medium text-zinc-800" title={board.title}>
          {board.title || "untitled reel"}
        </p>

        <button
          onClick={() => studio.goStage("script")}
          className="nodrag w-full rounded border border-edge bg-ink p-2 text-left
            transition-colors hover:bg-hover"
          title="the title and the style bible — the paragraph every still and every clip is told"
        >
          <span className="block text-[10px] uppercase tracking-wide text-zinc-500">
            style bible
          </span>
          <span className="mt-0.5 line-clamp-3 block text-[10px] leading-relaxed text-zinc-500">
            {board.style_bible || "nothing written yet"}
          </span>
          <span className="mt-1 block text-[10px] text-zinc-400">edit in Script →</span>
        </button>

        <button
          onClick={() => studio.goStage("storyboard")}
          className="nodrag flex w-full items-center gap-2 rounded border border-edge bg-ink px-2 py-1.5
            text-left transition-colors hover:bg-hover"
          title="the cast, the sets and the props this film is made of — designed once, then
            bound to the scenes that contain them"
        >
          <span className="text-[13px]">🎭</span>
          <span className="min-w-0 flex-1">
            <span className="block text-[10px] uppercase tracking-wide text-zinc-500">staging</span>
            <span className="block truncate text-[10px] text-zinc-500">
              {board.staging.length
                ? board.staging.map((entry) => entry.name).join(", ")
                : "nothing designed yet — the cast, the sets, the props"}
            </span>
          </span>
          <span className="shrink-0 text-[10px] text-zinc-400">
            {board.staging.length || "＋"}
          </span>
        </button>

        <button
          onClick={() => studio.goStage("assets")}
          className="nodrag flex w-full items-center gap-2 rounded border border-edge bg-ink px-2 py-1.5
            text-left transition-colors hover:bg-hover"
          title="the still each shot opens on, the cast reference every one of them is matched to,
            and what each one is drawn from"
        >
          {board.reference ? (
            <img src={board.reference} alt="" className="h-9 w-6 shrink-0 rounded object-cover" />
          ) : (
            <span className="flex h-9 w-6 shrink-0 items-center justify-center rounded border border-dashed border-edge text-[9px] text-zinc-400">
              —
            </span>
          )}
          <span className="min-w-0 flex-1">
            <span className="block text-[10px] uppercase tracking-wide text-zinc-500">stills</span>
            <span className="block truncate text-[10px] text-zinc-500">
              {board.manual_stills
                ? "your own — nothing generates"
                : board.assets_needed.length
                  ? `${board.assets_needed.length} scene${
                      board.assets_needed.length === 1 ? "" : "s"
                    } still to draw`
                  : "every scene has one"}
            </span>
          </span>
          <span className="shrink-0 text-[10px] text-zinc-400">→</span>
        </button>

        <div className="flex items-center justify-between pt-1 text-[10px] text-zinc-400">
          <span>
            {totalFrames} frames @ {board.steps} steps
            {board.temperature != null && board.temperature !== 1
              ? ` · temp ${board.temperature.toFixed(2)}`
              : ""}
          </span>
          <span>seed {board.seed}</span>
        </div>

        {/* A new beat starts with no action, which leaves it `planned` -- so it is excluded
            from the render button and costs nothing until somebody writes the movement. */}
        <button
          onClick={() => void studio.guard(() => api.addBeat(board.slug, {}))}
          disabled={structureBusy}
          className="w-full rounded bg-soft py-1 text-[11px] text-zinc-700
            hover:bg-softer disabled:cursor-not-allowed disabled:opacity-40"
          title={
            structureBusy
              ? "wait for the current job to finish"
              : "appends an empty scene to the end of the linear sequence"
          }
        >
          ＋ add scene at end
        </button>
      </div>

      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
