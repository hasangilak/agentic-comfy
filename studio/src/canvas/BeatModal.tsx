import { useEffect, useRef, useState } from "react";
import { api, clock, money } from "../api";
import { panelUrls, slotsLeft, stillPictures } from "../beat";
import {
  DEFAULT_GEMINI_IMAGE_MODEL,
  DEFAULT_GEMINI_IMAGE_SIZE,
  GEMINI_IMAGE_MODELS,
  GEMINI_IMAGE_SIZES,
  type Beat,
  type Board,
  type GeminiImageModel,
  type GeminiImageSize,
} from "../types";
import { useBusy, useDraft, useStudio } from "../useStudio";
import { stillsAllowed } from "../route";
import { Badge, Button, inputClass } from "../ui";
import { AddPicture, type AddPictureHandle } from "./AddPicture";
import { PromptField } from "./Mentions";
import { NewPicture, PicturePanel } from "./PicturePanel";
import { ReviseField } from "./ReviseField";
import { StagingBind } from "./StagingBind";
import { StillChat } from "./StillChat";
import { CameraChips } from "./CameraChips";

/**
 * One scene, full screen.
 *
 * A node is 240px wide because a canvas of eight of them has to be readable at once, which
 * makes it the wrong place to actually look at a picture: the still is 36px tall there, the
 * conversation about it is a 40px scroller, and the reference pictures are thumbnails the size
 * of a fingernail. Everything here is the same state and the same endpoints — this is a second
 * view of the beat, never a second copy of it — at a size where you can see what you are
 * judging.
 *
 * Rendered from `App`, not from the node. Inside a node it would sit under React Flow's
 * transformed viewport, where `position: fixed` is measured from the panned and zoomed layer
 * rather than from the window, so the overlay would pan away with the canvas.
 *
 * Deliberately not here: the join and the ▶ render selection. Both are decisions about the
 * chain, which is the thing the canvas exists to show — the join is drawn as the wire between
 * two nodes, and it stops meaning anything when you can only see one of them.
 */
