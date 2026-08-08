import { useEffect, useRef, useState } from "react";
import type { AssetTurn } from "../types";
import { Button, inputClass } from "../ui";

/**
 * A conversation about one image — the transcript, the composer, and nothing that knows which
 * image it is.
 *
 * There are two of these now: the still's, and one per reference picture. They are the same
 * panel with three words changed, and a copy of it would be a hundred lines that drift — the
 * `✦ rendered again` line, the `prompt rewritten` disclosure, the Enter-to-send handling. So the
 * shape lives here and the two callers are thin wrappers that supply their own turns, endpoint
 * and copy: `StillChat` and `PictureChat`.
 *
 * `attach` is null for a conversation that takes no pictures. In the STILL's conversation an
 * attachment means "here is what I mean" and is stored on the beat, because the still renderer
 * reads the beat — which is also why it can move the join, hence `warning`. In a PICTURE's
 * conversation the picture is the subject, so there is nothing to attach and no join to move.
 */
export interface AttachControl {
  /** How many more pictures the beat can take, staged files included. */
  slotsLeft: number;
  /** What sending them would do to the join, or null when it would do nothing. */
  warning: string | null;
  /** The cap message, so the wrapper's own vocabulary reaches a disabled button. */
  fullTitle: string;
  title: string;
}

export function AssetChat({
  turns,
  busy,
  onSend,
  placeholder,
  empty,
  expanded = false,
  attach = null,
  offline = null,
}: {
  turns: AssetTurn[];
  busy: boolean;
  onSend: (message: string, files: File[]) => void;
  placeholder: string;
  /** What this conversation is for, shown before anything has been said. */
  empty: React.ReactNode;
  expanded?: boolean;
  attach?: AttachControl | null;
  /** Shown when the image server is down, so a redraw will not happen. */
  offline?: string | null;
}) {
  const [message, setMessage] = useState("");
  // Held here until the note is sent, so a picture and the sentence about it arrive as one turn.
  // Attaching first and then typing would render twice: once from the upload landing on the
  // beat, once from the note.
  const [files, setFiles] = useState<File[]>([]);
  const picker = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const slotsLeft = Math.max(0, (attach?.slotsLeft ?? 0) - files.length);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [turns.length, busy]);

  const send = () => {
    const trimmed = message.trim();
    if ((!trimmed && !files.length) || busy) return;
    const sending = files;
    setMessage("");
    setFiles([]);
    onSend(trimmed, sending);
  };

  return (
    <div className={`space-y-1.5 p-1.5 ${expanded ? "" : "border-t border-[#26262e]"}`}>
      {turns.length ? (
        <div
          ref={scroller}
          // Capped rather than flex-filled: the modal's right-hand column is itself a scroller,
          // and a `flex-1` child inside one collapses to nothing when the column has no spare
          // height to hand out.
          className={`thin nodrag nowheel space-y-1.5 overflow-y-auto ${
            expanded ? "max-h-[26rem]" : "max-h-40"
          }`}
        >
          {turns.map((turn, index) => (
            <div key={index}>
              <div
                className={
                  turn.role === "user"
                    ? "ml-4 rounded rounded-br-sm bg-[#26262e] px-1.5 py-1 text-[10px] " +
                      "leading-snug text-zinc-200"
                    : "text-[10px] leading-snug text-zinc-400"
                }
              >
                {turn.text}
              </div>
              {turn.regenerated ? (
                <div className="text-[10px] text-[#4ade80]/80">✦ rendered again</div>
              ) : null}
              {turn.error ? (
                <div className="text-[10px] leading-snug text-[#f59e0b]">{turn.error}</div>
              ) : null}
              {/* The prompt the picture is drawn from, on the turn that changed it. Shown in
                  full rather than truncated: it is the one thing on this panel you may need to
                  copy, and reading half of it says nothing. */}
              {turn.prompt ? (
                <details className="mt-0.5">
                  <summary className="cursor-pointer text-[10px] text-zinc-600
                    hover:text-zinc-400">
                    prompt rewritten
                  </summary>
                  <p className="mt-0.5 leading-snug text-[10px] text-zinc-500">{turn.prompt}</p>
                </details>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <p className="text-[10px] leading-snug text-zinc-600">{empty}</p>
      )}

      {attach ? (
        <input
          ref={picker}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          className="hidden"
          onChange={(event) => {
            const wanted = Array.from(event.target.files ?? []).slice(0, slotsLeft);
            if (wanted.length) setFiles((current) => [...current, ...wanted]);
            // Cleared so re-picking the same file still fires a change event.
            event.target.value = "";
          }}
        />
      ) : null}

      {files.length ? (
        <div className="flex flex-wrap gap-1">
          {files.map((file, index) => (
            <span
              key={`${file.name}-${index}`}
              className="flex items-center gap-1 rounded bg-[#26262e] px-1 py-0.5 text-[10px]
                text-zinc-300"
              title={`${file.name} — sent with this note and kept on the beat as a reference picture`}
            >
              <span className="max-w-24 truncate">{file.name}</span>
              <button
                onClick={() => setFiles((current) => current.filter((_, at) => at !== index))}
                className="text-zinc-500 hover:text-red-400"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}

      <textarea
        className={`${inputClass} thin nodrag ${expanded ? "h-16" : "h-12"}`}
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            send();
          }
        }}
        placeholder={placeholder}
      />
      <div className="flex flex-wrap items-center gap-1.5">
        <Button tone="primary" onClick={send} disabled={busy || (!message.trim() && !files.length)}>
          {busy ? "…" : "send"}
        </Button>
        {attach ? (
          <Button
            tone="ghost"
            disabled={busy || slotsLeft === 0}
            onClick={() => picker.current?.click()}
            title={slotsLeft ? attach.title : attach.fullTitle}
          >
            ⤒ picture
          </Button>
        ) : null}
        {files.length && attach?.warning ? (
          <span className="text-[10px] leading-snug text-[#f59e0b]">
            sending this {attach.warning}
          </span>
        ) : null}
        {offline ? (
          <span className="text-[10px] leading-snug text-[#f59e0b]">{offline}</span>
        ) : null}
      </div>
    </div>
  );
}
