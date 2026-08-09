import { useState } from "react";
import { api } from "../api";
import { joinWarning, stillPictures } from "../beat";
import type { Beat, GeminiImageModel, GeminiImageSize } from "../types";
import { useBusy, useDraft, useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";
import { AssetChat } from "./AssetChat";
import { PromptField } from "./Mentions";
import { ReferenceNote } from "./SequenceNode";

/**
 * One reference picture, at a size where you can see it: what it is for, what it was drawn
 * from, and the conversation about it.
 *
 * Lifted out of `BeatModal` because that file was already 467 lines before a picture could be
 * drawn or discussed, not because this is a second view of anything — every control here is the
 * same endpoint the tray and the node call.
 *
 * The draw prompt is revealed by ✦ redraw rather than always rendered. Most reference pictures
 * are uploads that will never be redrawn, and a second always-on textarea in a 26rem column,
 * inert in the common case, is a lot of chrome for the uncommon one. The field exists and is
 * stored either way; this is only about when it is on screen.
 */
/**
 * A picture that does not exist yet: describe it, and Gemini draws it.
 *
 * Nothing is written to the board until the file lands. `Board.ref_paths` is file-existence
 * based, so a slot holding a prompt and no image is not a state the server can represent -- and
 * a placeholder image would sit in `pictures_for` where a render could pay for it. The cost is
 * that a failed draw loses what you typed; it is in the job's log, and the alternative is worse.
 */
export function NewPicture({
  beat,
  geminiModel,
  geminiImageSize,
}: {
  beat: Beat;
  geminiModel: GeminiImageModel;
  geminiImageSize: GeminiImageSize;
}) {
  const studio = useStudio();
  const board = studio.board!;
  const [prompt, setPrompt] = useState("");
  const drawing = useBusy("ref_draw", (detail) => detail.beat === beat.n && detail.index === null);
  const warning = joinWarning(beat);

  const send = () => {
    const trimmed = prompt.trim();
    if (!trimmed || drawing) return;
    setPrompt("");
    void studio.guard(() =>
      api.createRef(board.slug, beat.n, trimmed, {
        model: geminiModel,
        imageSize: geminiImageSize,
      }),
    );
  };

  return (
    <>
      <span className="text-[10px] uppercase tracking-wide text-zinc-500">draw a picture</span>
      <textarea
        className={`${inputClass} thin h-24 leading-relaxed`}
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        placeholder="a close-up of an iron-grey club, layered cardstock, side on"
          title="a design sheet, not a shot: the subject whole and centred on a plain ground. It is
            drawn with the beat's available cast, opening-still, and reference images as context"
      />
      <div className="flex flex-wrap items-center gap-1.5">
        <Button tone="primary" onClick={send} disabled={drawing || !prompt.trim()}>
          {drawing ? "drawing…" : "✦ draw it"}
        </Button>
        <span className="text-[10px] text-zinc-400">Gemini image generation · beat references included</span>
      </div>
      {warning ? (
        <p className="text-[10px] leading-snug text-stale">⚠ drawing one means {warning}</p>
      ) : null}
      {studio.stillsBackend === "papercut" ? null : (
        <p className="text-[10px] leading-snug text-stale">
          the image server is down — start it with <code>make images</code>
        </p>
      )}
      <p className="text-[10px] leading-snug text-zinc-400">
        Or upload one with the ＋ tile. Either way it becomes a reference the video model is shown
        and the still is drawn from, and either way you can talk to it afterwards.
      </p>
    </>
  );
}

export function PicturePanel({
  beat,
  index,
  note,
  label,
  geminiModel,
  geminiImageSize,
}: {
  beat: Beat;
  /** 1-based, the number the API addresses this picture by — not the number the prompt uses. */
  index: number;
  note: string;
  /** What the prompt calls it: `ref_offset + index`. */
  label: number;
  geminiModel: GeminiImageModel;
  geminiImageSize: GeminiImageSize;
}) {
  const studio = useStudio();
  const board = studio.board!;
  const stored = beat.ref_draws?.[index - 1] ?? "";
  const [open, setOpen] = useState(false);

  // Busy on THIS picture, not on the beat: a beat can have nine, and a spinner on all of them
  // because one is being drawn says the wrong thing.
  const drawing = useBusy(
    "ref_draw",
    (detail) => detail.beat === beat.n && detail.index === index,
  );
  const talking = useBusy(
    "ref_chat",
    (detail) => detail.beat === beat.n && detail.index === index,
  );

  const draw = useDraft(stored, (next) =>
    void studio.guard(() => api.describeRef(board.slug, beat.n, index, { draw: next })),
  );

  const redraw = () => {
    // Flushed first, so ✦ never renders the prompt that was on screen a debounce ago. Same
    // ordering `ReviseField.send` uses, and for the same reason.
    draw.flush();
    void studio.guard(() =>
      api.drawRef(board.slug, beat.n, index, {
        prompt: draw.draft.trim(),
        model: geminiModel,
        imageSize: geminiImageSize,
      }),
    );
  };

  return (
    <>
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">picture {label}</span>
        <Button
          tone="ghost"
          className="ml-auto"
          disabled={drawing}
          onClick={() => setOpen((current) => !current)}
          title={
            stored
              ? "draw this picture again with the current picture first and the beat's other " +
                "references available. It is edited rather than restarted: the image server is " +
                "told to hold what is there and change only what you ask for"
              : "this picture was uploaded, so there is no prompt to draw it from yet. Say what " +
                "it should be and it becomes something you can iterate on"
          }
        >
          {drawing ? "drawing…" : stored ? "✦ redraw" : "✦ draw"}
        </Button>
      </div>

      {open ? (
        <div className="space-y-1.5 rounded border border-edge p-2">
          <span className="text-[10px] uppercase tracking-wide text-zinc-500">drawn from</span>
          {/* `stillPictures`, because this text goes to Gemini — the same prose vocabulary the
              still's prompt uses, not the video model's `<Picture N>` tags. */}
          <PromptField
            className={`${inputClass} thin h-24 leading-relaxed`}
            value={draw.draft}
            onChange={draw.change}
            onBlur={draw.flush}
            options={stillPictures(beat, board.staging ?? [])}
            placeholder="what this picture shows — the prop, the costume, the set, on a plain ground"
            title="the prompt Gemini draws this picture from. Not what the picture is FOR — that is
              the note below, and it is what the video model is told. Type @ to name another of
              this scene's pictures"
          />
          <div className="flex flex-wrap items-center gap-1.5">
            <Button tone="primary" onClick={redraw} disabled={drawing || !draw.draft.trim()}>
              {drawing ? "drawing…" : "draw it"}
            </Button>
            <p className="text-[10px] leading-snug text-zinc-400">
              A design sheet, not a shot: the subject whole and centred on a plain ground, nothing
              cropped. The current picture is sent first for edits, followed by the beat's other
              available references for context.
            </p>
          </div>
          {studio.stillsBackend === "papercut" ? null : (
            <p className="text-[10px] leading-snug text-stale">
              the image server is down — start it with <code>make images</code>
            </p>
          )}
        </div>
      ) : null}

      {/* What the picture is FOR, in the model's own words. The same field and the same endpoint
          the node's list edits — imported rather than re-typed for exactly that reason. */}
      <ReferenceNote slug={board.slug} n={beat.n} index={index} label={label} value={note} />
      <p className="text-[10px] leading-snug text-zinc-400">
        Say what this picture is FOR — “the same single Moth that performs the action”, “the set
        only, no puppet”. Shown a picture with no explanation the model assumes the picture IS the
        scene.
        {index <= beat.still_refs
          ? " This one is drawn into the still as well as the clip."
          : " This one steers the clip only; the still takes fewer pictures."}
      </p>

      {/* No attachments here, deliberately: the picture IS the subject, and a file sent with a
          note would become a tenth reference nobody asked for. Adding one is the tray's job. */}
      <AssetChat
        turns={beat.ref_chats?.[index - 1] ?? []}
        busy={talking}
        expanded
        onSend={(message) =>
          void studio.guard(() => api.refChat(board.slug, beat.n, index, message))
        }
        placeholder="what should be different about this picture?"
        empty={
          <>
            “make the club longer and more battered”, “drop the background to plain grey”, “same
            thing, a different draw”. The prompt is rewritten and the picture drawn again from
            it with the beat's other references available — the clip is not touched until you
            render it.
          </>
        }
        offline={
          studio.stillsBackend === "papercut"
            ? null
            : "the image server is down — the prompt will be rewritten, but nothing redrawn"
        }
      />
    </>
  );
}
