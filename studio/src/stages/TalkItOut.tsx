import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Turn } from "../panels/Turn";
import { useStudio } from "../useStudio";

/**
 * The interview, as a page.
 *
 * Four questions decide a 40-second film — how the time is split, how many camera setups, who
 * is in it, what the last frame leaves you with — and they are section 0 of the authoring
 * brief, which the one-shot path deliberately splices out because a form had answered it. Here
 * the brief goes over whole and the model asks them.
 *
 * The transcript is the board's own `data["chat"]`, from the first message: the board exists
 * before the script does. So a reload does not lose the conversation, the URL is sendable, and
 * the interview and every later board conversation are one history rather than two.
 */
/**
 * The tappable answers in a model turn, if it offered any.
 *
 * The system preamble asks for one option per line beginning with "- ". That is a formatting
 * convention with a graceful degrade, NOT a second specification: the six beat splits and every
 * other choice live in section 0 of the brief and nowhere else, and a reply with no such lines
 * simply shows no chips.
 *
 * Measured against a live turn: the brief's own options come back as
 * "- `8 × 5s` — eight quick beats. Busiest, most cutting energy…", so the whole line is far too
 * long to be a button. The label is what comes before the dash — which is the answer itself,
 * and is what gets sent.
 */
function options(text: string): string[] {
  const found: string[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line.startsWith("- ")) continue;
    const label = line
      .slice(2)
      .split(/\s+[—–]\s+|\s+--\s+/)[0]
      .replace(/`/g, "")
      .replace(/[.,;:]$/, "")
      .trim();
    if (label && label.length <= 48 && !found.includes(label)) found.push(label);
  }
  return found.slice(0, 6);
}

export function TalkItOut() {
  const studio = useStudio();
  const board = studio.board!;
  const [message, setMessage] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  const thinking = Object.values(studio.jobs).some(
    (job) =>
      job.kind === "develop" &&
      job.slug === board.slug &&
      (job.state === "queued" || job.state === "running"),
  );

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [studio.chat.length, thinking]);

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || thinking) return;
    setMessage("");
    void studio.guard(() => api.develop(board.slug, trimmed));
  };

  const last = [...studio.chat].reverse().find((turn) => turn.role !== "user");
  const chips = thinking ? [] : options(last?.text ?? "");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={scroller} className="thin min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {studio.chat.map((turn, index) => (
          <Turn key={index} turn={turn} />
        ))}
        {thinking ? (
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            <span className="h-1.5 w-1.5 rounded-full bg-warm live-dot" />
            {/* Two very different waits behind one spinner, so name the expensive one when it
                starts: an interview turn is seconds, the pass that writes and then marks the
                script is minutes. `announce` moves the job's phase at exactly that moment. */}
            {studio.activeJob?.phase === "writing the script"
              ? "writing the script, then marking it against the brief…"
              : "thinking…"}
          </div>
        ) : null}
      </div>

      {chips.length ? (
        <div className="flex flex-wrap gap-1.5 pt-3">
          {chips.map((chip) => (
            <button
              key={chip}
              onClick={() => send(chip)}
              className="rounded-full bg-soft px-3 py-1.5 text-[11px] text-zinc-700
                transition-colors hover:bg-softer"
            >
              {chip}
            </button>
          ))}
        </div>
      ) : null}

      <div className="pt-3">
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
            placeholder="answer, or say what you want instead…"
          />
          <div className="flex items-center gap-1.5 px-0.5 pt-1">
            {/* Section 0 already handles this word: "If the director replies 'you decide' or
                'defaults', proceed immediately with 2 x 10s + 4 x 5s across 3 shots." One
                button, no new prompt surface, and it is the honest escape hatch for someone
                who wanted the one-shot path after all. */}
            <button
              onClick={() => send("defaults")}
              disabled={thinking}
              title="the brief's own defaults — 2 × 10s + 4 × 5s across 3 shots"
              className="rounded-full px-2 py-1 text-[10px] text-zinc-400 transition-colors
                hover:bg-hover hover:text-zinc-700 disabled:opacity-40"
            >
              you decide — write it now
            </button>
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
 * The brief, from the file, beside the conversation until there is a script to show instead.
 *
 * Rendered as pre-wrapped text rather than through a markdown library: the point is that the
 * director reads the actual specification the model is working from, and a second rendering of
 * those rules — in a component, in a summary, anywhere — is the drift the whole codebase is
 * arranged to prevent.
 */
export function TheBrief() {
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    void api
      .brief()
      .then(setMarkdown)
      .catch((problem) => setFailed(String(problem)));
  }, []);

  if (failed) {
    return <p className="text-[11px] leading-relaxed text-stale">{failed}</p>;
  }
  return (
    <div className="thin max-h-full overflow-y-auto rounded-2xl border border-edge bg-ink p-3">
      <p className="mb-2 text-[10px] uppercase tracking-wide text-zinc-400">
        what it is asking you about
      </p>
      <pre className="whitespace-pre-wrap font-sans text-[11px] leading-relaxed text-zinc-500">
        {markdown ?? "…"}
      </pre>
    </div>
  );
}
