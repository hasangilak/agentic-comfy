import { Handle, Position } from "@xyflow/react";
import { api, money } from "../api";
import { useDraft, useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";

/** The finished thing: the stitched 1080x1920 file, plus the caption that ships with it. */
export function ReelNode() {
  const studio = useStudio();
  const board = studio.board!;
  const seconds = board.beats.reduce((sum, beat) => sum + beat.actual_seconds, 0);
  const missing = board.beats.filter((beat) => !beat.video).map((beat) => beat.n);
  const rendered = board.beats.length - missing.length;
  const complete = missing.length === 0 && board.beats.length > 0;

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
        <span
          className="ml-auto text-[10px] text-zinc-500"
          title={
            // The ledger, per scene: what this film actually cost, which is the last question a
            // Reels tool has to answer. Off `beat.render.cost`, stamped at render time.
            board.beats
              .filter((beat) => beat.render)
              .map((beat) => `scene ${beat.n}: ${money(beat.render!.cost)}`)
              .join("\n") || "nothing rendered yet"
          }
        >
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
            className="flex h-52 flex-col items-center justify-center gap-1.5 rounded-xl bg-ink
              px-3 text-center text-[10px] leading-relaxed text-zinc-400"
          >
            {complete ? (
              "render again to stitch"
            ) : (
              <>
                <span>
                  {rendered} of {board.beats.length} scenes rendered
                </span>
                {/* Which ones, by number and clickable. "3 of 5" leaves the director counting
                    the nodes to find the two that are missing, and the answer is right here. */}
                <span className="flex flex-wrap justify-center gap-1">
                  {missing.map((n) => (
                    <button
                      key={n}
                      onClick={() => studio.setExpanded(n)}
                      className="nodrag rounded bg-soft px-1.5 py-0.5 text-warm hover:bg-softer"
                      title={`scene ${n} has no clip yet`}
                    >
                      {n}
                    </button>
                  ))}
                </span>
              </>
            )}
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
