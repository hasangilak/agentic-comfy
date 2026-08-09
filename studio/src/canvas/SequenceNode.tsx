import { Handle, Position } from "@xyflow/react";
import { useRef, useState } from "react";
import { api, clock, money } from "../api";
import { slotsLeft, videoPictures } from "../beat";
import type { Beat, Source } from "../types";
import { useDraft, useStudio } from "../useStudio";
import { Badge, STATE_LOOK, inputClass } from "../ui";
import { Panel } from "./Panel";

/**
 * The four joins, in the order the button walks them: the free continuation, the one that also
 * lands on a still, the default cut, then the exact-keyframe cut. Beat 1 cannot continue from
 * anything, so it only toggles between the two that stand on their own -- the two cuts.
 */
const JOIN_CYCLE: Record<Source, Source> = {
  chain: "bridge",
  bridge: "reference",
  reference: "asset",
  asset: "chain",
};

// A still is generated through Gemini while the image server is up, so what these describe is
// what each join buys and what it gives up -- not a price. The panel on the script node is where
// the live stills backend is named.
const JOIN_HELP: Record<Source, string> = {
  chain:
    "the same take carrying on — same set, same camera, needs no still at all. Click to keep " +
    "the continuation but make it land on a still of your own",
  bridge:
    "the same take carrying on, but it must arrive at this beat's own still on its last " +
    "frame: continuity plus a composition you chose. Needs one still. Click to make it a " +
    "clean cut instead",
  reference:
    "a clean cut, and the normal one: this scene opens on its own still and the model is shown " +
    "the reel's cast reference alongside it for the whole clip, so the puppets keep being held " +
    "to their design instead of only matching frame one. Add more pictures of the cast, the set " +
    "or a prop below. Every picture rides through every sampling step, so more of them means a " +
    "slower render. Click for a cut whose opening frame is exact instead",
  asset:
    "a clean cut whose opening frame is EXACT — the still is handed over as a keyframe, so the " +
    "clip begins on it pixel for pixel. Nothing else is supplied, so the cast is not re-asserted " +
    "for the rest of the take. Worth it when the first frame has to land precisely. Click to " +
    "carry the previous take on unbroken instead",
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
  // Two pickers, because a reference scene takes two different kinds of image and conflating
  // them is how a picture of the cast ends up as the frame the shot opens on. The media area and
  // the "opening still" row use `picker`; the picture strip has its own.
  const picker = useRef<HTMLInputElement>(null);
  const look = STATE_LOOK[beat.state];
  const renderSelected = studio.renderSelection.includes(beat.n);
  const canSelectForRender = !["planned", "needs_asset", "rendering"].includes(beat.state);
  // A plain continuation has no still of its own, so its thumbnail is the frame it opened on
  // -- the last frame of the clip before it. Every other join shows its own still, which for a
  // bridge is the frame it has to arrive at and on a reference cut is the composition it opens on.
  const thumb = beat.source === "chain" ? beat.frame : beat.asset;
  // What an uploaded or generated still MEANS here. On a bridge it is the ending, and saying
  // so is the difference between a picture that lands and a picture that replaces the join.
  const isBridge = beat.source === "bridge";
  // The reference join is the default cut: it has a still like the others, and a set of extra
  // pictures none of the others can take.
  const isReference = beat.source === "reference";
  const refs = beat.refs ?? [];
  // The whole conditioning set in the order the prompt numbers it, from the one module that
  // knows that order. `index` is the number the API addresses an upload by, and it is null on
  // an automatic slot -- those follow the still and the cast reference and cannot be edited here.
  const pictures = videoPictures(beat, board.staging ?? []);
  // `videoPictures` is empty off the reference join, mirroring the server -- so a beat whose join
  // was cycled away afterwards would show none of the pictures it still has on disk, and offer no
  // way to remove them. They reach no renderer there, which is worth SAYING rather than hiding:
  // silently dropping them from the canvas is how a director ends up believing a scene is
  // conditioned on something it is not.
  const stranded = isReference
    ? []
    : refs.map((url, i) => ({
        url,
        note: beat.ref_prompts?.[i] ?? "",
        index: i + 1,
        id: beat.ref_ids?.[i] ?? null,
        tag: "",
        label: `picture ${i + 1}`,
        token: null,
      }));
  // Against the per-beat budget, not the model's flat cap: two of the nine slots are already
  // spoken for on a scene that opens a shot.
  const refSlotsLeft = slotsLeft(beat);
  // The one reference shape with nowhere to put a still: it opens where the previous clip ended.
  const carrying = isReference && beat.carry;

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

  // The still. A bridge keeps its join -- the picture is the frame it lands on, not a replacement
  // for the continuation -- and so does an `asset` beat, whose whole point is the exact keyframe.
  // Everything else takes it as the default cut's opening composition, which is what promotes a
  // continuation to its own shot.
  const upload = (dropped: FileList | File[] | null | undefined) => {
    setDropping(false);
    const files = dropped ? Array.from(dropped) : [];
    const file = files[0];
    if (!file) return;
    // A scene carrying the previous clip opens where that one ended, so it has nowhere to put a
    // still -- the server refuses one. A picture dropped on it can only be a reference, so it is
    // taken as one rather than bounced with an error the drop gesture did not deserve.
    if (carrying) return uploadRefs(files);
    setUploading(true);
    void studio
      .guard(() =>
        api.uploadAsset(
          board.slug, beat.n, file,
          isBridge ? "bridge" : beat.source === "asset" ? "asset" : "reference",
        ),
      )
      .finally(() => setUploading(false));
  };

  // The extra pictures, appended after the ones already there because the prompt names them by
  // position -- inserting would re-point every note that follows at a different picture.
  const uploadRefs = (chosen: FileList | File[] | null | undefined) => {
    const wanted = (chosen ? Array.from(chosen) : []).slice(0, refSlotsLeft);
    if (!wanted.length) return;
    setUploading(true);
    void studio
      .guard(() => api.uploadRefs(board.slug, beat.n, wanted))
      .finally(() => setUploading(false));
  };

  const removeRef = (index: number) =>
    void studio.guard(() => api.removeRef(board.slug, beat.n, index));

  // A 16:9 still loses its sides to the vertical crop. Worth saying before it is rendered,
  // not after -- the source art in this repo is all landscape.
  const cropped =
    beat.asset_aspect !== null && Math.abs(beat.asset_aspect - board.gen_aspect) > 0.08;

  // Beat 1 has nothing before it, so the two continuations are unreachable -- but both cuts are
  // reachable: neither takes anything from upstream. So the first node toggles between the
  // default cut and the exact-keyframe one rather than being frozen.
  const nextSource: Source = beat.n === 1
    ? isReference ? "asset" : "reference"
    : JOIN_CYCLE[beat.source];

  const cycleSource = () =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { source: nextSource }));

  return (
    <div className={`lift w-64 overflow-hidden rounded-2xl border bg-panel ${look.ring}`}>
      <Handle type="target" position={Position.Left} />

      <div className="flex items-center gap-2 border-b border-edge px-2.5 py-1.5">
        <span className="text-xs font-medium text-zinc-700">{beat.n}</span>
        <Badge state={beat.state} />
        <label
          className={`nodrag nopan flex items-center gap-1 text-[10px] ${
            canSelectForRender
              ? "cursor-pointer text-zinc-600 hover:text-zinc-800"
              : "cursor-not-allowed text-zinc-300"
          }`}
          title={
            canSelectForRender
              ? "include this scene when you press Render"
              : beat.state === "needs_asset"
                ? isReference
                  ? "add the still this scene opens on — generate it, upload it, or add a " +
                    "reference picture instead"
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
            className="h-3 w-3 accent-warm"
          />
          render
        </label>
        {/* The same beat, full screen. A node is 240px wide so the whole chain stays readable;
            everything that needs looking AT rather than reading -- the still, the pictures it is
            drawn from, the conversation about them -- is unusable at that size. */}
        <button
          onClick={() => studio.setExpanded(beat.n)}
          className="nodrag ml-auto text-[10px] text-zinc-400 hover:text-warm"
          title="open this scene full screen — its stills, the pictures they are drawn from, and
            the conversation about them"
        >
          ⤢
        </button>
        <button
          onClick={() => void studio.guard(() => api.addBeat(board.slug, { n: beat.n }))}
          disabled={structureBusy}
          className="text-[10px] text-zinc-400 hover:text-warm
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
          className="text-[10px] text-zinc-400 hover:text-warm
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
          className="text-zinc-400 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-30"
          title={structureBusy ? "wait for the current job to finish" : "delete this scene"}
        >
          ×
        </button>
      </div>

      {/* Media. Letterboxed rather than cropped: this is 9:16 content and pretending
          otherwise would misrepresent the framing. Also the drop target for your own
          stills, for a scene you would rather draw than describe. */}
      <div
        className="relative h-40 bg-ink"
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
          className="hidden"
          onChange={(event) => {
            upload(event.target.files);
            // Cleared so re-picking the same file still fires a change event.
            event.target.value = "";
          }}
        />
        {thumb ? (
          <img src={thumb} alt="" className="h-full w-full object-contain opacity-90" />
        ) : (pictures.length || stranded.length) ? (
          /* No still, but pictures: a scene conditioned only on uploads, or one carrying the
             clip before it. Numbered, because the numbers are load-bearing -- the prompt tells
             the model about <Picture 1>..<Picture N> in exactly this order. */
          <div className="nodrag nowheel grid h-full auto-rows-[2.85rem] grid-cols-3 gap-0.5
            overflow-y-auto p-0.5">
            {(pictures.length ? pictures : stranded).map((picture, index) => (
              <div key={picture.url ?? index} className="group relative bg-ink">
                {picture.url ? (
                  <img src={picture.url} alt="" className="h-full w-full object-cover opacity-90" />
                ) : null}
                <span
                  className="absolute left-0.5 top-0.5 rounded bg-black/70 px-1 text-[9px]
                    text-zinc-700"
                  title={`the prompt calls this <Picture ${index + 1}>`}
                >
                  {index + 1}
                </span>
                {picture.index === null ? null : (
                  <button
                    onClick={() => removeRef(picture.index!)}
                    title={`remove <Picture ${index + 1}> — the rest are renumbered`}
                    className="absolute right-0.5 top-0.5 hidden rounded bg-black/70 px-1
                      text-[10px] text-zinc-700 hover:text-red-600 group-hover:block"
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-3
            text-center text-[10px] text-zinc-400">
            <span>
              {beat.source === "chain"
                ? `continues from beat ${beat.n - 1}`
                : isBridge
                  ? `add the still this scene lands on`
                  : carrying
                    ? `opens where beat ${beat.n - 1} ends — add reference pictures below`
                    : "add this scene's opening still"}
            </span>
          </div>
        )}

        {dropping || uploading ? (
          <div
            className="absolute inset-0 flex items-center justify-center border-2 border-dashed
              border-warm bg-black/70 text-[11px] text-warm"
          >
            {uploading
              ? "uploading…"
              : carrying
                ? refSlotsLeft
                  ? `drop to add a reference picture — ${refSlotsLeft} slot${
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
            <div className="mb-1 h-1 overflow-hidden rounded bg-soft">
              <div
                className="h-full bg-live transition-[width] duration-500"
                style={{ width: `${Math.round(fraction * 100)}%` }}
              />
            </div>
            <div className="flex justify-between text-[10px] text-zinc-600">
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
        <div className="border-t border-edge bg-ink">
          <div className="flex items-center gap-2 px-2.5 py-1.5">
            <span className="text-[10px] uppercase tracking-wide text-live">
              rendered output
            </span>
            <a
              href={beat.video}
              download
              className="nodrag ml-auto text-[10px] text-live hover:text-green-700"
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
                confirming ? "text-red-600" : "text-zinc-400 hover:text-red-600"
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
            className="nodrag nowheel h-36 w-full border-t border-edge bg-black object-contain"
            controls
            preload="metadata"
            loop
          />
        </div>
      ) : null}

      <div className="space-y-2 p-2.5">
        {/* The still, the pictures it is drawn from and the conversation about them all
            moved to the Assets stage, where there is room to look at a picture and to see what
            it was actually conditioned on. What is left here is the fact and the way there: the
            canvas is about the chain, and a 240px card was never where a still got judged. */}
        {carrying ? null : (
          <button
            onClick={() => studio.goStage("assets")}
            className="nodrag flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left
              text-[10px] leading-snug hover:bg-soft"
            title={
              isBridge
                ? "the still this scene has to land on, and what it is drawn from"
                : "the still this scene opens on, and what it is drawn from"
            }
          >
            <span className={beat.asset ? "text-zinc-500" : "text-warm"}>
              {beat.asset ? "◫" : "○"}
            </span>
            <span className="min-w-0 flex-1 truncate text-zinc-500">
              {beat.asset
                ? `${isBridge ? "closing" : "opening"} still` +
                  (refs.length ? ` · ${refs.length} picture${refs.length === 1 ? "" : "s"}` : "")
                : `needs ${isBridge ? "the still it lands on" : "an opening still"}`}
            </span>
            <span className="shrink-0 text-zinc-400">→</span>
          </button>
        )}

        {/* The storyboard sketch of this shot, above the pictures it is NOT one of: a panel reaches
            no renderer, so it belongs next to what the shot is rather than next to what conditions
            it. Absent entirely until this board has a storyboard. */}
        <Panel beat={beat} />

        {/* Which of the film's designs are in this shot. Read-only here and edited in the
            expanded view: at 240px the node can say WHICH, and a row of toggles for a bible of
            a dozen would be the widest thing on it. Shown only once something is bound, so a
            board with no design bible looks exactly as it did. */}
        {beat.staging?.length ? (
          <button
            onClick={() => studio.setStagingOpen(true)}
            className="nodrag flex w-full items-center gap-1 rounded px-1 py-0.5 text-left
              text-[10px] leading-snug text-zinc-500 hover:bg-soft"
            title={
              `this scene is conditioned on ${beat.staging.length} of the reel's designs — ` +
              `${beat.staging_refs} of them as pictures here` +
              (beat.staging_text ? `, the rest as words: ${beat.staging_text}` : "")
            }
          >
            <span className="shrink-0">🎭</span>
            <span className="min-w-0 flex-1 truncate">
              {beat.staging
                .map((id) => board.staging.find((entry) => entry.id === id)?.name ?? "?")
                .join(", ")}
            </span>
          </button>
        ) : null}

        {/* The reference join's only route to continuity. ref2va has no keyframe input, so
            the previous clip cannot be handed over as a frame -- but the node takes reference
            VIDEO, and its tail in that slot is the same idea: the model is shown where the
            take had got to instead of being told where to start. */}
        {isReference && beat.n > 1 ? (
          <label
            className="nodrag flex cursor-pointer items-start gap-1.5 rounded px-1 py-0.5
              text-[10px] leading-snug text-zinc-600 hover:bg-soft"
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
              className="mt-0.5 h-3 w-3 accent-live"
            />
            <span>
              carry the last seconds of beat {beat.n - 1} as{" "}
              <code>&lt;Video 1&gt;</code>
              {beat.carry ? (
                <span className="text-live"> · continues that take</span>
              ) : null}
            </span>
          </label>
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
            leading-snug text-zinc-600 hover:bg-soft disabled:opacity-40
            disabled:hover:bg-transparent"
          title={
            beat.n === 1
              ? isReference
                ? "the first scene cannot continue from anything, so it is one of the two cuts. " +
                  "Click for a cut whose opening frame is exact instead"
                : "the first scene cannot continue from anything, so it is one of the two cuts. " +
                  "Click for the normal cut, which also holds the cast for the whole clip"
              : JOIN_HELP[beat.source]
          }
        >
          {isReference ? (
            <>
              <span className="text-warm">◈</span>{" "}
              {beat.carry
                ? `carries beat ${beat.n - 1}`
                : beat.opens_on
                  ? "cut · opens on this still"
                  : "cut · no opening still yet"}{" "}
              · {pictures.length || "no"} picture{pictures.length === 1 ? "" : "s"}
            </>
          ) : beat.source === "chain" ? (
            <>
              <span className="text-live">↳</span> continues from beat {beat.n - 1}
            </>
          ) : beat.source === "bridge" ? (
            <>
              <span className="text-live">↳</span>
              <span className="text-warm">⇥</span> continues from beat {beat.n - 1} · lands
              on this still
            </>
          ) : (
            <>
              <span className="text-warm">✂</span> cut · opens on this still exactly
            </>
          )}
        </button>

        {/* Two lengths, no stepper. 10s is 243 frames -- exactly the longest render that
            has ever completed on this card -- so there is nothing above it worth offering. */}
        <div className="flex items-center justify-between border-t border-edge pt-2">
          <div className="flex items-center gap-1">
            {board.lengths.map((option) => {
              const active = Math.round(beat.seconds) === Math.round(option);
              return (
                <button
                  key={option}
                  onClick={() => setSeconds(option)}
                  className={`rounded px-2 py-0.5 text-[11px] transition-colors ${
                    active
                      ? "bg-solid font-medium text-white"
                      : "bg-soft text-zinc-600 hover:bg-softer"
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
          <p className="text-[10px] leading-snug text-stale">
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
 *
 * `index` addresses the upload on the server, where the files are numbered from 1. `label` is the
 * number the PROMPT uses, which is further along by however many slots filled themselves. The two
 * are separate arguments rather than one plus arithmetic because getting them confused attaches a
 * note to a different picture than the one it describes -- and nothing about the result looks
 * wrong until the render comes back with the cast reference acted out as a second character.
 */
export function ReferenceNote({
  slug,
  n,
  index,
  label,
  value,
}: {
  slug: string;
  n: number;
  index: number;
  label: number;
  value: string;
}) {
  const studio = useStudio();
  const note = useDraft(value, (next) =>
    // `prompt` only: the same route also carries the draw prompt, and a note edit must not
    // clear it. The server writes only the keys it is given, which is what makes that safe.
    void studio.guard(() => api.describeRef(slug, n, index, { prompt: next })),
  );

  return (
    <div className="flex items-center gap-1.5">
      <span
        className="w-4 shrink-0 text-center text-[10px] text-zinc-500"
        title={`the prompt calls this <Picture ${label}>`}
      >
        {label}
      </span>
      <input
        className={inputClass}
        value={note.draft}
        onChange={(event) => note.change(event.target.value)}
        onBlur={note.flush}
        placeholder={`what <Picture ${label}> is for`}
        title="what the model should take from this picture — identity, set, a prop. Leave
          empty and it decides for itself"
      />
    </div>
  );
}
