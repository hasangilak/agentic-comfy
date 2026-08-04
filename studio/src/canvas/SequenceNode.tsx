import { Handle, Position } from "@xyflow/react";
import { useState } from "react";
import { api, clock, money } from "../api";
import type { Beat } from "../types";
import { useDraft, useStudio } from "../useStudio";
import { Badge, Button, STATE_LOOK, inputClass } from "../ui";

const MIN_SECONDS = 5.2; // the model's 124-frame floor
const MAX_SECONDS = 15.0;

/**
 * One shot. The unit of both storytelling and spending: everything on this card either
 * describes what moves, or tells you what it will cost to find out.
 */
export function SequenceNode({ data }: { data: { beat: Beat } }) {
  const { beat } = data;
  const studio = useStudio();
  const board = studio.board!;
  const [playing, setPlaying] = useState(false);
  const look = STATE_LOOK[beat.state];
  // A chained beat has no still of its own, so its thumbnail is the frame it opened on --
  // which is the last frame of the clip before it.
  const thumb = beat.asset ?? beat.frame;

  const action = useDraft(beat.action, (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { action: next })),
  );

  const job = studio.activeJob;
  const isRendering = beat.state === "rendering";
  const elapsed = isRendering && job?.beat_started_at ? Date.now() / 1000 - job.beat_started_at : 0;
  const remaining = Math.max(0, beat.predicted_seconds - elapsed);
  // Sampling steps dominate the render, so step progress is a fair stand-in for the beat.
  const fraction =
    job && job.step_max > 0 ? job.step / job.step_max : Math.min(0.98, elapsed / beat.predicted_seconds);

  const setSeconds = (delta: number) => {
    const next = Math.min(MAX_SECONDS, Math.max(MIN_SECONDS, beat.seconds + delta));
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { seconds: next }));
  };

  const toggleSource = () =>
    void studio.guard(() =>
      api.patchBeat(board.slug, beat.n, {
        source: beat.source === "chain" ? "asset" : "chain",
      }),
    );

  return (
    <div className={`w-60 rounded-lg border bg-[#16161b] shadow-lg ${look.ring}`}>
      <Handle type="target" position={Position.Left} />

      <div className="flex items-center gap-2 border-b border-[#26262e] px-2.5 py-1.5">
        <span className="text-xs font-medium text-zinc-300">{beat.n}</span>
        <Badge state={beat.state} />
        <button
          onClick={() => void studio.guard(() => api.removeBeat(board.slug, beat.n))}
          className="ml-auto text-zinc-600 hover:text-red-400"
          title="delete this beat"
        >
          ×
        </button>
      </div>

      {/* Media. Letterboxed rather than cropped: this is 9:16 content and pretending
          otherwise would misrepresent the framing. */}
      <div className="relative h-36 bg-black">
        {playing && beat.video ? (
          <video src={beat.video} className="h-full w-full object-contain" controls autoPlay loop />
        ) : thumb || beat.video ? (
          <>
            <img
              src={thumb ?? undefined}
              alt=""
              className="h-full w-full object-contain opacity-90"
            />
            {beat.video ? (
              <button
                onClick={() => setPlaying(true)}
                className="absolute inset-0 flex items-center justify-center bg-black/30
                  text-2xl text-white/80 transition hover:bg-black/10 hover:text-white"
                title="play this clip"
              >
                ▶
              </button>
            ) : null}
          </>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-[10px] text-zinc-600">
            {beat.state === "needs_asset" ? (
              <>
                <span>opens on its own still</span>
                <Button
                  tone="quiet"
                  onClick={() => void studio.guard(() => api.assets(board.slug, [beat.n]))}
                  title="uses one image from a quota of roughly five per five hours"
                >
                  generate a still
                </Button>
              </>
            ) : (
              <span>continues from beat {beat.n - 1}</span>
            )}
          </div>
        )}

        {isRendering ? (
          <div className="absolute inset-x-0 bottom-0 bg-black/80 px-2 py-1.5">
            <div className="mb-1 h-1 overflow-hidden rounded bg-[#26262e]">
              <div
                className="h-full bg-[#4ade80] transition-[width] duration-500"
                style={{ width: `${Math.round(fraction * 100)}%` }}
              />
            </div>
            <div className="flex justify-between text-[10px] text-zinc-400">
              <span>
                {job && job.step_max > 0 ? `step ${job.step}/${job.step_max}` : "sampling"}
              </span>
              <span>
                {clock(elapsed)} · ~{clock(remaining)} left
              </span>
            </div>
          </div>
        ) : null}
      </div>

      <div className="space-y-2 p-2.5">
        <textarea
          className={`${inputClass} thin h-20 leading-relaxed`}
          value={action.draft}
          onChange={(event) => action.change(event.target.value)}
          onBlur={action.flush}
          placeholder="what MOVES in this shot — the camera never moves"
        />

        <button
          onClick={toggleSource}
          disabled={beat.n === 1}
          className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-[10px]
            text-zinc-400 hover:bg-[#26262e] disabled:opacity-40 disabled:hover:bg-transparent"
          title={
            beat.n === 1
              ? "the first beat has nothing to continue from"
              : beat.source === "chain"
                ? "continuous motion, costs no image quota — click for a clean cut instead"
                : "a clean cut, costs one image from the quota — click to continue instead"
          }
        >
          {beat.source === "chain" ? (
            <>
              <span className="text-[#4ade80]">↳</span> continues from beat {beat.n - 1}
            </>
          ) : (
            <>
              <span className="text-[#d99a4e]">✂</span> own still · 1 image
            </>
          )}
        </button>

        <div className="flex items-center justify-between border-t border-[#26262e] pt-2">
          <div className="flex items-center gap-1">
            <button
              onClick={() => setSeconds(-1)}
              className="h-5 w-5 rounded bg-[#26262e] text-zinc-400 hover:bg-[#32323c]"
            >
              −
            </button>
            <span
              className={`w-14 text-center text-[11px] ${
                beat.over_proven ? "text-[#f59e0b]" : "text-zinc-300"
              }`}
              title={`${beat.frames} frames, snapped onto the model's frame grid`}
            >
              {beat.actual_seconds.toFixed(1)}s
            </span>
            <button
              onClick={() => setSeconds(1)}
              className="h-5 w-5 rounded bg-[#26262e] text-zinc-400 hover:bg-[#32323c]"
            >
              +
            </button>
          </div>
          <span className="text-[11px] text-zinc-500" title="predicted, from measured render rates">
            {money(beat.predicted_cost)}
          </span>
        </div>

        {beat.over_proven ? (
          <p className="text-[10px] leading-snug text-[#f59e0b]">
            {beat.frames} frames is past the proven limit — a render this long has failed on
            this card before.
          </p>
        ) : null}

        {beat.render && beat.state === "rendered" ? (
          <p className="text-[10px] text-zinc-600">
            took {clock(beat.render.render_seconds)} · cost {money(beat.render.cost)}
          </p>
        ) : null}
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
