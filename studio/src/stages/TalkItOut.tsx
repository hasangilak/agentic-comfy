import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Turn } from "../panels/Turn";
import type { ChatTurn, InterviewQuestion } from "../types";
import { useStudio } from "../useStudio";

/**
 * The interview, as a page.
 *
 * Four questions decide a short film — how long it runs and how that time is split, how
 * many camera setups, who is in it, what the last frame leaves you with — and they are
 * section 0 of the authoring brief. The model asks them through `ask_director` as a
 * structured form (checkboxes / fields); prose bullet chips remain as a fallback for older
 * turns that never used the tool.
 *
 * When the last ask has structured (or inferred) questions, answer fields sit under that turn
 * in the transcript — one control per question, one send — rather than a disconnected card
 * below a duplicated numbered list.
 *
 * The transcript is the board's own `data["chat"]`, from the first message.
 */

/** Closed sets that match `develop.DEFAULT_OPTIONS` for older boards without server questions. */
const DEFAULT_OPTIONS: Record<string, string[]> = {
  beats: [
    "4 × 5s",
    "2 × 10s",
    "6 × 5s",
    "8 × 5s",
    "4 × 10s",
    "2 × 10s + 4 × 5s",
    "1 × 10s + 6 × 5s",
    "3 × 10s + 2 × 5s",
    "6 × 10s",
  ],
  shots: ["3 setups", "4 setups", "5 setups", "one long chained take", "no long chained take"],
  cast: ["design them", "I will paste a style bible"],
  // Matches develop.DEFAULT_OPTIONS["tone"] — model often sends tone as kind "text" with no
  // options; without this the form is a blank field for a question that has a closed set.
  tone: [
    "hopeful landing",
    "triumphant survival",
    "bittersweet arrival",
    "quiet wonder",
    "lingering unease",
    "relief and stillness",
  ],
};

type InterviewTopic = "beats" | "shots" | "cast" | "tone";

function topicOf(question: InterviewQuestion): InterviewTopic {
  const id = question.id.trim().toLowerCase();
  if (id === "beats" || id === "shots" || id === "cast" || id === "tone") return id;
  const prompt = question.prompt.toLowerCase();
  if (prompt.includes("beat") || prompt.includes("split") || prompt.includes("duration") || prompt.includes("how long")) return "beats";
  if (prompt.includes("cast") || prompt.includes("style_bible") || prompt.includes("puppet")) return "cast";
  if (prompt.includes("shot") || prompt.includes("camera") || prompt.includes("setup")) return "shots";
  if (
    prompt.includes("tone") ||
    prompt.includes("ending") ||
    prompt.includes("mood") ||
    prompt.includes("final frame")
  ) {
    return "tone";
  }
  return "tone";
}

function defaultOptionsFor(id: string, prompt: string): string[] {
  const key = id.trim().toLowerCase();
  if (DEFAULT_OPTIONS[key]) return [...DEFAULT_OPTIONS[key]];
  const lower = prompt.toLowerCase();
  if (lower.includes("beat") || lower.includes("split") || lower.includes("duration") || lower.includes("how long")) {
    return [...DEFAULT_OPTIONS.beats];
  }
  // Cast prompts often mention a reference from a previous shot, so classify them first.
  if (lower.includes("cast") || lower.includes("style_bible") || lower.includes("puppet")) {
    return [...DEFAULT_OPTIONS.cast];
  }
  if (lower.includes("shot") || lower.includes("camera") || lower.includes("setup")) {
    return [...DEFAULT_OPTIONS.shots];
  }
  if (
    lower.includes("tone") ||
    lower.includes("ending") ||
    lower.includes("mood") ||
    lower.includes("final frame")
  ) {
    return [...DEFAULT_OPTIONS.tone];
  }
  return [];
}

function withDefaults(question: InterviewQuestion): InterviewQuestion {
  if (question.kind === "choice" || question.kind === "multi") {
    if (question.options.length >= 2) return question;
    const filled = defaultOptionsFor(question.id, question.prompt);
    if (filled.length >= 2) {
      return { ...question, kind: topicOf(question) === "shots" ? "multi" : "choice", options: filled };
    }
    return { ...question, kind: "text", options: [] };
  }
  if (question.options.length) return question;
  const filled = defaultOptionsFor(question.id, question.prompt);
  if (filled.length >= 2) {
    return { ...question, kind: topicOf(question) === "shots" ? "multi" : "choice", options: filled };
  }
  return question;
}

