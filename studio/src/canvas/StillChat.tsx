import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Beat } from "../types";
import { useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";

/**
 * The conversation about one still.
 *
 * The board's own chat panel edits the story — beats, joins, lengths. This edits a picture, and
 * it is a different conversation on purpose: the model is shown this still and the reel's cast
 * reference, and what it writes back is this beat's `asset_prompt`, followed by a re-render.
 *
 * The automatic review posts here too, so a node reads as the whole history of how its picture
 * got to be what it is: what was asked for, what the reviewer objected to, which turns ended in
 * a redraw. That is the thing which is baffling anywhere else — a still that came back different
 * from the prompt you can see — and obvious here.
 *
 * Collapsed by default. A node is 240px wide and most of them are finished; the count on the
 * toggle is what makes an unread verdict findable without opening eight panels.
 */
export function StillChat({ beat }: { beat: Beat }) {
  const studio = useStudio();
  const board = studio.board!;
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const turns = beat.asset_chat ?? [];

  const busy = Object.values(studio.jobs).some(
    (job) =>
      job.kind === "still_chat" &&
      job.slug === board.slug &&
      (job.state === "queued" || job.state === "running") &&
      job.detail.beat === beat.n,
  );

  useEffect(() => {
    if (open) scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [open, turns.length, busy]);

  const send = () => {
    const trimmed = message.trim();
    if (!trimmed || busy) return;
    setMessage("");
    void studio.guard(() => api.stillChat(board.slug, beat.n, trimmed));
  };

  return (
    <div className="rounded border border-[#26262e]">
      <button
        onClick={() => setOpen((current) => !current)}
        className="nodrag flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left
          text-[10px] text-zinc-400 hover:bg-[#26262e]"
        title={
          "say what is wrong with this still and have it redrawn — the model looks at the " +
          "picture, rewrites this beat's prompt and renders it again. Free, ~10–18 s"
        }
      >
        <span className="text-[#d99a4e]">✎</span>
        {busy ? "looking at this still…" : "talk about this still"}
        {turns.length ? <span className="ml-auto text-zinc-600">{turns.length}</span> : null}
      </button>

      {open ? (
        <div className="space-y-1.5 border-t border-[#26262e] p-1.5">
          {turns.length ? (
            <div ref={scroller} className="thin nodrag nowheel max-h-40 space-y-1.5 overflow-y-auto">
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
                  {/* The prompt the picture is drawn from, on the turn that changed it. Shown
                      in full rather than truncated: it is the one thing on this panel you may
                      need to copy, and reading half of it says nothing. */}
                  {turn.prompt ? (
                    <details className="mt-0.5">
                      <summary className="cursor-pointer text-[10px] text-zinc-600
                        hover:text-zinc-400">
                        prompt rewritten
                      </summary>
                      <p className="mt-0.5 leading-snug text-[10px] text-zinc-500">
                        {turn.prompt}
                      </p>
                    </details>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[10px] leading-snug text-zinc-600">
              “her ears are too pointed”, “move the lamp to the left”, “same thing again, a
              different draw”. The picture is redrawn from the corrected prompt; the video is
              not touched.
            </p>
          )}

          <textarea
            className={`${inputClass} thin nodrag h-12`}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
            placeholder="what should be different about this still?"
          />
          <div className="flex items-center gap-1.5">
            <Button tone="primary" onClick={send} disabled={busy || !message.trim()}>
              {busy ? "…" : "send"}
            </Button>
            {studio.stillsBackend === "papercut" ? null : (
              <span className="text-[10px] leading-snug text-[#f59e0b]">
                the image server is down — the prompt will be rewritten, but nothing redrawn
              </span>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
