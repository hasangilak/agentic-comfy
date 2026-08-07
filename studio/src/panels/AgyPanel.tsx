import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";

const NUDGES = [
  "make it slower and calmer",
  "add one more beat at the end",
  "tighten the whole reel to 30 seconds",
  "this action is too busy — one movement only",
];

/**
 * The conversation that drives the board. Every turn returns a reply plus a list of edits,
 * and the edits are shown as they land so the canvas never changes without saying why.
 *
 * Note what is missing: agy cannot render. It writes, rewrites, re-times and reorders for
 * free, and spends image quota only when asked. Pressing render stays a human act.
 */
export function AgyPanel() {
  const studio = useStudio();
  const board = studio.board!;
  const [message, setMessage] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  const thinking = Object.values(studio.jobs).some(
    (job) => job.kind === "chat" && job.state === "running",
  );

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [studio.chat.length, thinking]);

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || thinking) return;
    setMessage("");
    void studio.guard(() => api.chat(board.slug, trimmed, studio.selection));
  };

  return (
    <div className="flex w-80 shrink-0 flex-col border-l border-[#26262e] bg-[#16161b]">
      <div className="flex items-center gap-2 border-b border-[#26262e] px-3 py-2">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">agy</span>
        <span className="ml-auto text-[10px] text-zinc-600">free — plan quota</span>
      </div>

      <div ref={scroller} className="thin flex-1 space-y-3 overflow-y-auto p-3">
        {studio.chat.map((turn, index) => (
          <div key={index}>
            <div
              className={
                turn.role === "user"
                  ? "ml-6 rounded-lg rounded-br-sm bg-[#26262e] px-2.5 py-1.5 text-xs text-zinc-200"
                  : "text-xs leading-relaxed text-zinc-300"
              }
            >
              {turn.role === "user" && turn.selection?.length ? (
                <span className="mr-1.5 text-[10px] text-[#d99a4e]">
                  beat {turn.selection.join(", ")}
                </span>
              ) : null}
              {turn.text}
            </div>
            {turn.ops?.length ? (
              <div className="mt-1 space-y-0.5">
                {turn.ops.map((op, opIndex) => (
                  <div key={opIndex} className="text-[10px] text-[#4ade80]/80">
                    ✦ {op.summary}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        ))}
        {thinking ? <div className="text-xs text-zinc-500">thinking…</div> : null}
      </div>

      {studio.chat.length <= 2 ? (
        <div className="space-y-1 border-t border-[#26262e] px-3 py-2">
          {NUDGES.map((nudge) => (
            <button
              key={nudge}
              onClick={() => send(nudge)}
              className="block w-full truncate rounded px-1.5 py-1 text-left text-[11px]
                text-zinc-500 hover:bg-[#26262e] hover:text-zinc-300"
            >
              {nudge}
            </button>
          ))}
        </div>
      ) : null}

      <div className="border-t border-[#26262e] p-3">
        {studio.selection.length ? (
          <div className="mb-1.5 flex items-center gap-1.5 text-[10px] text-[#d99a4e]">
            <span>context: beat {studio.selection.join(", ")} — "this one" means these</span>
            <button
              onClick={() => studio.setSelection([])}
              className="text-[#d99a4e]/60 hover:text-[#d99a4e]"
              title="talk about the whole board instead"
            >
              ×
            </button>
          </div>
        ) : (
          <div className="mb-1.5 text-[10px] text-zinc-600">
            select a node to talk about just that beat
          </div>
        )}
        <textarea
          className={`${inputClass} thin h-16`}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send(message);
            }
          }}
          placeholder="ask agy to change the board…"
        />
        <div className="mt-1.5 flex items-center gap-2">
          <Button tone="primary" onClick={() => send(message)} disabled={thinking || !message.trim()}>
            send
          </Button>
          {board.assets_needed.length && !board.manual_stills ? (
            <Button
              tone="quiet"
              onClick={() => void studio.guard(() => api.assets(board.slug))}
              title={
                studio.stillsBackend === "papercut"
                  ? "rendered by mflux on this machine — free and unlimited, ~10–18 s each"
                  : "roughly five images per five-hour window — the scarcest thing here"
              }
            >
              generate {board.assets_needed.length} still
              {board.assets_needed.length === 1 ? "" : "s"}
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