export function BeatModal() {
  const studio = useStudio();
  const board = studio.board;
  const beat = board?.beats.find((candidate) => candidate.n === studio.expanded) ?? null;
  const setExpanded = studio.setExpanded;

  // Escape closes, from anywhere in the overlay -- including the textareas, which is where the
  // hand actually is. Bound on the window rather than on the panel so it works before anything
  // inside has been focused.
  useEffect(() => {
    if (beat === null) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(null);
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [beat, setExpanded]);

  if (!board || !beat) return null;
  return <Expanded board={board} beat={beat} />;
}

/**
 * What the modal can be looking at. `url` is null only for a still that does not exist yet, or
 * on the `new` slot, which is not an image at all.
 *
 * `new` is a pseudo-asset: the tile that composes a picture nobody has drawn yet. It rides in
 * the strip and the right column exactly as a real one does, so "pick a thing, work on it in the
 * panel" stays one mechanic rather than growing a second mode. It is deliberately NOT an empty
 * slot on the board -- `Board.ref_paths` is file-existence based, so a picture with a prompt and
 * no file is not something the server can represent, and inventing one would put a blank image
 * where a render could pick it up.
 */
type Asset = {
  id: string;
  kind: "still" | "cast" | "picture" | "video" | "frame" | "new";
  url: string | null;
  label: string;
  note: string;
  /** 1-based, the number the API addresses an upload by. Only on the director's own pictures. */
  index?: number;
};

function assetsOf(beat: Beat, allowNewPicture = true): Asset[] {
  const isBridge = beat.source === "bridge";
  const found: Asset[] = [];

  // The still first, and present even when it does not exist yet: this is where it is
  // generated from, so an empty slot is the affordance rather than a gap. Carrying the
  // previous clip used to hide this -- that clip was the opening. The still and the video
  // now sit together, so the slot stays.
  found.push({
      id: "still",
      kind: "still",
      url: beat.asset,
      label: isBridge ? "closing still" : "opening still",
      note: isBridge
        ? "the frame this scene has to arrive at, at the end of a take that carries on from " +
          `scene ${beat.n - 1}`
        : beat.source === "asset"
          ? "the exact first frame of this clip — handed to the model as a keyframe"
          : "the composition this scene opens on",
  });

  // In-betweens of a stop-motion sequence, after the opening still. Pose 1 IS that still.
  (beat.poses ?? []).slice(1).forEach((url, at) => {
    found.push({
      id: `pose:${at + 2}`,
      kind: "still",
      url,
      label: `pose ${at + 2}`,
      note: `stop-motion pose ${at + 2} of ${beat.poses.length} — same take, next increment of the action`,
    });
  });

  // The cast reference, read-only: it belongs to the reel, not to this scene, and it is here to
  // be compared against rather than edited. Skipped when it IS this beat's own still.
  const cast = beat.auto_refs.find((auto) => auto.kind === "cast" && auto.url)
    ?? beat.auto_refs.find((auto) => !auto.kind && auto.url && auto.url !== beat.asset);
  if (cast?.url) {
    found.push({ id: "cast", kind: "cast", url: cast.url, label: "cast reference", note: cast.note });
  }

  beat.refs.forEach((url, at) => {
    found.push({
      // Keyed by the picture's stable id, never by its position. `remove_ref` compacts, so
      // "picture-3" addresses a different file the moment anything before it is deleted -- and
      // with a draw prompt and a conversation hanging off the selection, that is the difference
      // between editing what you are looking at and editing its neighbour.
      id: `picture:${beat.ref_ids?.[at] ?? at + 1}`,
      kind: "picture",
      url,
      // The number the PROMPT uses, which is further along by however many slots wired
      // themselves. Addressing the file is `index`; they are not the same number.
      label: `picture ${beat.ref_offset + at + 1}`,
      note: beat.ref_prompts[at] ?? "",
      index: at + 1,
    });
  });

  if (beat.video) {
    found.push({
      id: "video",
      kind: "video",
      url: beat.video,
      label: "rendered clip",
      note: "the take this scene rendered as — the one thing on this board that cost money",
    });
  }
  if (beat.frame && beat.frame !== beat.asset) {
    found.push({
      id: "frame",
      kind: "frame",
      url: beat.frame,
      label: "opened on",
      note: "the frame this clip actually began on",
    });
  }
  if (beat.end_frame) {
    found.push({
      id: "end-frame",
      kind: "frame",
      url: beat.end_frame,
      label: "landed on",
      note: "the frame this clip was told to arrive at",
    });
  }
  if (beat.carry_clip) {
    found.push({
      id: "carry",
      kind: "video",
      url: beat.carry_clip,
      label: "carried tail",
      note:
        `the last seconds of scene ${beat.n - 1}, sent as <Video 1> so this one opens where ` +
        "that take had got to",
    });
  }
  // Last, because it is the affordance rather than a thing the scene owns -- and because
  // appending is what `uploadRefs` does, so a slot offered anywhere else would promise a
  // position the API cannot give.
  if (allowNewPicture && slotsLeft(beat) > 0) {
    found.push({
      id: "new",
      kind: "new",
      url: null,
      label: "draw one",
      note: "describe a prop, a costume, a set — Gemini draws it and it becomes a reference",
    });
  }
  return found;
}

function Expanded({ board, beat }: { board: Board; beat: Beat }) {
  const studio = useStudio();
  const [picked, setPicked] = useState("still");
  const [uploading, setUploading] = useState(false);
  const [dropping, setDropping] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const tray = useRef<AddPictureHandle>(null);

  const assets = assetsOf(beat, !board.manual_stills);
  const current = assets.find((asset) => asset.id === picked) ?? assets[0] ?? null;

  /**
   * Remove a picture and select its NEIGHBOUR, not the still.
   *
   * Bouncing back to the still on every × makes clearing three pictures three trips across the
   * tray. The picture before it is where the eye already is; the still is only the fallback when
   * nothing else survives.
   */
  const removePicture = (asset: Asset) => {
    if (asset.index === undefined) return;
    const at = assets.findIndex((candidate) => candidate.id === asset.id);
    const neighbour =
      assets.slice(0, at).reverse().find((candidate) => candidate.kind === "picture")
      ?? assets.slice(at + 1).find((candidate) => candidate.kind === "picture")
      ?? assets.find((candidate) => candidate.id !== asset.id);
    setPicked(neighbour?.id ?? "still");
    void studio.guard(() => api.removeRef(board.slug, beat.n, asset.index!));
  };
  const isBridge = beat.source === "bridge";
  const carrying = beat.source === "reference" && beat.carry;
  const canGenerate = stillsAllowed(board);
  const [geminiModel, setGeminiModel] = useState<GeminiImageModel>(
    beat.gemini_model ?? DEFAULT_GEMINI_IMAGE_MODEL,
  );
  const [geminiImageSize, setGeminiImageSize] = useState<GeminiImageSize>(
    beat.gemini_image_size ?? DEFAULT_GEMINI_IMAGE_SIZE,
  );
  const liteModel = geminiModel === "gemini-3.1-flash-lite-image";

  useEffect(() => {
    setGeminiModel(beat.gemini_model ?? DEFAULT_GEMINI_IMAGE_MODEL);
    setGeminiImageSize(beat.gemini_image_size ?? DEFAULT_GEMINI_IMAGE_SIZE);
  }, [beat.n, beat.gemini_model, beat.gemini_image_size]);

  const saveGeminiSettings = (model: GeminiImageModel, imageSize: GeminiImageSize) => {
    setGeminiModel(model);
    setGeminiImageSize(imageSize);
    void studio.guard(() =>
      api.patchBeat(board.slug, beat.n, {
        gemini_model: model,
        gemini_image_size: imageSize,
      }),
    );
  };

  const generating = useBusy(
    "asset",
    (detail) => Array.isArray(detail.beats) && (detail.beats as number[]).includes(beat.n),
  );
  // A picture being drawn into a slot that does not exist yet. The tile shows it from the JOB,
  // because the board has nothing to show until the file lands.
  const drawingNew = useBusy(
    "ref_draw",
    (detail) => detail.beat === beat.n && detail.index === null,
  );

  // The prompt the still is drawn from. Editable here and nowhere else on the canvas: it is
  // what the conversation rewrites, so being able to read it beside the picture is most of why
  // a rewrite that came back different from what you asked for is legible at all.
  const prompt = useDraft(beat.asset_prompt, (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { asset_prompt: next })),
  );

  // The shot grammar the storyboard panel is drawn from. A plain field rather than a `ReviseField`:
  // `revise` is a story edit and its prompt knows nothing about shot sizes, so pointing it at this
  // would rewrite camera language from a brief about staging. Editing this marks nothing stale —
  // no renderer ever sees it.
  const panelText = useDraft(beat.panel ?? "", (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { panel: next })),
  );
  const drawingPanel = useBusy(
    "panel_draw",
    (detail) => !Array.isArray(detail.beats) || detail.beats.includes(beat.n),
  );

  const upload = (chosen: FileList | File[] | null | undefined) => {
    const file = (chosen ? Array.from(chosen) : [])[0];
    if (!file) return;
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40 p-6 backdrop-blur-sm"
      onClick={() => studio.setExpanded(null)}
    >
      <div
        className="lift-lg flex h-full w-full max-w-6xl flex-col overflow-hidden rounded-2xl
          border border-edge bg-panel"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-3 border-b border-edge px-3 py-2">
          <span className="text-sm font-medium text-zinc-800">scene {beat.n}</span>
          <Badge state={beat.state} />
          <span className="text-[11px] text-zinc-500">
            {beat.source === "chain"
              ? `continues from scene ${beat.n - 1}`
              : isBridge
                ? `continues from scene ${beat.n - 1} · lands on its still`
                : carrying
                  ? `cut · continues scene ${beat.n - 1}`
                  : beat.hold_video
                    ? `cut · holds scene ${beat.n - 1} as identity`
                  : beat.source === "asset"
                    ? "cut · opens on its still exactly"
                    : "cut · opens on its still"}
          </span>
          <div className="ml-auto flex items-center gap-1">
            <CameraChips
              board={board}
              value={beat.camera}
              onChange={(camera) =>
                void studio.guard(() => api.patchBeat(board.slug, beat.n, { camera }))
              }
            />
            {beat.travel ? (
              <span
                className="px-1.5 text-[10px] uppercase tracking-wide text-zinc-500"
                title="lateral travel: the camera rig stays locked and the set layers slide opposite the walk"
              >
                set slides
              </span>
            ) : null}
            {board.lengths.map((option) => {
              const active = Math.round(beat.seconds) === Math.round(option);
              return (
                <button
                  key={option}
                  onClick={() =>
                    void studio.guard(() =>
                      api.patchBeat(board.slug, beat.n, { seconds: option }),
                    )
                  }
                  className={`rounded px-2 py-0.5 text-[11px] transition-colors ${
                    active
                      ? "bg-solid font-medium text-white"
                      : "bg-soft text-zinc-600 hover:bg-softer"
                  }`}
                  title={
                    active
                      ? `${beat.frames} frames, snapped onto the model's frame grid`
                      : `switch this scene to ${option}s`
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
          <button
            onClick={() => studio.setExpanded(null)}
            className="text-zinc-500 hover:text-zinc-800"
            title="close — Esc"
          >
            ×
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* The picture, at the size the decision about it is actually made at. */}
          <div className="flex min-w-0 flex-1 flex-col bg-ink">
            <div className="flex min-h-0 flex-1 items-center justify-center bg-ink p-2">
              {current?.kind === "video" && current.url ? (
                <video
                  src={current.url}
                  className="max-h-full max-w-full"
                  controls
                  loop
                  preload="metadata"
                />
              ) : current?.url ? (
                <img src={current.url} alt="" className="max-h-full max-w-full object-contain" />
              ) : (
                <div className="px-6 text-center text-[11px] leading-relaxed text-zinc-400">
                  {carrying
                    ? `no still yet — this scene continues scene ${beat.n - 1}, but it still opens on a still of its own`
                    : "no still yet — generate it, or upload your own, on the right"}
                </div>
              )}
            </div>

            {/* Every picture and clip this scene owns, in the order the prompt numbers them, and
                the only place a picture is added or removed. Also a drop target, because that is
                the gesture people try before they find the button. */}
            <div
              className={`thin flex shrink-0 items-start gap-1.5 overflow-x-auto border-t p-2 ${
                dropping ? "border-warm bg-warm/5" : "border-edge"
              }`}
              onDragOver={(event) => {
                event.preventDefault();
                setDropping(true);
              }}
              onDragLeave={() => setDropping(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDropping(false);
                tray.current?.offer(event.dataTransfer?.files);
              }}
            >
              {assets.map((asset) => (
                /* The × is a sibling of the tile, not a child: a button inside a button is
                   invalid HTML and React says so. Same wrapper the node's picture grid uses. */
                <div key={asset.id} className="group relative shrink-0">
                  <button
                    onClick={() => setPicked(asset.id)}
                    title={asset.note}
                    className={`rounded border p-1 text-left transition-colors ${
                      current?.id === asset.id
                        ? "border-warm bg-soft"
                        : "border-edge hover:border-softer"
                    }`}
                  >
                    <div
                      className={`flex h-14 w-14 items-center justify-center overflow-hidden ${
                        asset.kind === "new"
                          ? "rounded border border-dashed border-edge text-warm"
                          : "bg-ink"
                      }`}
                    >
                      {asset.url ? (
                        asset.kind === "video" ? (
                          <video src={asset.url} className="h-full w-full object-cover" muted />
                        ) : (
                          <img src={asset.url} alt="" className="h-full w-full object-cover" />
                        )
                      ) : asset.kind === "new" ? (
                        <span className="text-sm">{drawingNew ? "…" : "✦"}</span>
                      ) : (
                        <span className="text-[9px] text-zinc-400">empty</span>
                      )}
                    </div>
                    <div className="mt-1 w-14 truncate text-[9px] text-zinc-600">{asset.label}</div>
                  </button>
                  {asset.kind === "picture" && asset.index !== undefined ? (
                    /* Unconfirmed, matching the node's. The arm-then-confirm on a rendered clip
                       exists because that clip cost money; copying it here would teach the hand
                       to double-click destructive controls generally. */
                    <button
                      onClick={() => removePicture(asset)}
                      title={`remove ${asset.label} — the pictures after it are renumbered`}
                      className="absolute right-1 top-1 hidden rounded bg-black/80 px-1 text-[10px]
                        text-zinc-700 hover:text-red-600 group-hover:block"
                    >
                      ×
                    </button>
                  ) : null}
                </div>
              ))}
              {/* Last, because appending is what the API does -- `uploadRefs` never reorders, so
                  a + at the front would promise a position it cannot give. */}
              <div className="pt-1">
                <AddPicture
                  ref={tray}
                  beat={beat}
                  variant="tile"
                  onAdded={(id) => id && setPicked(`picture:${id}`)}
                />
              </div>
            </div>
          </div>

          {/* What can be done to the thing on the left, then the words that get rendered. */}
          <div className="thin flex w-[26rem] shrink-0 flex-col gap-3 overflow-y-auto border-l
            border-edge p-3">
            <div className="space-y-2 rounded border border-edge bg-ink p-2">
              <div className="text-[10px] uppercase tracking-wide text-zinc-500">
                Gemini image settings for this beat
              </div>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-[10px] text-zinc-600">
                  <span className="mb-1 block">model</span>
                  <select
                    className={`${inputClass} h-8 py-1 text-[11px]`}
                    value={geminiModel}
                    onChange={(event) => {
                      const model = event.target.value as GeminiImageModel;
                      saveGeminiSettings(
                        model,
                        model === "gemini-3.1-flash-lite-image" ? "1K" : geminiImageSize,
                      );
                    }}
                  >
                    {GEMINI_IMAGE_MODELS.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-[10px] text-zinc-600">
                  <span className="mb-1 block">image size</span>
                  <select
                    className={`${inputClass} h-8 py-1 text-[11px]`}
                    value={liteModel ? "1K" : geminiImageSize}
                    disabled={liteModel}
                    onChange={(event) =>
                      saveGeminiSettings(geminiModel, event.target.value as GeminiImageSize)
                    }
                  >
                    {GEMINI_IMAGE_SIZES.map((size) => (
                      <option key={size} value={size} disabled={liteModel && size !== "1K"}>
                        {size}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="text-[10px] leading-snug text-zinc-400">
                {GEMINI_IMAGE_MODELS.find((option) => option.id === geminiModel)?.blurb}
                {liteModel
                  ? " Output is fixed at 1K."
                  : " Used for this beat's stills and drawn pictures."}
              </p>
            </div>

            {current ? (
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] uppercase tracking-wide text-zinc-500">
                    {current.label}
                  </span>
                  {current.kind === "still" ? (
                    <>
                      <Button
                        tone="ghost"
                        className="ml-auto"
                        disabled={uploading}
                        onClick={() => picker.current?.click()}
                        title={
                          isBridge
                            ? "use your own image as the frame this scene lands on"
                            : "use your own image as the composition this scene opens on"
                        }
                      >
                        {uploading ? "uploading…" : beat.asset ? "⤒ replace" : "⤒ upload"}
                      </Button>
                      {board.manual_stills ? null : (
                        <Button
                          tone="ghost"
                          disabled={generating || !canGenerate}
                          onClick={() =>
                            void studio.guard(() =>
                              api.assets(board.slug, [beat.n], {
                                model: geminiModel,
                                imageSize: geminiImageSize,
                              }),
                            )
                          }
                          title={
                            !canGenerate
                              ? "write every storyboard panel, and lock the cast, before a still is drawn"
                              : board.reference
                              ? "generate this still — the cast is matched to the reel's " +
                                "reference, so only the setting changes"
                              : "generate this still — it is the first, so it becomes the " +
                                "reel's cast reference"
                          }
                        >
                          {generating ? "generating…" : beat.asset ? "✦ regenerate" : "✦ generate"}
                        </Button>
                      )}
                    </>
                  ) : null}
                  {current.kind === "picture" && current.index !== undefined ? (
                    <Button
                      tone="ghost"
                      className="ml-auto"
                      onClick={() => {
                        setPicked("still");
                        void studio.guard(() =>
                          api.removeRef(board.slug, beat.n, current.index!),
                        );
                      }}
                      title="remove this picture — the ones after it are renumbered"
                    >
                      × remove
                    </Button>
                  ) : null}
                  {current.kind === "video" && current.id === "video" ? (
                    <a
                      href={current.url ?? undefined}
                      download
                      className="ml-auto text-[10px] text-live hover:text-green-700"
                    >
                      ↓ clip
                    </a>
                  ) : null}
                </div>

                <p className="text-[10px] leading-snug text-zinc-500">{current.note}</p>

                <input
                  ref={picker}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={(event) => {
                    upload(event.target.files);
                    event.target.value = "";
                  }}
                />

                {/* Everything a picture can be: drawn, described, argued with. Its own file
                    because this one was already 467 lines before a picture had a prompt. */}
                {current.kind === "picture" && current.index !== undefined ? (
                  <PicturePanel
                    beat={beat}
                    index={current.index}
                    note={current.note}
                    label={beat.ref_offset + current.index}
                    geminiModel={geminiModel}
                    geminiImageSize={geminiImageSize}
                  />
                ) : null}

                {current.kind === "new" ? (
                  <NewPicture
                    beat={beat}
                    geminiModel={geminiModel}
                    geminiImageSize={geminiImageSize}
                  />
                ) : null}

                {current.kind === "still" ? (
                  <>
                    <span className="text-[10px] uppercase tracking-wide text-zinc-500">
                      drawn from
                    </span>
                    {/* `stillPictures`, not `videoPictures`: this text reaches Gemini, whose
                        picture list is identity-first and capped — so the same picture is a
                        different number here than it is in the action field below. */}
                    <PromptField
                      className={`${inputClass} thin h-24 leading-relaxed`}
                      value={prompt.draft}
                      onChange={prompt.change}
                      onBlur={prompt.flush}
                      options={stillPictures(beat, board.staging ?? [])}
                      onPick={(option) =>
                        setPicked(option.id === "cast" ? "cast" : `picture:${option.id}`)
                      }
                      placeholder="what this still shows — the setting, the framing, the moment"
                      title="the prompt the image server draws this still from. The conversation
                        below rewrites it; editing it here does the same thing by hand. Type @ to
                        name one of this scene's pictures"
                    />
                    {!beat.asset ? (
                      <p className="text-[10px] leading-snug text-zinc-400">
                        "Generate or upload the still and you can talk about it here."
                      </p>
                    ) : (
                      <StillChat beat={beat} expanded />
                    )}
                  </>
                ) : null}
              </div>
            ) : null}

            {/* Above the two lines that get rendered, because it answers the question they
                assume: which of the film's designed things is in this shot. */}
            <div className="border-t border-edge pt-3">
              <StagingBind board={board} beat={beat} />
            </div>

            <div className="space-y-3 border-t border-edge pt-3">
              <ReviseField
                beat={beat}
                field="scene"
                label="scene"
                rows="h-14"
                placeholder="where this shot happens"
                title="the setting, in the prompt with the action: where this beat is and at what
                  scale. Beats in one continuous shot should carry the same line"
                hint="“say it from further back”, “same set as the scene before”"
              />
              <ReviseField
                beat={beat}
                field="action"
                label="action"
                rows="h-28"
                placeholder="what MOVES in this shot — the camera never moves"
                title="only what moves. Appearance belongs in the style bible, and the camera
                  never moves"
                hint="“slower, one movement only”, “make it read as continuing from the last shot”"
              />
            </div>

            {/* The storyboard panel: the sketch, and the shot grammar it is drawn from. Below the
                two lines that get rendered because it is about none of them — a panel is a drawing
                of this shot for a human to look at, and nothing downstream reads it. Which is also
                why there is no review, no conversation and no staleness attached to it. */}
            <div className="space-y-2 border-t border-edge pt-3">
              <div className="flex items-center gap-2">
                <span className="text-[10px] uppercase tracking-wide text-zinc-500">panel</span>
                <span className="text-[10px] text-zinc-400">
                  shot size, angle, camera — a rough sketch, on the cheapest model. Nothing is
                  rendered from it
                </span>
                <Button
                  onClick={() => void studio.guard(() => api.drawPanel(board.slug, beat.n))}
                  disabled={drawingPanel || !panelText.draft.trim()}
                  title={
                    panelText.draft.trim()
                      ? "draw this panel"
                      : "say what the panel shows first, or write the storyboard from the sidebar"
                  }
                  className="ml-auto"
                >
                  {drawingPanel ? "drawing…" : beat.panel_url ? "✦ redraw panels" : "✦ draw panels"}
                </Button>
              </div>
              <div className="flex gap-3">
                <textarea
                  value={panelText.draft}
                  onChange={(event) => panelText.change(event.target.value)}
                  onBlur={panelText.flush}
                  placeholder="medium shot, low angle, the fox at frame left, arrow right"
                  className={`${inputClass} h-24 flex-1`}
                />
                {panelUrls(beat).length ? (
                  <div className="flex h-24 gap-1">
                    {panelUrls(beat).map((url) => (
                      <img
                        key={url}
                        src={url}
                        alt=""
                        className="h-24 rounded border border-edge bg-ink object-contain"
                      />
                    ))}
                  </div>
                ) : null}
              </div>
              {(beat.panel_frames ?? []).filter((line) => line.trim()).map((line) => (
                <p key={line} className="text-[10px] leading-snug text-zinc-400" title={line}>
                  {line}
                </p>
              ))}
            </div>

            {beat.render && beat.state === "rendered" ? (
              <p className="text-[10px] text-zinc-500">
                rendered in {clock(beat.render.render_seconds)} · cost {money(beat.render.cost)}
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