/**
 * Recover a form from a prose interview turn that never called ask_director.
 *
 * Numbered lines (`1. …`, `2) …`) become questions. Bullets containing an em-dash are
 * suggestions; other bullets are subquestions and stay in the prompt.
 */
function questionsFromProse(text: string): InterviewQuestion[] {
  const lines = text.split("\n");
  const found: InterviewQuestion[] = [];
  let current: InterviewQuestion | null = null;

  const flush = () => {
    if (!current) return;
    if (current.options.length >= 2) current.kind = "choice";
    found.push(withDefaults(current));
    current = null;
  };

  for (const raw of lines) {
    const line = raw.trim();
    const numbered = line.match(/^\*{0,2}(\d+)[.)]\s+(.*?)\*{0,2}$/);
    if (numbered) {
      flush();
      const prompt = numbered[2]
        .replace(/\*\*/g, "")
        .replace(/\s+/g, " ")
        .trim();
      if (prompt) {
        current = {
          id: `q${numbered[1]}`,
          prompt,
          kind: "text",
          options: [],
        };
      }
      continue;
    }
    if (current && line.startsWith("- ")) {
      const bullet = line.slice(2).replace(/\s+/g, " ").trim();
      const separator = bullet.match(/\s+[—–]\s+|\s+--\s+/);
      const backtick = bullet.match(/^`([^`]+)`/);
      if (separator || backtick) {
        const label = (backtick?.[1] ?? bullet.slice(0, separator?.index))
          .replace(/`/g, "")
          .replace(/[.,;:]$/, "")
          .trim();
        if (label && label.length <= 72 && !current.options.includes(label)) {
          current.options.push(label);
        }
      } else if (bullet) {
        current.prompt = `${current.prompt} ${bullet}`;
      }
      continue;
    }
    // Models often put the heading and question body on separate lines.
    if (current && line) {
      current.prompt = `${current.prompt} ${line.replace(/\*\*/g, "")}`;
    }
  }
  flush();
  return found.slice(0, 8);
}

/** Intro above the first numbered question — the form carries the questions themselves. */
function preambleOf(text: string): string {
  const lines: string[] = [];
  for (const raw of text.split("\n")) {
    if (/^\*{0,2}\d+[.)]\s+/.test(raw.trim())) break;
    lines.push(raw);
  }
  return lines.join("\n").trim();
}

type AnswerState = {
  defer: boolean;
  choice: string;
  multi: string[];
  text: string;
};

function emptyAnswer(): AnswerState {
  return { defer: false, choice: "", multi: [], text: "" };
}

function formatAnswers(
  questions: InterviewQuestion[],
  answers: Record<string, AnswerState>,
): { message: string; values: Partial<Record<InterviewTopic, string>>; complete: boolean } {
  const lines: string[] = [];
  const values: Partial<Record<InterviewTopic, string>> = {};
  let deferred = 0;
  for (const question of questions) {
    const state = answers[question.id] ?? emptyAnswer();
    let value = "";
    if (state.defer) {
      value = "you decide";
      deferred += 1;
    } else if (state.text.trim()) {
      value = state.text.trim();
    } else if (question.kind === "choice" && state.choice) {
      value = state.choice;
    } else if (question.kind === "multi" && state.multi.length) {
      value = state.multi.join("; ");
    }
    if (!value) continue;
    values[topicOf(question)] = value;
    lines.push(`${question.prompt}: ${value}`);
  }
  const complete = Object.keys(values).length === questions.length;
  return {
    message: deferred === questions.length ? "defaults" : lines.join("\n"),
    values,
    complete,
  };
}

