import { Handle, Position } from "@xyflow/react";
import { useRef, useState } from "react";
import { api } from "../api";
import { useDraft, useStudio } from "../useStudio";
import { Field, inputClass } from "../ui";

/**
 * The head of the board. Holds the title, the style bible -- the paragraph of appearance
 * that every asset prompt reuses -- and the character reference, which is the same promise
 * made as a picture. Text alone never held the cast still across a cut: each scene's still
 * was a fresh reading of the same paragraph. The reference is what every new still is
 * generated from, so a cut changes the setting rather than the characters.
 */
export function ScriptNode() {
  const studio = useStudio();
  const board = studio.board!;
  const [open, setOpen] = useState(false);
  const pickReference = useRef<HTMLInputElement>(null);
  const structureBusy = Object.values(studio.jobs).some(
    (job) =>
      job.slug === board.slug && (job.state === "queued" || job.state === "running"),
  );

  const title = useDraft(board.title, (next) =>
    void studio.guard(() => api.patchBoard(board.slug, { title: next })),
  );
  const bible = useDraft(board.style_bible, (next) =>
    void studio.guard(() => api.patchBoard(board.slug, { style_bible: next })),
  );

  const totalFrames = board.beats.reduce((sum, beat) => sum + beat.frames, 0);
  const seconds = board.beats.reduce((sum, beat) => sum + beat.actual_seconds, 0);

  return (
    <div className="w-72 rounded-lg border border-[#26262e] bg-[#16161b] shadow-lg">
      <div className="flex items-center gap-2 border-b border-[#26262e] px-3 py-2">
        <span className="text-sm">📄</span>
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">script</span>
        <span className="ml-auto text-[10px] text-zinc-500">
          {board.beats.length} beats · {seconds.toFixed(1)}s
        </span>
      </div>

      <div className="space-y-2 p-3">
        <input
          className={inputClass}
          value={title.draft}
          onChange={(event) => title.change(event.target.value)}
          onBlur={title.flush}
          placeholder="reel title"
        />

        <button
          onClick={() => setOpen((value) => !value)}
          className="flex w-full items-center justify-between text-[10px] uppercase
            tracking-wide text-zinc-500 hover:text-zinc-300"
        >
          <span>style bible</span>
          <span>{open ? "hide" : "show"}</span>
        </button>
        {open ? (
          <Field label="reused in every asset prompt">
            <textarea
              className={`${inputClass} thin h-40 leading-relaxed`}
              value={bible.draft}
              onChange={(event) => bible.change(event.target.value)}
              onBlur={bible.flush}
              placeholder="the medium, the exact character, the palette — look only, never motion"
            />
          </Field>
        ) : (
          <p className="line-clamp-3 text-[11px] leading-relaxed text-zinc-500">
            {board.style_bible || "no style bible yet"}
          </p>
        )}

        <div className="flex items-start gap-2 rounded border border-[#26262e] bg-[#0d0d10] p-2">
          {board.reference ? (
            <img
              src={board.reference}
              alt="character reference"
              className="h-14 w-8 shrink-0 rounded object-cover"
            />
          ) : (
            <div
              className="flex h-14 w-8 shrink-0 items-center justify-center rounded
                border border-dashed border-[#33333d] text-[9px] text-zinc-600"
            >
              none
            </div>
          )}
          <div className="min-w-0 flex-1">
            <p className="text-[10px] uppercase tracking-wide text-zinc-500">cast reference</p>
            <p className="mt-0.5 text-[10px] leading-snug text-zinc-500">
              {!board.reference
                ? "the first still generated will set the look"
                : board.reference_explicit
                  ? "every new still is matched to this image"
                  : "using scene 1’s still — every new still is matched to it"}
            </p>
            <div className="mt-1 flex gap-2 text-[10px]">
              <button
                onClick={() => pickReference.current?.click()}
                className="text-zinc-400 hover:text-[#d99a4e]"
                title="pin the cast with your own image, for every still generated from here on"
              >
                {board.reference_explicit ? "replace" : "use my own"}
              </button>
              {board.reference_explicit ? (
                <button
                  onClick={() => void studio.guard(() => api.clearReference(board.slug))}
                  className="text-zinc-500 hover:text-red-300"
                  title="go back to using scene 1’s still"
                >
                  clear
                </button>
              ) : null}
            </div>
          </div>
          {/* Costs nothing and spends no quota: it only changes what future stills are
              generated from. Existing stills are left alone. */}
          <input
            ref={pickReference}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) void studio.guard(() => api.uploadReference(board.slug, file));
            }}
          />
        </div>

        <div className="flex items-center justify-between pt-1 text-[10px] text-zinc-600">
          <span>
            {totalFrames} frames @ {board.steps} steps
          </span>
          <span>seed {board.seed}</span>
        </div>

        {/* A new beat starts with no action, which leaves it `planned` -- so it is excluded
            from the render button and costs nothing until somebody writes the movement. */}
        <button
          onClick={() => void studio.guard(() => api.addBeat(board.slug, {}))}
          disabled={structureBusy}
          className="w-full rounded bg-[#26262e] py-1 text-[11px] text-zinc-300
            hover:bg-[#32323c] disabled:cursor-not-allowed disabled:opacity-40"
          title={
            structureBusy
              ? "wait for the current job to finish"
              : "appends an empty scene to the end of the linear sequence"
          }
        >
          ＋ add scene at end
        </button>
      </div>

      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
