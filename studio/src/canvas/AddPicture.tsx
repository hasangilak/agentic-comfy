import { useImperativeHandle, useRef, useState, type Ref } from "react";
import { api } from "../api";
import { joinWarning, slotsLeft } from "../beat";
import type { Beat } from "../types";
import { useStudio } from "../useStudio";
import { Button } from "../ui";

/** What a container can ask of this control — used by the modal's tray, which is a drop target. */
export interface AddPictureHandle {
  /** Take these files through the same warning-and-confirm path the button does. */
  offer: (files: FileList | File[] | null | undefined) => void;
}

/**
 * Adding a reference picture to a beat, wherever that is offered.
 *
 * Three places offer it — the node's button, the modal's `+` tile, and dropping files on the
 * modal's tray — and all three have the same consequence: storing a picture puts the beat on the
 * reference join (`api.store_refs` moves it before the first file is written). So the warning
 * lives here rather than being written out three times, which is how two of them end up saying
 * different things about the same act.
 *
 * The warning is an inline confirm strip, not a dialog: there is no dialog primitive in this
 * studio and one should not be added for this. Same shape and the same amber as the node's
 * arm-then-confirm on a rendered clip.
 *
 * The sequencing is the load-bearing part. You cannot ask a question *after* `<input type=file>`
 * fires and *before* the upload unless you are holding the files — so a beat that is already on
 * the reference join uploads immediately and is never nagged, and every other beat has its files
 * staged, unsent, until the director says go.
 */
export function AddPicture({
  beat,
  variant = "button",
  onAdded,
  ref,
}: {
  beat: Beat;
  variant?: "button" | "tile";
  /** Called with the stable id of the first picture stored, so a view can select it. */
  onAdded?: (id: string | null) => void;
  ref?: Ref<AddPictureHandle>;
}) {
  const studio = useStudio();
  const board = studio.board;
  const picker = useRef<HTMLInputElement>(null);
  const [pending, setPending] = useState<File[] | null>(null);
  const [busy, setBusy] = useState(false);

  const left = board ? slotsLeft(beat) : 0;
  const warning = joinWarning(beat);
  const full = left === 0;

  const send = (chosen: File[]) => {
    if (!board) return;
    const wanted = chosen.slice(0, left);
    if (!wanted.length) return;
    const at = beat.refs?.length ?? 0;
    setBusy(true);
    setPending(null);
    void studio
      .guard(() =>
        api.uploadRefs(board.slug, beat.n, wanted).then((next) => {
          // Read off the board that just came back, not the stale `beat` prop: the id was minted
          // by the store that this response IS, so the prop cannot know it yet.
          const fresh = next.beats.find((candidate) => candidate.n === beat.n);
          onAdded?.(fresh?.ref_ids?.[at] ?? null);
          return next;
        }),
      )
      .finally(() => setBusy(false));
  };

  /** Nothing is sent from here when the join would move — the strip below asks first. */
  const offer = (chosen: FileList | File[] | null | undefined) => {
    const files = (chosen ? Array.from(chosen) : []).slice(0, left);
    if (!files.length) return;
    if (!warning) return send(files);
    setPending(files);
  };

  // Declared before the early return so the hook order is stable, which is also why `left` above
  // tolerates a missing board rather than bailing first.
  useImperativeHandle(ref, () => ({ offer }));
  if (!board) return null;

  const title = full
    ? `${board.max_refs} pictures is the model's limit — remove one first`
    : warning
      ? `add reference pictures — ${warning}`
      : "add reference pictures for the model to hold this scene to";

  return (
    <div className={variant === "tile" ? "shrink-0" : ""}>
      <input
        ref={picker}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        hidden
        onChange={(event) => {
          offer(event.target.files);
          // Cleared so choosing the same file twice in a row still fires a change event.
          event.target.value = "";
        }}
      />

      {variant === "tile" ? (
        <button
          onClick={() => picker.current?.click()}
          disabled={busy || full}
          title={title}
          className="flex h-14 w-14 flex-col items-center justify-center rounded border
            border-dashed border-[#3a3a44] text-zinc-500 hover:border-[#d99a4e]
            hover:text-[#d99a4e] disabled:cursor-not-allowed disabled:opacity-30"
        >
          <span className="text-sm leading-none">＋</span>
          <span className="mt-0.5 text-[9px] leading-none">{busy ? "…" : `${left} left`}</span>
        </button>
      ) : (
        <Button
          tone="ghost"
          onClick={() => picker.current?.click()}
          disabled={busy || full}
          title={title}
        >
          {busy ? "uploading…" : "⤒ add picture"}
        </Button>
      )}

      {pending ? <Confirm beat={beat} files={pending} onSend={send} onCancel={() => setPending(null)} /> : null}
    </div>
  );
}

/**
 * What is about to happen to the join, and the chance not to do it.
 *
 * The staged files carry their own × because a director who picked four and wants three should
 * not have to cancel and start the picker again.
 */
function Confirm({
  beat,
  files,
  onSend,
  onCancel,
}: {
  beat: Beat;
  files: File[];
  onSend: (files: File[]) => void;
  onCancel: () => void;
}) {
  const [staged, setStaged] = useState(files);
  const warning = joinWarning(beat);
  return (
    <div className="mt-2 space-y-2 rounded border border-[#d99a4e]/40 bg-[#d99a4e]/5 p-2">
      <p className="text-[10px] leading-snug text-[#d99a4e]">⚠ {warning}</p>
      <div className="flex flex-wrap gap-1">
        {staged.map((file, at) => (
          <span
            key={`${file.name}-${at}`}
            className="flex items-center gap-1 rounded bg-[#26262e] px-1.5 py-0.5 text-[10px]
              text-zinc-400"
          >
            <span className="max-w-24 truncate">{file.name}</span>
            <button
              onClick={() => setStaged((current) => current.filter((_, i) => i !== at))}
              className="text-zinc-600 hover:text-red-400"
              title="do not send this one"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <Button tone="primary" onClick={() => onSend(staged)} disabled={!staged.length}>
          add anyway
        </Button>
        <Button tone="quiet" onClick={onCancel}>
          cancel
        </Button>
      </div>
    </div>
  );
}