export function TalkItOut() {
  const studio = useStudio();
  const board = studio.board!;
  const [message, setMessage] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const written = board.beats.length > 0;

  const thinking = Object.values(studio.jobs).some(
    (job) =>
      job.kind === "develop" &&
      job.slug === board.slug &&
      (job.state === "queued" || job.state === "running"),
  );

  const send = (text: string, answers?: Partial<Record<InterviewTopic, string>>) => {
    const trimmed = text.trim();
    if (!trimmed || thinking) return;
    setMessage("");
    void studio.guard(() => api.develop(board.slug, trimmed, answers));
  };

  const lastIndex = (() => {
    for (let i = studio.chat.length - 1; i >= 0; i -= 1) {
      if (studio.chat[i].role !== "user") return i;
    }
    return -1;
  })();
  const last: ChatTurn | undefined = lastIndex >= 0 ? studio.chat[lastIndex] : undefined;

  // Structured ask_director turns always get a form. Prose inference only while the script
  // is still being negotiated — otherwise an old numbered list under a finished draft would
  // resurrect a form nobody can usefully submit.
  const structured = !thinking ? (last?.questions ?? []).map(withDefaults) : [];
  const inferred = useMemo(() => {
    if (written || thinking || structured.length || !last?.text) return [];
    return questionsFromProse(last.text);
  }, [written, thinking, structured.length, last?.text]);
  const formQuestions = structured.length ? structured : inferred;
  const formQuestionsKey = formQuestions.map((question) => question.id).join("|");
  const askPreamble = formQuestions.length && last?.text ? preambleOf(last.text) : "";

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [studio.chat.length, thinking, formQuestionsKey]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={scroller} className="thin min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {studio.chat.map((turn, index) => {
          if (index === lastIndex && formQuestions.length) {
            return (
              <div key={index} className="space-y-3">
                {askPreamble ? (
                  <div className="whitespace-pre-wrap text-xs leading-relaxed text-zinc-700">
                    {askPreamble}
                  </div>
                ) : null}
                <InterviewForm
                  questions={formQuestions}
                  disabled={thinking}
                  onSubmit={(text, answers) => send(text, answers)}
                  onDeferAll={() => send("defaults")}
                />
              </div>
            );
          }
          return <Turn key={index} turn={turn} />;
        })}
        {thinking ? (
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            <span className="h-1.5 w-1.5 rounded-full bg-warm live-dot" />
            {studio.activeJob?.phase === "writing the script"
              ? "writing the script, then marking it against the brief…"
              : "thinking…"}
          </div>
        ) : null}
      </div>

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
            placeholder={
              written
                ? "say what to change…"
                : formQuestions.length
                  ? "or type a free answer instead…"
                  : "answer, or say what you want instead…"
            }
          />
          <div className="flex items-center gap-1.5 px-0.5 pt-1">
            {!written ? (
              <button
                onClick={() => send("defaults")}
                disabled={thinking}
                title="leave every remaining choice to the model — 2 × 10s + 4 × 5s across 3 shots"
                className="rounded-full px-2 py-1 text-[10px] text-zinc-400 transition-colors
                  hover:bg-hover hover:text-zinc-700 disabled:opacity-40"
              >
                it&apos;s on you — write it now
              </button>
            ) : null}
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
 * Structured answers for the last ask_director (or prose-inferred) turn.
 *
 * Each question can be deferred with "it's on you". Deferring every question sends the brief's
 * magic word `defaults`, which is what makes the model write immediately.
 */
