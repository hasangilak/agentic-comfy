import { Handle, Position } from "@xyflow/react";
import { api, money } from "../api";
import { useDraft, useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";

/** The finished thing: the stitched 1080x1920 file, plus the caption that ships with it. */
export function ReelNode() {
  const studio = useStudio();
  const board = studio.board!;
  const seconds = board.beats.reduce((sum, beat) => sum + beat.actual_seconds, 0);
  const rendered = board.beats.filter((beat) => beat.video).length;
  const complete = rendered === board.beats.length && board.beats.length > 0;

  const caption = useDraft(board.caption, (next) =>
    void studio.guard(() => api.patchBoard(board.slug, { caption: next })),
  );

  const writing = Object.values(studio.jobs).some(
    (job) => job.kind === "caption" && job.state === "running",
  );

  return (
    <div className="lift w-64 rounded-2xl border border-edge bg-panel">
      <Handle type="target" position={Position.Left} />

      <div className="flex items-center gap-2 border-b border-edge px-3 py-2">
        <span className="text-sm">🎬</span>
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">reel</span>
        <span className="ml-auto text-[10px] text-zinc-500">
          {seconds.toFixed(1)}s · {money(board.spent)} spent
        </span>
      </div>

      <div className="space-y-2 p-3">
        {board.reel ? (
          <video
            src={board.reel}
            className="h-52 w-full rounded-xl bg-zinc-900 object-contain"
            controls
            loop
          />
        ) : (
          <div
            className="flex h-52 items-center justify-center rounded-xl bg-ink
              text-center text-[10px] leading-relaxed text-zinc-400"
          >
            {complete
              ? "render again to stitch"
              : `${rendered} of ${board.beats.length} beats rendered`}
          </div>
        )}

        <div className="flex items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">caption</span>
          <Button
            tone="ghost"
            className="ml-auto"
            disabled={writing}
            onClick={() => void studio.guard(() => api.caption(board.slug))}
            title="free — asks the local model to write it from the board"
          >
            {writing ? "writing…" : "↻ write it"}
          </Button>
        </div>
        <textarea
          className={`${inputClass} thin h-20 leading-relaxed`}
          value={caption.draft}
          onChange={(event) => caption.change(event.target.value)}
          onBlur={caption.flush}
          placeholder="caption and hashtags for the post"
        />

        {board.reel ? (
          <a
            href={board.reel}
            download
            className="block rounded bg-soft py-1.5 text-center text-xs
              text-zinc-800 hover:bg-softer"
          >
            ↓ download 1080×1920
          </a>
        ) : null}
      </div>
    </div>
  );
}
