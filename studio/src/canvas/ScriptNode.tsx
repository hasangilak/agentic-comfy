import { Handle, Position } from "@xyflow/react";
import { useState } from "react";
import { api } from "../api";
import { useDraft, useStudio } from "../useStudio";
import { Field, inputClass } from "../ui";

/**
 * The head of the board. Holds the title and the style bible -- the paragraph of
 * appearance that every asset prompt reuses so the character does not drift between shots.
 */
export function ScriptNode() {
  const studio = useStudio();
  const board = studio.board!;
  const [open, setOpen] = useState(false);

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
          className="w-full rounded bg-[#26262e] py-1 text-[11px] text-zinc-300 hover:bg-[#32323c]"
          title="appends an empty beat, continuing from the last one"
        >
          ＋ add a beat
        </button>
      </div>

      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}
