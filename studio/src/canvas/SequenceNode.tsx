import { Handle, Position } from "@xyflow/react";
import { useRef, useState } from "react";
import { api, clock, money } from "../api";
import type { Beat } from "../types";
import { useDraft, useStudio } from "../useStudio";
import { Badge, Button, STATE_LOOK, inputClass } from "../ui";

/**
 * One shot. The unit of both storytelling and spending: everything on this card either
 * describes what moves, or tells you what it will cost to find out.
 */
export function SequenceNode({ data }: { data: { beat: Beat } }) {
  const { beat } = data;
  const studio = useStudio();
  const board = studio.board!;
  const [playing, setPlaying] = useState(false);
  const [dropping, setDropping] = useState(false);
  const [uploading, setUploading] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const look = STATE_LOOK[beat.state];
  // A chained beat has no still of its own, so its thumbnail is the frame it opened on --
  // which is the last frame of the clip before it.
  const thumb = beat.source === "asset" ? beat.asset : beat.frame;

  const action = useDraft(beat.action, (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { action: next })),
  );

  const job = studio.activeJob;
  const isRendering = beat.state === "rendering";
  const assetJob = Object.values(studio.jobs).find(
    (candidate) =>
      candidate.kind === "asset" &&
      candidate.slug === board.slug &&
      (candidate.state === "queued" || candidate.state === "running") &&
      Array.isArray(candidate.detail.beats) &&
      candidate.detail.beats.includes(beat.n),
  );
  const isGenerating = Boolean(assetJob);
  const structureBusy = Object.values(studio.jobs).some(
    (candidate) =>
      candidate.slug === board.slug &&
      (candidate.state === "queued" || candidate.state === "running"),
  );
  const elapsed = isRendering && job?.beat_started_at ? Date.now() / 1000 - job.beat_started_at : 0;
  const remaining = Math.max(0, beat.predicted_seconds - elapsed);
  // Sampling steps dominate the render, so step progress is a fair stand-in for the beat.
  const fraction =
    job && job.step_max > 0 ? job.step / job.step_max : Math.min(0.98, elapsed / beat.predicted_seconds);

  const setSeconds = (next: number) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { seconds: next }));

  const upload = (file: File | undefined) => {
    setDropping(false);
    if (!file) return;
    setUploading(true);
    void studio
      .guard(() => api.uploadAsset(board.slug, beat.n, file))
      .finally(() => setUploading(false));
  };

  // A 16:9 still loses its sides to the vertical crop. Worth saying before it is rendered,
  // not after -- the source art in this repo is all landscape.
  const cropped =
    beat.asset_aspect !== null && Math.abs(beat.asset_aspect - board.gen_aspect) > 0.08;

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
          onClick={() => void studio.guard(() => api.addBeat(board.slug, { n: beat.n }))}
          disabled={structureBusy}
          className="ml-auto text-[10px] text-zinc-600 hover:text-[#d99a4e]
            disabled:cursor-not-allowed disabled:opacity-30"
          title={
            structureBusy
              ? "wait for the current job to finish"
              : "insert a new scene before this one"
          }
        >
          + before
        </button>
        <button
          onClick={() => void studio.guard(() => api.addBeat(board.slug, { n: beat.n + 1 }))}
          disabled={structureBusy}
          className="text-[10px] text-zinc-600 hover:text-[#d99a4e]
            disabled:cursor-not-allowed disabled:opacity-30"
          title={
            structureBusy
              ? "wait for the current job to finish"
              : "insert a new scene after this one"
          }
        >
          + after
        </button>
        <button
          onClick={() => void studio.guard(() => api.removeBeat(board.slug, beat.n))}
          disabled={structureBusy}
          className="text-zinc-600 hover:text-red-400 disabled:cursor-not-allowed disabled:opacity-30"
          title={structureBusy ? "wait for the current job to finish" : "delete this scene"}
        >
          ×
        </button>
      </div>

      {/* Media. Letterboxed rather than cropped: this is 9:16 content and pretending
          otherwise would misrepresent the framing. Also the drop target for your own
          stills, which is how you avoid spending image quota at all. */}
      <div
        className="relative h-36 bg-black"
        onDragOver={(event) => {
          event.preventDefault();
          setDropping(true);
        }}
        onDragLeave={() => setDropping(false)}
        onDrop={(event) => {
          event.preventDefault();
          upload(event.dataTransfer.files?.[0]);
        }}
      >
        <input
          ref={picker}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={(event) => upload(event.target.files?.[0] ?? undefined)}
        />
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
            <span>
              {beat.source === "asset"
                ? "add this scene's opening still"
                : `continues from beat ${beat.n - 1}`}
            </span>
          </div>
        )}

        {dropping || uploading ? (
          <div
            className="absolute inset-0 flex items-center justify-center border-2 border-dashed
              border-[#d99a4e] bg-black/70 text-[11px] text-[#d99a4e]"
          >
            {uploading ? "uploading…" : "drop to use as this beat's still"}
          </div>
        ) : null}

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
        {/* Asset preparation is available on every node, regardless of whether it currently
            continues from the previous clip or already has media. Both actions make this
            scene a clean cut; they never need a video render to become available. */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">opening still</span>
          <Button
            tone="ghost"
            className="ml-auto"
            disabled={uploading}
            onClick={() => picker.current?.click()}
            title="use your own image for this scene — costs no quota"
          >
            {uploading ? "uploading…" : beat.asset ? "⤒ replace" : "⤒ upload"}
          </Button>
          <Button
            tone="ghost"
            disabled={isGenerating}
            onClick={() => void studio.guard(() => api.assets(board.slug, [beat.n]))}
            title="generate an opening still for this scene and make it a clean cut"
          >
            {isGenerating ? "generating…" : beat.asset ? "✦ regenerate" : "✦ generate"}
          </Button>
        </div>

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

        {/* Two lengths, no stepper. 10s is 243 frames -- exactly the longest render that
            has ever completed on this card -- so there is nothing above it worth offering. */}
        <div className="flex items-center justify-between border-t border-[#26262e] pt-2">
          <div className="flex items-center gap-1">
            {board.lengths.map((option) => {
              const active = Math.round(beat.seconds) === Math.round(option);
              return (
                <button
                  key={option}
                  onClick={() => setSeconds(option)}
                  className={`rounded px-2 py-0.5 text-[11px] transition-colors ${
                    active
                      ? "bg-[#d99a4e] font-medium text-[#1a1208]"
                      : "bg-[#26262e] text-zinc-400 hover:bg-[#32323c]"
                  }`}
                  title={
                    active
                      ? `${beat.frames} frames, snapped onto the model's frame grid`
                      : `switch this beat to ${option}s`
                  }
                >
                  {option}s
                </button>
              );
            })}
          </div>
          <span className="text-[11px] text-zinc-500" title="predicted, from measured render rates">
            {money(beat.predicted_cost)}
          </span>
        </div>

        {cropped ? (
          <p className="text-[10px] leading-snug text-[#f59e0b]">
            This still is not 9:16 — its sides will be cropped away to fit the vertical frame.
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
