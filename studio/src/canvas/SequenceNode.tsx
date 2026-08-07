import { Handle, Position } from "@xyflow/react";
import { useRef, useState } from "react";
import { api, clock, money } from "../api";
import type { Beat, Source } from "../types";
import { useDraft, useStudio } from "../useStudio";
import { Badge, Button, STATE_LOOK, inputClass } from "../ui";

/**
 * The four joins, in the order the button walks them: free, then the two that cost an image,
 * then the reference join. Beat 1 cannot continue from anything, so it only toggles between
 * the two joins that stand on their own -- its own still, or its own reference pictures.
 */
const JOIN_CYCLE: Record<Source, Source> = {
  chain: "bridge",
  bridge: "asset",
  asset: "reference",
  reference: "chain",
};

// What each join costs is stated as "one still", not "one image from the quota": a still is
// free and unmetered when the local mflux renderer is up and rationed at roughly five per
// five hours when it is not, and the join is the same join either way. The panel on the
// script node is where the live backend is named.
const JOIN_HELP: Record<Source, string> = {
  chain:
    "the same take carrying on — same set, same camera, needs no still at all. Click to keep " +
    "the continuation but make it land on a still of your own",
  bridge:
    "the same take carrying on, but it must arrive at this beat's own still on its last " +
    "frame: continuity plus a composition you chose. Needs one still. Click to make it a " +
    "clean cut instead",
  asset:
    "a clean cut to a new setting, needs one still of its own. Click to condition this " +
    "scene on reference pictures instead of an opening still",
  reference:
    "no opening frame at all: the model is shown your reference pictures of the cast and the " +
    "set and composes the shot itself. Uploads only, so nothing is generated — but every " +
    "picture is carried through every sampling step, so more of them means a slower render. " +
    "Click to carry the previous take on unbroken instead",
};

/**
 * One shot. The unit of both storytelling and spending: everything on this card either
 * describes what moves, or tells you what it will cost to find out.
 */
