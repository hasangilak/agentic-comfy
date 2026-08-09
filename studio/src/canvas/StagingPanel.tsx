import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import {
  DEFAULT_GEMINI_IMAGE_MODEL,
  DEFAULT_GEMINI_IMAGE_SIZE,
  type Board,
  type StageEntry,
  type StageKind,
} from "../types";
import { useBusy, useDraft, useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";
import { AssetChat } from "./AssetChat";

/**
 * The reel's design bible: the cast, the sets and the props, each drawn once and then bound to
 * the scenes that contain them.
 *
 * The layer that was missing. A style bible is one paragraph, so the same sentence produced a
 * round-eared pig in scene 1 and a sharper-eared one in scene 4 and neither prompt was wrong. A
 * cast reference is one image, and it is not a design sheet at all — it is scene 1's own still,
 * a composed shot whose framing and light every later still was then anchored to. And a scene's
 * reference pictures are per-scene, so a second character had nowhere to live and the same
 * clearing was redrawn from the same words in every shot that used it.
 *
 * A design here is named, written down, drawn as a sheet, and bound. Whatever the scenes that
 * bind it are conditioned on, they are conditioned on the same picture and told the same
 * sentence.
 *
 * Rendered from `App` rather than from the node that opens it, for the reason `BeatModal` is: a
 * `position: fixed` overlay inside a React Flow node is measured against the transformed
 * viewport and pans away with the canvas.
 */
export function StagingPanel() {
  const studio = useStudio();
  const board = studio.board;
  const setOpen = studio.setStagingOpen;

  useEffect(() => {
    if (!studio.stagingOpen) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [studio.stagingOpen, setOpen]);

  if (!board || !studio.stagingOpen) return null;
  return <Bible board={board} />;
}

const KIND_LOOK: Record<StageKind, { label: string; icon: string; hint: string }> = {
  character: {
    label: "character",
    icon: "🦊",
    hint: "someone who acts. Drawn as a design sheet — the puppet whole and centred on a plain "
      + "ground — and sent to both the clip and the still it opens on",
  },
  environment: {
    label: "set",
    icon: "🌲",
    hint: "a place, drawn empty of characters and in the reel's own vertical frame. It reaches "
      + "the clip as a picture and the still as words: four slots do not hold a cast and a set, "
      + "and what a still must not get wrong is the cast",
  },
  prop: {
    label: "prop",
    icon: "🔦",
    hint: "a thing that has to look the same wherever it turns up. Drawn like a character sheet "
      + "and sent to both renders",
  },
};

function Bible({ board }: { board: Board }) {
  const studio = useStudio();
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState<StageKind>("character");
  const [name, setName] = useState("");

  const picked =
    board.staging.find((entry) => entry.id === studio.stagingPick) ?? board.staging[0] ?? null;
  const full = board.staging.length >= board.max_staging;

  const add = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    setName("");
    setAdding(false);
    void studio.guard(async () => {
      const created = await api.addStage(board.slug, { kind, name: trimmed });
      // Selected straight away, because the next thing anyone does is say what it looks like.
      studio.setStagingPick(created.id);
      return created;
    });
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-900/40 p-6 backdrop-blur-sm"
      onClick={() => studio.setStagingOpen(false)}
    >
      <div
        className="lift-lg flex h-full w-full max-w-5xl flex-col overflow-hidden rounded-2xl
          border border-edge bg-panel"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-3 border-b border-edge px-3 py-2">
          <span className="text-sm">🎭</span>
          <span className="text-sm font-medium text-zinc-800">staging</span>
          <span className="text-[11px] text-zinc-500">
            the cast, the sets and the props this film is made of — drawn once, then bound to the
            scenes they appear in
          </span>
          <span className="ml-auto text-[11px] text-zinc-400">
            {board.staging.length} / {board.max_staging}
          </span>
          <button
            onClick={() => studio.setStagingOpen(false)}
            className="text-zinc-500 hover:text-zinc-800"
            title="close — Esc"
          >
            ×
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* The bible itself: every design, and the one control that adds another. */}
          <div className="thin w-64 shrink-0 space-y-1 overflow-y-auto border-r border-edge p-2">
            {board.staging.map((entry) => (
              <button
                key={entry.id}
                onClick={() => studio.setStagingPick(entry.id)}
                className={`flex w-full items-center gap-2 rounded-xl p-1.5 text-left
                  transition-colors ${entry.id === picked?.id ? "bg-soft" : "hover:bg-hover"}`}
              >
                {entry.sheet ? (
                  <img src={entry.sheet} alt="" className="h-10 w-10 rounded-lg object-cover" />
                ) : (
                  <span
                    className="flex h-10 w-10 items-center justify-center rounded-lg border
                      border-dashed border-edge text-[13px]"
                    title="no sheet yet — this design reaches every render as words alone"
                  >
                    {KIND_LOOK[entry.kind].icon}
                  </span>
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] text-zinc-800">{entry.name}</span>
                  <span className="block text-[10px] text-zinc-400">
                    {KIND_LOOK[entry.kind].label}
                    {/* Which scenes contain it. The binding lives on the beat, so this is the
                        only place the whole answer is visible at once. */}
                    {" · "}
                    {sceneList(board, entry.id)}
                  </span>
                </span>
              </button>
            ))}

            {!board.staging.length ? (
              <p className="px-2 py-3 text-[11px] leading-relaxed text-zinc-400">
                Nothing designed yet. Add the characters and the sets that turn up in more than
                one scene — those are the ones that drift.
              </p>
            ) : null}

            {adding ? (
              <div className="space-y-1.5 rounded-xl border border-edge bg-ink p-2">
                <div className="flex gap-1 rounded-full bg-panel p-0.5 text-[10px]">
                  {board.stage_kinds.map((option) => (
                    <button
                      key={option}
                      onClick={() => setKind(option)}
                      title={KIND_LOOK[option].hint}
                      className={`flex-1 rounded-full px-1.5 py-1 transition-colors ${
                        kind === option ? "bg-solid text-white" : "text-zinc-500 hover:text-zinc-800"
                      }`}
                    >
                      {KIND_LOOK[option].label}
                    </button>
                  ))}
                </div>
                <input
                  className={`${inputClass} bg-panel`}
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  onKeyDown={(event) => event.key === "Enter" && add()}
                  placeholder={kind === "environment" ? "the clearing" : "Vera"}
                  autoFocus
                />
                <div className="flex gap-1.5">
                  <Button tone="primary" onClick={add} disabled={!name.trim()}>
                    add
                  </Button>
                  <Button tone="ghost" onClick={() => setAdding(false)}>
                    cancel
                  </Button>
                </div>
                <p className="text-[10px] leading-snug text-zinc-400">
                  The name is what the prompts call it, so make it the one the action lines use.
                </p>
              </div>
            ) : (
              <button
                onClick={() => setAdding(true)}
                disabled={full}
                title={
                  full
                    ? `${board.max_staging} designs is the ceiling — remove one first`
                    : "free: this makes an entry, it does not draw anything"
                }
                className="w-full rounded-xl bg-soft py-1.5 text-[11px] text-zinc-700
                  hover:bg-softer disabled:cursor-not-allowed disabled:opacity-40"
              >
                ＋ design something
              </button>
            )}
          </div>

          {picked ? (
            <Design board={board} entry={picked} />
          ) : (
            <div className="flex flex-1 items-center justify-center p-6 text-center">
              <p className="max-w-sm text-[11px] leading-relaxed text-zinc-400">
                A design bound to a scene reaches its clip as a numbered picture and its still as
                the same image — so the wolf in scene 2 and the wolf in scene 6 are one wolf
                rather than two readings of the same sentence.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Which scenes bind this design, as "scenes 2, 5" — or nothing, said plainly. */
function sceneList(board: Board, id: string): string {
  const scenes = board.beats.filter((beat) => beat.staging?.includes(id)).map((beat) => beat.n);
  if (!scenes.length) return "in no scene yet";
  return `${scenes.length === 1 ? "scene" : "scenes"} ${scenes.join(", ")}`;
}

/**
 * One design, at a size where you can see it: what it is, what it is drawn from, and the
 * conversation about it.
 *
 * The two text fields are deliberately not one, and the split is the same one a reference
 * picture has. `note` is what every prompt in the film is TOLD this design is — it becomes
 * "<Picture 3> is Vera, the fox mother in warm orange" — and it reaches both renderers, so
 * editing it marks every scene that binds it stale. `draw` is what Gemini is asked for, and it
 * reaches neither: it produces a sheet, and the sheet's own hash is already in the fingerprint.
 * "A fox side-on against flat black" is a good draw prompt and a terrible end to the sentence
 * "<Picture 3> is …".
 */
function Design({ board, entry }: { board: Board; entry: StageEntry }) {
  const studio = useStudio();
  const picker = useRef<HTMLInputElement>(null);
  const [confirming, setConfirming] = useState(false);

  const drawing = useBusy("stage_draw", (detail) => detail.id === entry.id);
  const talking = useBusy("stage_chat", (detail) => detail.id === entry.id);
  const busy = drawing || talking;

  const name = useDraft(entry.name, (next) =>
    void studio.guard(() => api.describeStage(board.slug, entry.id, { name: next })),
  );
  const note = useDraft(entry.note, (next) =>
    void studio.guard(() => api.describeStage(board.slug, entry.id, { note: next })),
  );
  const draw = useDraft(entry.draw, (next) =>
    void studio.guard(() => api.describeStage(board.slug, entry.id, { draw: next })),
  );

  // Reset when the selection moves, or the panel keeps offering to delete the design you were
  // looking at a moment ago.
  useEffect(() => setConfirming(false), [entry.id]);

  const redraw = () => {
    const text = draw.draft.trim();
    if (!text || busy) return;
    draw.flush();
    void studio.guard(() =>
      api.drawStage(board.slug, entry.id, {
        prompt: text,
        model: DEFAULT_GEMINI_IMAGE_MODEL,
        imageSize: DEFAULT_GEMINI_IMAGE_SIZE,
      }),
    );
  };

  const remove = () => {
    // The neighbour, so deleting three designs is not three trips back across the list.
    const at = board.staging.findIndex((candidate) => candidate.id === entry.id);
    const next = board.staging[at - 1] ?? board.staging[at + 1] ?? null;
    studio.setStagingPick(next?.id ?? null);
    void studio.guard(() => api.removeStage(board.slug, entry.id));
  };

  return (
    <div className="flex min-h-0 flex-1">
      <div className="flex min-w-0 flex-1 items-center justify-center bg-ink p-4">
        {entry.sheet ? (
          <img
            src={entry.sheet}
            alt={entry.name}
            className="max-h-full max-w-full rounded-xl object-contain"
          />
        ) : (
          <div className="max-w-xs text-center">
            <p className="mb-2 text-3xl">{KIND_LOOK[entry.kind].icon}</p>
            <p className="text-[11px] leading-relaxed text-zinc-400">
              No sheet yet. Until there is one, this design still reaches every scene that binds
              it — as the sentence on the right, which is already better than nothing.
            </p>
          </div>
        )}
      </div>

      <div className="thin w-80 shrink-0 space-y-2.5 overflow-y-auto border-l border-edge p-3">
        <div className="flex items-center gap-2">
          <input
            className={`${inputClass} py-1`}
            value={name.draft}
            onChange={(event) => name.change(event.target.value)}
            onBlur={name.flush}
            placeholder="what the prompts call it"
          />
          {/* The kind decides three things at once — the suffix, the shape, and whether the
              still gets an image or a sentence — so it is a control rather than a label. */}
          <select
            value={entry.kind}
            onChange={(event) =>
              void studio.guard(() =>
                api.describeStage(board.slug, entry.id, {
                  kind: event.target.value as StageKind,
                }),
              )
            }
            title={KIND_LOOK[entry.kind].hint}
            className="shrink-0 rounded-xl border border-edge bg-ink px-1.5 py-1.5 text-[11px]
              text-zinc-700 outline-none"
          >
            {board.stage_kinds.map((option) => (
              <option key={option} value={option}>
                {KIND_LOOK[option].label}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">
            what every scene is told it is
          </span>
          <textarea
            className={`${inputClass} thin h-20 leading-relaxed`}
            value={note.draft}
            onChange={(event) => note.change(event.target.value)}
            onBlur={note.flush}
            placeholder="the fox mother, warm orange cardstock with a cream chest"
            title="this reaches both renderers — it becomes the sentence that says which picture
              is which. Editing it marks every scene that binds this design stale."
          />
          <p className="text-[10px] leading-snug text-zinc-400">
            Reaches the render as “{entry.role}”.
          </p>
        </div>

        <div className="space-y-1">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">drawn from</span>
          <textarea
            className={`${inputClass} thin h-24 leading-relaxed`}
            value={draw.draft}
            onChange={(event) => draw.change(event.target.value)}
            onBlur={draw.flush}
            placeholder={
              entry.kind === "environment"
                ? "a moonlit clearing ringed with birches, seen from ground level"
                : "a fox mother side-on, ears forward, on a plain ground"
            }
            title="the prompt Gemini draws the sheet from. It reaches neither renderer — the sheet
              it produces is what they see — so editing it does not mark anything stale."
          />
          <div className="flex flex-wrap items-center gap-1.5">
            <Button tone="primary" onClick={redraw} disabled={busy || !draw.draft.trim()}>
              {drawing ? "drawing…" : entry.sheet ? "✦ draw it again" : "✦ draw it"}
            </Button>
            <Button tone="ghost" onClick={() => picker.current?.click()} disabled={busy}>
              ⤒ upload
            </Button>
            <input
              ref={picker}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) {
                  void studio.guard(() => api.uploadStageSheet(board.slug, entry.id, file));
                }
              }}
            />
          </div>
          <p className="text-[10px] leading-snug text-zinc-400">
            {entry.kind === "environment"
              ? "A set, not a shot: the place with nobody in it, in the reel's own vertical frame."
              : "A design sheet, not a shot: the subject whole and centred on a plain ground."}{" "}
            {entry.sheet
              ? "A redraw holds what is there and changes only what you ask for."
              : "Nothing conditions the first draw — a model shown the cast draws the cast. Name "
                + "another design with @ if this one should share its materials."}
          </p>
          {studio.stillsBackend === "papercut" ? null : (
            <p className="text-[10px] leading-snug text-stale">
              the image server is down — start it with <code>make images</code>
            </p>
          )}
        </div>

        <AssetChat
          turns={entry.chat ?? []}
          busy={talking}
          onSend={(message) =>
            void studio.guard(() => api.stageChat(board.slug, entry.id, message))
          }
          placeholder="her chest should be cream, not white"
          empty={
            <>Say what should be different about the sheet and it is rewritten and drawn again.</>
          }
          offline={
            studio.stillsBackend === "papercut"
              ? null
              : "the image server is down, so a redraw will not happen — the prompt is still rewritten"
          }
        />

        <div className="border-t border-edge pt-2">
          {confirming ? (
            <div className="space-y-1.5">
              <p className="text-[10px] leading-snug text-stale">
                {sceneList(board, entry.id) === "in no scene yet"
                  ? "Nothing binds it, so nothing else changes."
                  : `${sceneList(board, entry.id)} bind it — they stop being conditioned on it, ` +
                    "and any @ that names it becomes what it was for."}
              </p>
              <div className="flex gap-1.5">
                <Button tone="danger" onClick={remove}>
                  delete it
                </Button>
                <Button tone="ghost" onClick={() => setConfirming(false)}>
                  keep it
                </Button>
              </div>
            </div>
          ) : (
            <Button tone="ghost" onClick={() => setConfirming(true)} disabled={busy}>
              × remove this design
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
