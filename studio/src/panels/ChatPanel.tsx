import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStudio } from "../useStudio";
import { ActivityTimeline } from "./ActivityTimeline";
import { Turn } from "./Turn";

const NUDGES = [
  "make it slower and calmer",
  "add one more beat at the end",
  "this action is too busy — one movement only",
  "write the script and style it",
];

/**
 * The conversation that drives the board. The director edits directly or delegates to
 * specialists; activity events show tools and subagents as they run.
 */
export function ChatPanel({ onCollapse }: { onCollapse: () => void }) {
  const studio = useStudio();
  const board = studio.board!;
  const [message, setMessage] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  const thinking = Boolean(studio.agentJob);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [studio.chat.length, thinking, studio.agentActivity.length]);

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || thinking) return;
    setMessage("");
    void studio.guard(() => api.chat(board.slug, trimmed, studio.selection));
  };

  const missing = board.assets_needed.length;

  return (
    <div className="flex w-96 shrink-0 flex-col border-l border-edge bg-panel">
      <div className="flex items-center gap-2 px-4 py-3.5">
        <span className="min-w-0 flex-1 truncate text-[15px] font-medium text-zinc-900">
          {board.title || "untitled reel"}
        </span>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] ${
            studio.model.ready ? "bg-soft text-zinc-500" : "bg-stale/10 text-stale"
          }`}
          title={
            studio.model.ready
              ? "the director, specialists, and still review — through the Google API"
              : "no Google API key, or it was refused. Put X-GOOG-API-KEY=… in .env."
          }
        >
          {studio.model.ready ? studio.model.model || "gemini" : "model offline"}
        </span>
        <button
          onClick={onCollapse}
          title="hide this panel and give the canvas the width"
          className="rounded-lg px-1.5 py-1 text-[13px] text-zinc-400 transition-colors
            hover:bg-hover hover:text-zinc-700"
        >
          ⇥
        </button>
      </div>

      <div ref={scroller} className="thin flex-1 space-y-4 overflow-y-auto px-4 pb-2">
        {studio.chat.map((turn, index) => (
          <Turn key={index} turn={turn} />
        ))}
        {thinking ? (
          <div className="space-y-2">
            <ActivityTimeline events={studio.agentActivity} live defaultOpen />
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <span className="h-1.5 w-1.5 rounded-full bg-warm live-dot" />
              {studio.agentJob?.phase || "director is working…"}
            </div>
          </div>
        ) : null}
      </div>

      {studio.chat.length <= 2 ? (
        <div className="space-y-0.5 px-3 pb-1">
          {NUDGES.map((nudge) => (
            <button
              key={nudge}
              onClick={() => send(nudge)}
              className="block w-full truncate rounded-xl px-2.5 py-1.5 text-left text-[11px]
                text-zinc-500 transition-colors hover:bg-hover hover:text-zinc-800"
            >
              {nudge}
            </button>
          ))}
        </div>
      ) : null}

      {missing && !board.manual_stills ? (
        <div className="mx-3 mb-2 flex items-center gap-2 rounded-2xl border border-edge bg-ink px-3 py-2">
          <span className="min-w-0 flex-1 text-[11px] text-zinc-600">
            {missing} scene{missing === 1 ? "" : "s"} without the still{" "}
            {missing === 1 ? "it opens on" : "they open on"}
          </span>
          <button
            onClick={() => studio.goStage("assets")}
            title="the stage where a still is generated, judged and talked about"
            className="shrink-0 rounded-full bg-solid px-3 py-1.5 text-[11px] font-medium
              text-white transition-colors hover:bg-zinc-800"
          >
            → Assets
          </button>
        </div>
      ) : null}

      <div className="p-3 pt-0">
        <div className="rounded-2xl border border-edge bg-panel p-2 transition-colors focus-within:border-zinc-300">
          <textarea
            className="thin h-16 w-full resize-none bg-transparent px-1.5 py-1 text-xs
              leading-relaxed text-zinc-800 outline-none placeholder:text-zinc-400"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send(message);
              }
            }}
            placeholder="ask the director for a change…"
          />
          <div className="flex items-center gap-1.5 px-0.5 pt-1">
            {studio.selection.length ? (
              <button
                onClick={() => studio.setSelection([])}
                title="talk about the whole board instead"
                className="flex items-center gap-1 rounded-full bg-warm/10 px-2 py-1 text-[10px]
                  text-warm transition-colors hover:bg-warm/20"
              >
                beat {studio.selection.join(", ")} ×
              </button>
            ) : (
              <span className="px-1 text-[10px] text-zinc-400">
                select a scene to talk about just that one
              </span>
            )}
            <button
              onClick={() => send(message)}
              disabled={thinking || !message.trim()}
              title="send — Enter also sends, Shift+Enter is a newline"
              className="ml-auto flex h-8 w-8 items-center justify-center rounded-full bg-solid
                text-[13px] text-white transition-colors hover:bg-zinc-800
                disabled:cursor-not-allowed disabled:opacity-30"
            >
              ↑
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