export function SequenceNode({ data }: { data: { beat: Beat } }) {
  const { beat } = data;
  const studio = useStudio();
  const board = studio.board!;
  const [dropping, setDropping] = useState(false);
  const [uploading, setUploading] = useState(false);
  // Armed for one click, then it disarms itself -- a delete control that stays hot is one
  // stray click away from throwing away a render.
  const [confirming, setConfirming] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const look = STATE_LOOK[beat.state];
  const renderSelected = studio.renderSelection.includes(beat.n);
  const canSelectForRender = !["planned", "needs_asset", "rendering"].includes(beat.state);
  // A plain continuation has no still of its own, so its thumbnail is the frame it opened on
  // -- the last frame of the clip before it. A cut and a bridge both show their own still,
  // which for a bridge is the frame it has to arrive at.
  const thumb = beat.source === "chain" ? beat.frame : beat.asset;
  // What an uploaded or generated still MEANS here. On a bridge it is the ending, and saying
  // so is the difference between a picture that lands and a picture that replaces the join.
  const isBridge = beat.source === "bridge";
  // The reference join has no still at all -- it has a set of pictures, shown as a grid where
  // every other join shows one frame.
  const isReference = beat.source === "reference";
  const refs = beat.refs ?? [];
  const refSlotsLeft = Math.max(0, board.max_refs - refs.length);

  const action = useDraft(beat.action, (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { action: next })),
  );
  // In the video prompt alongside the action, so it is an input to the render rather than a
  // note to yourself -- which is why it is editable here and marks the beat stale when changed.
  const scene = useDraft(beat.scene, (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { scene: next })),
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

  // A bridge keeps its join: the picture is the frame it lands on, not a replacement for the
  // continuation. Every other join treats a supplied still as the beat's opening frame, which
  // makes it a cut. On the reference join a drop is a set of pictures instead, appended after
  // the ones already there, because the prompt names them by position.
  const upload = (dropped: FileList | File[] | null | undefined) => {
    setDropping(false);
    const files = dropped ? Array.from(dropped) : [];
    if (!files.length) return;
    const wanted = isReference ? files.slice(0, refSlotsLeft) : files.slice(0, 1);
    if (!wanted.length) return;
    setUploading(true);
    void studio
      .guard(() =>
        isReference
          ? api.uploadRefs(board.slug, beat.n, wanted)
          : api.uploadAsset(board.slug, beat.n, wanted[0], isBridge ? "bridge" : "asset"),
      )
      .finally(() => setUploading(false));
  };

  const removeRef = (index: number) =>
    void studio.guard(() => api.removeRef(board.slug, beat.n, index));

  // A 16:9 still loses its sides to the vertical crop. Worth saying before it is rendered,
  // not after -- the source art in this repo is all landscape.
  const cropped =
    beat.asset_aspect !== null && Math.abs(beat.asset_aspect - board.gen_aspect) > 0.08;

  // Beat 1 has nothing before it, so the two continuations are unreachable -- but the
  // reference join is not: its pictures come from nowhere upstream. So the first node
  // toggles between its own still and its own references rather than being frozen.
  const nextSource: Source = beat.n === 1
    ? isReference ? "asset" : "reference"
    : JOIN_CYCLE[beat.source];

  const cycleSource = () =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { source: nextSource }));

  return (
    <div className={`w-60 rounded-lg border bg-[#16161b] shadow-lg ${look.ring}`}>
      <Handle type="target" position={Position.Left} />

      <div className="flex items-center gap-2 border-b border-[#26262e] px-2.5 py-1.5">
        <span className="text-xs font-medium text-zinc-300">{beat.n}</span>
        <Badge state={beat.state} />
        <label
          className={`nodrag nopan flex items-center gap-1 text-[10px] ${
            canSelectForRender
              ? "cursor-pointer text-zinc-400 hover:text-zinc-200"
              : "cursor-not-allowed text-zinc-700"
          }`}
          title={
            canSelectForRender
              ? "include this scene when you press Render"
              : beat.state === "needs_asset"
                ? isReference
                  ? "add at least one reference picture before rendering this scene"
                  : "add an opening still before rendering this scene"
                : "write the movement before rendering this scene"
          }
        >
          <input
            type="checkbox"
            checked={renderSelected}
            disabled={!canSelectForRender}
            onChange={() =>
              studio.setRenderSelection((current) =>
                current.includes(beat.n)
                  ? current.filter((n) => n !== beat.n)
                  : [...current, beat.n].sort((a, b) => a - b),
              )
            }
            className="h-3 w-3 accent-[#d99a4e]"
          />
          render
        </label>
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
          upload(event.dataTransfer.files);
        }}
      >
        <input
          ref={picker}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple={isReference}
          className="hidden"
          onChange={(event) => {
            upload(event.target.files);
            // Cleared so re-picking the same file still fires a change event.
            event.target.value = "";
          }}
        />
        {isReference ? (
          refs.length ? (
            /* Numbered, because the numbers are load-bearing: the prompt tells the model
               about <Picture 1>..<Picture N> in exactly this order. */
            <div className="nodrag nowheel grid h-full auto-rows-[2.85rem] grid-cols-3 gap-0.5
              overflow-y-auto p-0.5">
              {refs.map((src, index) => (
                <div key={src} className="group relative bg-[#0d0d10]">
                  <img src={src} alt="" className="h-full w-full object-cover opacity-90" />
                  <span
                    className="absolute left-0.5 top-0.5 rounded bg-black/70 px-1 text-[9px]
                      text-zinc-300"
                    title={`the prompt calls this <Picture ${index + 1}>`}
                  >
                    {index + 1}
                  </span>
                  <button
                    onClick={() => removeRef(index + 1)}
                    title={`remove <Picture ${index + 1}> — the rest are renumbered`}
                    className="absolute right-0.5 top-0.5 hidden rounded bg-black/70 px-1
                      text-[10px] text-zinc-300 hover:text-red-400 group-hover:block"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-1 px-3
              text-center text-[10px] text-zinc-600">
              <span>drop up to {board.max_refs} reference pictures</span>
              <span className="text-zinc-700">
                {beat.carry
                  ? `the cast and the set — this scene opens where beat ${beat.n - 1} ends`
                  : "the cast and the set — this scene has no opening frame"}
              </span>
            </div>
          )
        ) : thumb ? (
          <img src={thumb} alt="" className="h-full w-full object-contain opacity-90" />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-[10px] text-zinc-600">
            <span>
              {beat.source === "chain"
                ? `continues from beat ${beat.n - 1}`
                : isBridge
                  ? `add the still this scene lands on`
                  : "add this scene's opening still"}
            </span>
          </div>
        )}

        {dropping || uploading ? (
          <div
            className="absolute inset-0 flex items-center justify-center border-2 border-dashed
              border-[#d99a4e] bg-black/70 text-[11px] text-[#d99a4e]"
          >
            {uploading
              ? "uploading…"
              : isReference
                ? refSlotsLeft
                  ? `drop to add reference pictures — ${refSlotsLeft} slot${
                      refSlotsLeft === 1 ? "" : "s"
                    } left`
                  : `${board.max_refs} pictures is the model's limit — remove one first`
                : isBridge
                  ? "drop to use as the frame this beat lands on"
                  : "drop to use as this beat's still"}
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

      {beat.video ? (
        <div className="border-t border-[#26262e] bg-[#0d0d10]">
          <div className="flex items-center gap-2 px-2.5 py-1.5">
            <span className="text-[10px] uppercase tracking-wide text-[#4ade80]">
              rendered output
            </span>
            <a
              href={beat.video}
              download
              className="nodrag ml-auto text-[10px] text-[#4ade80] hover:text-green-300"
              title="download this scene's rendered clip"
            >
              ↓ clip
            </a>
            {/* Two clicks, because this is the one thing on the canvas that cost money.
                The file is only moved into the reel's .discarded/, so a wrong second click
                is still recoverable from disk. */}
            <button
              onClick={() => {
                if (!confirming) {
                  setConfirming(true);
                  window.setTimeout(() => setConfirming(false), 4000);
                  return;
                }
                setConfirming(false);
                void studio.guard(() => api.discardClip(board.slug, beat.n));
              }}
              disabled={isRendering}
              className={`nodrag text-[10px] disabled:cursor-not-allowed disabled:opacity-30 ${
                confirming ? "text-red-400" : "text-zinc-600 hover:text-red-400"
              }`}
              title={
                isRendering
                  ? "this scene is rendering; cancel the job first"
                  : confirming
                    ? "click again to discard — the clip moves to the reel's .discarded folder"
                    : "not happy with this take? discard it and the scene goes back to ready"
              }
            >
              {confirming ? "discard?" : "× clip"}
            </button>
          </div>
          <video
            src={beat.video}
            className="nodrag nowheel h-36 w-full border-t border-[#26262e] bg-black object-contain"
            controls
            preload="metadata"
            loop
          />
        </div>
      ) : null}

      <div className="space-y-2 p-2.5">
        {/* Asset preparation is available on every node, regardless of whether it currently
            continues from the previous clip or already has media, and never needs a video
            render to become available. On a bridge the still is the frame the scene lands on,
            so both actions leave the join alone; on any other join they make it a clean cut. */}
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">
            {isReference
              ? `references ${refs.length}/${board.max_refs}`
              : isBridge
                ? "closing still"
                : "opening still"}
          </span>
          <Button
            tone="ghost"
            className="ml-auto"
            disabled={uploading || (isReference && refSlotsLeft === 0)}
            onClick={() => picker.current?.click()}
            title={
              isReference
                ? refSlotsLeft
                  ? "add pictures of the cast and the set — costs no quota, but each one is " +
                    "carried through every sampling step"
                  : `${board.max_refs} is the model's limit; remove one to add another`
                : isBridge
                  ? "use your own image as the frame this scene lands on — costs no quota"
                  : "use your own image for this scene — costs no quota"
            }
          >
            {uploading ? "uploading…" : isReference ? "⤒ add" : beat.asset ? "⤒ replace" : "⤒ upload"}
          </Button>
          {/* Absent, not disabled, when the reel supplies its own stills: a greyed button
              still reads as "the way this is meant to work". The switch back is on the
              script node, next to the count of what is missing. A reference scene has no
              opening still to generate at all, so the affordance goes with it. */}
          {board.manual_stills || isReference ? null : (
            <Button
              tone="ghost"
              disabled={isGenerating}
              onClick={() => void studio.guard(() => api.assets(board.slug, [beat.n]))}
              title={
                isBridge
                  ? "generate the still this scene has to land on, keeping the continuation — " +
                    "the cast is matched to the reference on the script node"
                  : board.reference
                    ? "generate an opening still for this scene and make it a clean cut — the " +
                      "cast is matched to the reference on the script node, so only the setting changes"
                    : "generate an opening still for this scene and make it a clean cut — this " +
                      "is the first still, so it will become the reel's cast reference"
              }
            >
              {isGenerating ? "generating…" : beat.asset ? "✦ regenerate" : "✦ generate"}
            </Button>
          )}
        </div>

        {/* The reference join's only route to continuity. ref2va has no keyframe input, so
            the previous clip cannot be handed over as a frame -- but the node takes reference
            VIDEO, and its tail in that slot is the same idea: the model is shown where the
            take had got to instead of being told where to start. */}
        {isReference && beat.n > 1 ? (
          <label
            className="nodrag flex cursor-pointer items-start gap-1.5 rounded px-1 py-0.5
              text-[10px] leading-snug text-zinc-400 hover:bg-[#26262e]"
            title={
              "sends the last few seconds of the previous clip as <Video 1>, and tells the " +
              "model to open where it ends and carry the movement on. Makes this scene " +
              "depend on that one again, so re-rendering it marks this one as following a " +
              "change"
            }
          >
            <input
              type="checkbox"
              checked={beat.carry}
              onChange={(event) =>
                void studio.guard(() =>
                  api.patchBeat(board.slug, beat.n, { carry: event.target.checked }),
                )
              }
              className="mt-0.5 h-3 w-3 accent-[#4ade80]"
            />
            <span>
              carry the last seconds of beat {beat.n - 1} as{" "}
              <code>&lt;Video 1&gt;</code>
              {beat.carry ? (
                <span className="text-[#4ade80]"> · continues that take</span>
              ) : null}
            </span>
          </label>
        ) : null}

        {/* One line per picture, numbered to match the badges on the grid above. This is
            what stops the model rendering a reference as a second copy of the character:
            shown a picture with no explanation it assumes the picture IS the scene. */}
        {isReference && refs.length ? (
          <div className="space-y-1">
            {refs.map((src, index) => (
              <ReferenceNote
                key={src}
                slug={board.slug}
                n={beat.n}
                index={index + 1}
                value={beat.ref_prompts?.[index] ?? ""}
              />
            ))}
            <p className="text-[10px] leading-snug text-zinc-600">
              The prompt calls these <code>&lt;Picture 1&gt;</code>…
              <code>&lt;Picture {refs.length}&gt;</code>. Say what each one is FOR — “the same
              single Moth that performs the action”, “the set only, no puppet”.
            </p>
          </div>
        ) : null}

        <input
          className={inputClass}
          value={scene.draft}
          onChange={(event) => scene.change(event.target.value)}
          onBlur={scene.flush}
          placeholder="where this shot happens"
          title="the setting, in the prompt with the action: where this beat is and at what
            scale. Beats in one continuous shot should carry the same line"
        />

        <textarea
          className={`${inputClass} thin h-20 leading-relaxed`}
          value={action.draft}
          onChange={(event) => action.change(event.target.value)}
          onBlur={action.flush}
          placeholder="what MOVES in this shot — the camera never moves"
        />

        <button
          onClick={cycleSource}
          className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-[10px]
            leading-snug text-zinc-400 hover:bg-[#26262e] disabled:opacity-40
            disabled:hover:bg-transparent"
          title={
            beat.n === 1
              ? isReference
                ? "the first scene cannot continue from anything, so it is either its own " +
                  "still or its own reference pictures. Click for a still"
                : "the first scene cannot continue from anything, so it is either its own " +
                  "still or its own reference pictures. Click for references"
              : JOIN_HELP[beat.source]
          }
        >
          {isReference ? (
            <>
              <span className="text-[#d99a4e]">◈</span> composed from {refs.length || "no"}{" "}
              reference picture{refs.length === 1 ? "" : "s"} ·{" "}
              {beat.carry ? `carries beat ${beat.n - 1}` : "no opening frame"}
            </>
          ) : beat.source === "chain" ? (
            <>
              <span className="text-[#4ade80]">↳</span> continues from beat {beat.n - 1}
            </>
          ) : beat.source === "bridge" ? (
            <>
              <span className="text-[#4ade80]">↳</span>
              <span className="text-[#d99a4e]">⇥</span> continues from beat {beat.n - 1} · lands
              on this still · 1 image
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

        {/* Only keyframes are cropped onto the 9:16 grid. A reference picture is scaled by
            the model itself, aspect preserved, so warning about it would be a lie. */}
        {cropped && !isReference ? (
          <p className="text-[10px] leading-snug text-[#f59e0b]">
            This still is not 9:16 — its sides will be cropped away to fit the vertical frame.
          </p>
        ) : null}

        {beat.render && beat.state === "rendered" ? (
          <p className="text-[10px] text-zinc-500">
            rendered in {clock(beat.render.render_seconds)} · cost {money(beat.render.cost)}
          </p>
        ) : null}
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}

/**
 * The job of one reference picture, in the model's own words.
 *
 * Its own component because each row needs its own debounced draft, and a hook cannot live
 * inside a map. Saving marks the beat stale, exactly like editing the action -- these words
 * are in the render prompt, not a note to yourself.
 */
function ReferenceNote({
  slug,
  n,
  index,
  value,
}: {
  slug: string;
  n: number;
  index: number;
  value: string;
}) {
  const studio = useStudio();
  const note = useDraft(value, (next) =>
    void studio.guard(() => api.describeRef(slug, n, index, next)),
  );

  return (
    <div className="flex items-center gap-1.5">
      <span
        className="w-4 shrink-0 text-center text-[10px] text-zinc-500"
        title={`the prompt calls this <Picture ${index}>`}
      >
        {index}
      </span>
      <input
        className={inputClass}
        value={note.draft}
        onChange={(event) => note.change(event.target.value)}
        onBlur={note.flush}
        placeholder={`what <Picture ${index}> is for`}
        title="what the model should take from this picture — identity, set, a prop. Leave
          empty and it decides for itself"
      />
    </div>
  );
}
