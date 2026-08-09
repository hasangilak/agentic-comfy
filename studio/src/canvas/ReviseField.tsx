import { useState } from "react";
import { api } from "../api";
import { videoPictures } from "../beat";
import type { Beat, Job } from "../types";
import { useDraft, useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";
import { PromptField } from "./Mentions";

/**
 * One of the two lines that get rendered, with the model on hand to rewrite it.
 *
 * Typing is still the fast path and the textarea is the same one the node has. What this adds
 * is dictation: "shorter", "make it read as continuing from the last shot", "she should reach
 * for the lamp instead". The board's own chat can do exactly this — it is one `set_beat` call —
 * but only after working out from the sentence which scene and which line were meant, which is
 * the part that goes wrong on a board where every beat says something similar. Here both are
 * decided by which box you typed in.
 *
 * The reply is shown from the job rather than from the transcript. Both hold it — `agent.revise`
 * writes the turn into the board's own conversation, deliberately, so the next chat turn knows
 * the line moved — but the transcript is behind this modal, and a rewrite you cannot see the
 * reasoning for reads as the studio changing your words on its own.
 */
export function ReviseField({
  beat,
  field,
  label,
  placeholder,
  title,
  hint,
  rows = "h-20",
}: {
  beat: Beat;
  field: "scene" | "action";
  label: string;
  placeholder: string;
  title: string;
  hint: string;
  rows?: string;
}) {
  const studio = useStudio();
  const board = studio.board!;
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");

  const draft = useDraft(beat[field], (next) =>
    void studio.guard(() => api.patchBeat(board.slug, beat.n, { [field]: next })),
  );

  const mine = (job: Job) =>
    job.kind === "revise" &&
    job.slug === board.slug &&
    job.detail.beat === beat.n &&
    job.detail.field === field;
  const busy = Object.values(studio.jobs).some(
    (job) => mine(job) && (job.state === "queued" || job.state === "running"),
  );
  const answered = Object.values(studio.jobs)
    .filter((job) => mine(job) && job.state === "done")
    .sort((a, b) => (a.finished_at ?? 0) - (b.finished_at ?? 0))
    .pop();
  const reply = (answered?.result as { reply?: string } | null)?.reply ?? "";

  const send = () => {
    const trimmed = note.trim();
    if (!trimmed || busy) return;
    setNote("");
    // Flushed first: the model is about to be shown this beat as the board holds it, and a
    // pending debounce would have it rewriting the version from before you started typing.
    draft.flush();
    void studio.guard(() => api.reviseBeat(board.slug, beat.n, field, trimmed));
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</span>
        <button
          onClick={() => setOpen((current) => !current)}
          className="ml-auto text-[10px] text-zinc-500 hover:text-warm"
          title={
            `say what should be different about the ${field} and the model rewrites it. Free, ` +
            "a few seconds, and it marks the scene for re-rendering exactly like typing would"
          }
        >
          <span className="text-warm">✎</span> {busy ? "rewriting…" : "revise"}
        </button>
      </div>

      {/* `videoPictures`: this line reaches the video model, which is given the pictures in
          `<Picture N>` order — its own still first, then the cast, then the uploads. That is a
          different numbering from the still's, and the legend under the field shows which. */}
      <PromptField
        className={`${inputClass} thin ${rows} leading-relaxed`}
        value={draft.draft}
        onChange={draft.change}
        onBlur={draft.flush}
        options={videoPictures(beat, board.staging ?? [])}
        placeholder={placeholder}
        title={title}
      />

      {open ? (
        <div className="space-y-1 rounded border border-edge p-1.5">
          <textarea
            className={`${inputClass} thin h-12`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
            placeholder={hint}
          />
          <div className="flex items-center gap-1.5">
            <Button tone="primary" onClick={send} disabled={busy || !note.trim()}>
              {busy ? "…" : "rewrite"}
            </Button>
            {studio.model.ready ? null : (
              <span className="text-[10px] leading-snug text-stale">
                {studio.model.model || "the model"} is not running — nothing to rewrite with
              </span>
            )}
          </div>
          {reply ? <p className="text-[10px] leading-snug text-zinc-500">{reply}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
