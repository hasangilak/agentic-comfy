import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { ChatTurn } from "../types";
import { useStudio } from "../useStudio";

const NUDGES = [
  "make it slower and calmer",
  "add one more beat at the end",
  "this action is too busy — one movement only",
  "generate the stills this reel is missing",
];

/**
 * The conversation that drives the board. A turn is a tool loop, so it can edit a beat, read
 * the board back to see what that did, and ask the image server for the stills the board now
 * needs -- all in one turn. The edits are listed as they land, so the canvas never changes
 * without saying why.
 *
 * Note what is missing: the model cannot render. It writes, rewrites, re-times and reorders,
 * and it can ask for stills, all of which are free. Pressing render stays a human act -- which
 * is why the render button is on the canvas toolbar and nothing in this panel can reach it.
 */
export function ChatPanel({ onCollapse }: { onCollapse: () => void }) {
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
              ? "runs on this machine through Ollama — nothing metered, nothing leaves"
              : "Ollama is not answering, or the model is not pulled. `make qwen` pulls it."
          }
        >
          {studio.model.ready ? studio.model.model || "local model" : "model offline"}
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
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            <span className="h-1.5 w-1.5 rounded-full bg-warm live-dot" />
            thinking…
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

      {/* The reference design's "needs approval to run" strip, doing the job it does there:
          one thing the board is waiting on, and the button that spends it. Generating a still
          costs nothing but time, so this is a nudge rather than a gate. */}
      {missing && !board.manual_stills ? (
        <div className="mx-3 mb-2 flex items-center gap-2 rounded-2xl border border-edge bg-ink px-3 py-2">
          <span className="min-w-0 flex-1 text-[11px] text-zinc-600">
            {missing} scene{missing === 1 ? "" : "s"} without the still{" "}
            {missing === 1 ? "it opens on" : "they open on"}
          </span>
          <button
            onClick={() => void studio.guard(() => api.assets(board.slug))}
            disabled={studio.stillsBackend !== "papercut"}
            title={
              studio.stillsBackend === "papercut"
                ? "rendered by Gemini, then checked against the reel's cast reference — free " +
                  "and unlimited, ~10–18 s each"
                : "the image server is not running — start it with `make images`, or upload the " +
                  "stills yourself"
            }
            className="shrink-0 rounded-full bg-solid px-3 py-1.5 text-[11px] font-medium
              text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed
              disabled:opacity-40"
          >
            generate {missing}
          </button>
        </div>
      ) : null}

      {/* The composer is one card, the way the reference draws it: the field, and underneath it
          on the same sheet everything that decides what the message means. */}
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
            placeholder="ask for a change to the board…"
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

/**
 * One turn. The user's is a bubble on the right; the model's is plain text, because it is the
 * one being read rather than the one being addressed. What it *did* is a fold: a turn that
 * rewrote six beats used to push its own sentence off the top of the panel.
 */
function Turn({ turn }: { turn: ChatTurn }) {
  const [open, setOpen] = useState(false);
  const ops = turn.ops ?? [];

  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-soft px-3 py-2 text-xs
          leading-relaxed text-zinc-800">
          {turn.selection?.length ? (
            <span className="mr-1.5 text-[10px] text-warm">beat {turn.selection.join(", ")}</span>
          ) : null}
          {turn.text}
        </div>
      </div>
    );
  }

  return (
    <div>
      {ops.length ? (
        <button
          onClick={() => setOpen((value) => !value)}
          className="mb-1.5 flex w-full items-center gap-2 rounded-xl px-1.5 py-1 text-left
            transition-colors hover:bg-hover"
        >
          <span className="text-[12px] text-live">✦</span>
          <span className="text-[12px] text-zinc-600">
            Edits <span className="text-zinc-400">{ops.length}</span>
          </span>
          <span className="ml-auto text-[11px] text-zinc-300">{open ? "▾" : "›"}</span>
        </button>
      ) : null}
      {open ? (
        <div className="mb-1.5 space-y-1 border-l border-edge pl-3">
          {ops.map((op, index) => (
            <div key={index} className="text-[11px] leading-relaxed text-zinc-500">
              {op.summary}
            </div>
          ))}
        </div>
      ) : null}
      <div className="text-xs leading-relaxed text-zinc-700">{turn.text}</div>
    </div>
  );
}