function InterviewForm({
  questions,
  disabled,
  onSubmit,
  onDeferAll,
}: {
  questions: InterviewQuestion[];
  disabled: boolean;
  onSubmit: (text: string, answers: Partial<Record<InterviewTopic, string>>) => void;
  onDeferAll: () => void;
}) {
  const [answers, setAnswers] = useState<Record<string, AnswerState>>(() =>
    Object.fromEntries(questions.map((question) => [question.id, emptyAnswer()])),
  );

  // Reset when the model asks a new set — ids change, so a stale draft would answer the wrong thing.
  useEffect(() => {
    setAnswers(Object.fromEntries(questions.map((question) => [question.id, emptyAnswer()])));
  }, [questions.map((question) => question.id).join("|")]);

  const patch = (id: string, next: Partial<AnswerState>) => {
    setAnswers((was) => ({
      ...was,
      [id]: { ...(was[id] ?? emptyAnswer()), ...next },
    }));
  };

  const submission = formatAnswers(questions, answers);
  const canSend = Boolean(submission.message) && submission.complete && !disabled;

  return (
    <div className="space-y-3 rounded-2xl border border-edge bg-panel p-3">
      <div className="flex items-baseline gap-2">
        <span className="text-[10px] font-medium uppercase tracking-wide text-zinc-400">
          your answers
        </span>
        <button
          type="button"
          onClick={onDeferAll}
          disabled={disabled}
          className="ml-auto rounded-full px-2 py-0.5 text-[10px] text-zinc-400
            transition-colors hover:bg-hover hover:text-zinc-700 disabled:opacity-40"
          title="leave every question to the model and write the script now"
        >
          it&apos;s on you for all
        </button>
      </div>

      {questions.map((question, index) => {
        const state = answers[question.id] ?? emptyAnswer();
        return (
          <div key={question.id} className="space-y-1.5 border-t border-edge pt-2.5 first:border-0 first:pt-0">
            <div className="flex items-start gap-2">
              <span className="mt-0.5 w-4 shrink-0 text-[10px] text-zinc-300">{index + 1}</span>
              <p className="min-w-0 flex-1 text-[12px] leading-relaxed text-zinc-800">
                {question.prompt}
              </p>
              <button
                type="button"
                onClick={() => patch(question.id, { defer: !state.defer })}
                disabled={disabled}
                className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] transition-colors
                  disabled:opacity-40 ${
                    state.defer
                      ? "bg-solid text-white"
                      : "bg-soft text-zinc-500 hover:bg-softer hover:text-zinc-800"
                  }`}
                title="let the model decide this one"
              >
                it&apos;s on you
              </button>
            </div>

            {state.defer ? (
              <p className="pl-6 text-[11px] text-zinc-400">Leaving this one to the model.</p>
            ) : (
              <div className="space-y-2 pl-6">
                {question.kind === "choice" ? (
                  <div className="flex flex-wrap gap-1.5">
                    {question.options.map((option) => {
                      const on = state.choice === option;
                      return (
                        <button
                          key={option}
                          type="button"
                          disabled={disabled}
                          onClick={() =>
                            patch(question.id, {
                              choice: on ? "" : option,
                              text: "",
                            })
                          }
                          className={`rounded-full px-3 py-1.5 text-[11px] transition-colors
                            disabled:opacity-40 ${
                              on
                                ? "bg-solid text-white"
                                : "bg-soft text-zinc-700 hover:bg-softer"
                            }`}
                        >
                          {option}
                        </button>
                      );
                    })}
                  </div>
                ) : question.kind === "multi" ? (
                  <div className="flex flex-wrap gap-1.5">
                    {question.options.map((option) => {
                      const on = state.multi.includes(option);
                      return (
                        <button
                          key={option}
                          type="button"
                          disabled={disabled}
                          onClick={() => {
                            let multi = on
                              ? state.multi.filter((item) => item !== option)
                              : [...state.multi, option];
                            if (!on && topicOf(question) === "shots") {
                              if (option.includes("setups")) {
                                multi = multi.filter(
                                  (item) => item === option || !item.includes("setups"),
                                );
                              } else if (option.includes("long chained take")) {
                                multi = multi.filter(
                                  (item) =>
                                    item === option || !item.includes("long chained take"),
                                );
                              }
                            }
                            patch(question.id, { multi, text: "" });
                          }}
                          className={`rounded-full px-3 py-1.5 text-[11px] transition-colors
                            disabled:opacity-40 ${
                              on
                                ? "bg-solid text-white"
                                : "bg-soft text-zinc-700 hover:bg-softer"
                            }`}
                        >
                          {option}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
                <textarea
                  className="thin w-full resize-none rounded-xl border border-edge bg-ink px-2.5
                    py-2 text-[11px] leading-relaxed text-zinc-800 outline-none
                    placeholder:text-zinc-400"
                  rows={2}
                  disabled={disabled}
                  value={state.text}
                  onChange={(event) =>
                    patch(question.id, {
                      text: event.target.value,
                      choice: "",
                      multi: [],
                    })
                  }
                  placeholder={
                    question.kind === "text" ? "your answer…" : "or write a custom answer…"
                  }
                />
              </div>
            )}
          </div>
        );
      })}

      <button
        type="button"
        disabled={!canSend}
        onClick={() => onSubmit(submission.message, submission.values)}
        className="w-full rounded-xl bg-solid px-3 py-2 text-[12px] text-white
          transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-30"
      >
        {submission.message === "defaults" ? "it's on you — write it now" : "send answers"}
      </button>
      {!submission.complete ? (
        <p className="text-center text-[10px] text-zinc-400">
          Answer each question or leave it to the model.
        </p>
      ) : null}
    </div>
  );
}

/**
 * The brief, from the file, beside the conversation until there is a script to show instead.
 */
export function TheBrief() {
  const studio = useStudio();
  const medium = studio.board?.medium;
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    setMarkdown(null);
    setFailed(null);
    void api
      .brief(medium)
      .then(setMarkdown)
      .catch((problem) => setFailed(String(problem)));
  }, [medium]);

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
