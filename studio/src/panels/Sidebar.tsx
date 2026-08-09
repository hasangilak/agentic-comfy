import { useState } from "react";
import { api, clock, money } from "../api";
import { useStudio } from "../useStudio";
import { Button, RailRow, inputClass } from "../ui";

const CONTAINER_LOOK = {
  cold: { dot: "bg-zinc-300", label: "cold", hint: "no GPU running, nothing billing" },
  deploying: { dot: "bg-warm live-dot", label: "starting", hint: "billing has begun" },
  warm: { dot: "bg-live live-dot", label: "warm", hint: "GPU running and billing" },
  stopping: { dot: "bg-warm", label: "stopping", hint: "tearing the container down" },
};

/**
 * The rail: what exists, what is running, and the one button that makes something new.
 *
 * It replaced a full-width top bar. The bar spent a whole row of the window on state that
 * changes every few minutes -- container, clock, session cost -- and pushed the canvas down;
 * those three now sit here as readouts, and the two controls that are used *while looking at
 * the board* (render, cancel) float over the canvas instead, where the thing they spend money
 * on is.
 *
 * The two entry points below exist for opposite situations. "write it for me" hands the local
 * model the same authoring prompt a human would paste into an AI
 * (prompts/40s-paper-cutout-script.md) and turns a one-line concept into a shot list, which is
 * the fastest way to have something on the canvas. "paste a script" takes a script that already
 * exists -- written by hand, or with an AI somewhere else -- and adopts it verbatim, because
 * talking a model into a script you have already written is slower and lossier than handing it
 * over.
 */
export function Sidebar() {
  const studio = useStudio();
  const [mode, setMode] = useState<"write" | "paste">("write");
  const [concept, setConcept] = useState("");
  const [beats, setBeats] = useState(4);
  const [seconds, setSeconds] = useState(10);
  const [pasted, setPasted] = useState("");
  const [manualStills, setManualStills] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);
  const [open, setOpen] = useState(false);

  const look = CONTAINER_LOOK[studio.container.state];
  const planning = Object.values(studio.jobs).some(
    (job) => job.kind === "plan" && job.state === "running",
  );

  // The storyboard's two counts, off the board rather than tracked here: how many scenes have a
  // shot written and how many have a sketch drawn. Both are what the rows below say, and neither is
  // state this component owns.
  const board = studio.board;
  const panelBeats = (board?.beats ?? [])
    .filter((beat) => beat.panel?.trim())
    .map((beat) => beat.n);
  const written = panelBeats.length;
  const drawn = (board?.beats ?? []).filter((beat) => beat.panel_url).length;
  const busy = (kind: "panel_write" | "panel_draw") =>
    Object.values(studio.jobs).some(
      (job) =>
        job.kind === kind &&
        job.slug === board?.slug &&
        (job.state === "queued" || job.state === "running"),
    );
  const writingPanels = busy("panel_write");
  const drawingPanels = busy("panel_draw");

  const create = () => {
    const trimmed = concept.trim();
    if (!trimmed) return;
    setConcept("");
    setOpen(false);
    void studio.guard(() => api.createReel(trimmed, beats, seconds));
  };

  const adopt = async () => {
    const text = pasted.trim();
    if (!text || importing) return;
    setImporting(true);
    setNotes([]);
    try {
      const result = await api.importReel(text, manualStills);
      setPasted("");
      studio.setError(null);
      // Notes are worth reading before the panel disappears, so they hold it open. With a
      // clean script there is nothing to say and the canvas is what you want to look at.
      setNotes(result.notes);
      if (!result.notes.length) setOpen(false);
      await studio.refreshReels();
      await studio.openReel(result.slug);
    } catch (problem) {
      studio.setError(String(problem));
    } finally {
      setImporting(false);
    }
  };

  const load = async (file: File | undefined) => {
    if (!file) return;
    setNotes([]);
    setPasted(await file.text());
  };

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-edge bg-panel">
      <div className="flex items-center gap-2.5 px-4 py-4">
        <span
          className="flex h-8 w-8 items-center justify-center rounded-xl bg-solid text-[15px]"
          title="paper-cutout stop motion, one shot at a time"
        >
          🎞
        </span>
        <span className="truncate text-[15px] font-semibold text-zinc-900">Paper Reel</span>
      </div>

      <div className="px-3">
        <button
          onClick={() => setOpen((value) => !value)}
          className="flex w-full items-center gap-2 rounded-full bg-solid px-3 py-2.5 text-[13px]
            font-medium text-white transition-colors hover:bg-zinc-800"
        >
          <span className="text-base leading-none">{open ? "×" : "＋"}</span>
          {open ? "close" : "Start creating"}
        </button>
      </div>

      {open ? (
        <div className="mx-3 mt-3 space-y-2.5 rounded-2xl border border-edge bg-ink p-3">
          <div className="flex gap-1 rounded-full bg-panel p-0.5 text-[11px]">
            {(["write", "paste"] as const).map((option) => (
              <button
                key={option}
                onClick={() => setMode(option)}
                className={`flex-1 rounded-full px-2 py-1 transition-colors ${
                  mode === option
                    ? "bg-solid text-white"
                    : "text-zinc-500 hover:text-zinc-800"
                }`}
              >
                {option === "write" ? "write it for me" : "paste a script"}
              </button>
            ))}
          </div>

          {mode === "write" ? (
            <>
              <textarea
                className={`${inputClass} h-20 bg-panel`}
                value={concept}
                onChange={(event) => setConcept(event.target.value)}
                placeholder="a paper pig finds a hidden pond"
                autoFocus
              />
              <div className="flex items-center gap-2 text-[11px] text-zinc-500">
                <label className="flex items-center gap-1">
                  <input
                    type="number"
                    min={1}
                    max={8}
                    value={beats}
                    onChange={(event) => setBeats(Number(event.target.value))}
                    className="w-11 rounded-lg border border-edge bg-panel px-1.5 py-0.5 text-zinc-800"
                  />
                  beats
                </label>
                <span className="ml-auto flex items-center gap-1">
                  {[5, 10].map((option) => (
                    <button
                      key={option}
                      onClick={() => setSeconds(option)}
                      className={`rounded-full px-2 py-0.5 transition-colors ${
                        seconds === option
                          ? "bg-solid font-medium text-white"
                          : "bg-panel text-zinc-500 hover:text-zinc-800"
                      }`}
                    >
                      {option}s
                    </button>
                  ))}
                  each
                </span>
              </div>
              <Button
                tone="primary"
                className="w-full"
                onClick={create}
                disabled={planning || !concept.trim()}
              >
                {planning ? "writing…" : `write ${beats * seconds}s script`}
              </Button>
              <p className="text-[10px] leading-snug text-zinc-400">
                Free — the local model writes the script, picks where the cuts go, then marks its
                own work against the brief. A few minutes; nothing renders yet.
              </p>
            </>
          ) : (
            <>
              <textarea
                className={`${inputClass} thin h-28 bg-panel font-mono`}
                value={pasted}
                onChange={(event) => setPasted(event.target.value)}
                placeholder={'{ "title": …, "style_bible": …, "beats": [ … ] }'}
                spellCheck={false}
                autoFocus
              />
              {/* Decided here rather than after the fact, because the first thing an imported
                  script otherwise offers is a button that generates the stills it describes. */}
              <label
                className="flex cursor-pointer items-start gap-1.5 text-[10px] leading-snug text-zinc-500"
                title="every generate control disappears and the server refuses to spend image
                  quota on this reel — reversible on the script node"
              >
                <input
                  type="checkbox"
                  checked={manualStills}
                  onChange={(event) => setManualStills(event.target.checked)}
                  className="mt-0.5 h-3 w-3 accent-zinc-900"
                />
                <span>I'll supply the stills — don't generate any</span>
              </label>
              <div className="flex items-center gap-2">
                <Button
                  tone="primary"
                  onClick={() => void adopt()}
                  disabled={importing || !pasted.trim()}
                >
                  {importing ? "importing…" : "import script"}
                </Button>
                <label
                  className="cursor-pointer rounded-full bg-panel px-3 py-1.5 text-xs text-zinc-700
                    transition-colors hover:bg-softer"
                >
                  .json
                  <input
                    type="file"
                    accept=".json,application/json,text/plain"
                    className="hidden"
                    onChange={(event) => {
                      void load(event.target.files?.[0]);
                      // Cleared so picking the same file twice fires onChange again.
                      event.target.value = "";
                    }}
                  />
                </label>
              </div>
              {notes.length ? (
                <div className="space-y-1 rounded-xl border border-stale/30 bg-stale/5 p-2">
                  {notes.map((note) => (
                    <p key={note} className="text-[10px] leading-snug text-stale">
                      {note}
                    </p>
                  ))}
                  <p className="text-[10px] leading-snug text-zinc-500">
                    Imported anyway — all of it is fixable on the canvas for free.
                  </p>
                </div>
              ) : (
                <p className="text-[10px] leading-snug text-zinc-400">
                  Free — no model turn at all. Beat order, lengths and cuts arrive exactly as
                  written. <code>prompts/40s-paper-cutout-script.md</code> is the prompt that gets
                  an AI to write one — the same brief “write it for me” uses.
                </p>
              )}
            </>
          )}
        </div>
      ) : null}

      {/* The three services this studio orchestrates, in the shape the reference gives its
          navigation. None of them is a page to visit; each of them can simply not be running,
          and that is the only thing worth a permanent row. */}
      <div className="mt-4 space-y-0.5 px-3">
        <RailRow
          icon={<span className={`inline-block h-2 w-2 rounded-full ${look.dot}`} />}
          label="GPU"
          value={
            studio.container.state === "cold" ? (
              look.label
            ) : (
              <span className="font-mono">{clock(studio.liveSeconds)}</span>
            )
          }
          title={look.hint}
        />
        <RailRow
          icon="🖼"
          label="Stills"
          value={studio.stillsBackend === "papercut" ? "ready" : "offline"}
          tone={studio.stillsBackend === "papercut" ? "quiet" : "warn"}
          title={
            studio.stillsBackend === "papercut"
              ? "Papercut Studio is answering on :8791 — stills render through Gemini"
              : "the image server is not running; start it with `make images`, or upload stills"
          }
        />
        <RailRow
          icon="🧠"
          label={studio.model.model || "language model"}
          value={studio.model.ready ? "ready" : "offline"}
          tone={studio.model.ready ? "quiet" : "warn"}
          title={
            studio.model.ready
              ? "the script, the board edits and the still review — through the Google API"
              : "no Google API key, or it was refused. Put X-GOOG-API-KEY=… in .env."
          }
        />
      </div>

      {/* The storyboard: the whole reel as rough sketches, before anything a render uses exists.
          Here rather than in `CanvasToolbar` on purpose -- that toolbar is the money bar, the two
          controls that spend the GPU, and a panel spends none of it. Nothing here can mark a beat
          stale either, which is what makes it safe to press on a reel that is already rendered. */}
      {board ? (
        <div className="mt-4 space-y-0.5 px-3">
          <div className="px-2.5 pb-1 text-[11px] font-medium text-zinc-400">Storyboard</div>
          <RailRow
            icon="✎"
            label={written ? "rewrite the shots" : "write the shots"}
            value={writingPanels ? "writing…" : `${written}/${board.beats.length}`}
            onClick={() => void studio.guard(() => api.writePanels(board.slug))}
            title={
              "the local model writes the shot size, angle and camera move for every scene at " +
              "once -- it has to see them together to vary them. Free, and it rewrites every " +
              "scene, including the ones already written"
            }
          />
          <RailRow
            icon="▦"
            label={drawn ? "redraw the panels" : "draw the panels"}
            value={drawingPanels ? "drawing…" : `${drawn}/${board.beats.length}`}
            tone={written ? "quiet" : "warn"}
            onClick={() =>
              void studio.guard(() =>
                // Every scene with a shot written, rather than only the ones with no sketch: the
                // button says "redraw" once there are panels, and that is what redraw means.
                api.drawPanels(board.slug, drawn ? panelBeats : undefined),
              )
            }
            title={
              written
                ? "one rough sketch per scene on the cheapest model, then a contact sheet of all " +
                  "of them. Nothing is rendered from a panel, so this changes no beat's state"
                : "write the shots first -- there is nothing for a panel to be a drawing of yet"
            }
          />
          {board.panel_sheet ? (
            <RailRow
              icon="🗇"
              label="open the sheet"
              value={`${drawn} up`}
              onClick={() => window.open(board.panel_sheet!, "_blank")}
              title="every panel on one numbered sheet -- the film read at once, and a file you
                can send someone"
            />
          ) : null}
        </div>
      ) : null}

      <div className="mt-5 px-4 pb-1.5 text-[11px] font-medium text-zinc-400">Recent reels</div>

      <div className="thin flex-1 overflow-y-auto px-2 pb-2">
        {studio.reels.map((reel) => (
          <button
            key={reel.slug}
            onClick={() => void studio.openReel(reel.slug)}
            className={`mb-0.5 flex w-full items-center gap-2.5 rounded-xl p-1.5 text-left
              transition-colors ${reel.slug === studio.slug ? "bg-soft" : "hover:bg-hover"}`}
          >
            {reel.thumb ? (
              <img src={reel.thumb} alt="" className="h-9 w-9 rounded-lg object-cover" />
            ) : (
              <div className="h-9 w-9 rounded-lg bg-softer" />
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[12px] text-zinc-800">{reel.title}</span>
              <span className="block text-[10px] text-zinc-400">
                {reel.beats} beats · {money(reel.spent)}
              </span>
            </span>
          </button>
        ))}
        {!studio.reels.length ? (
          <p className="px-2 py-1 text-[11px] leading-relaxed text-zinc-400">
            No reels yet. Start one above.
          </p>
        ) : null}
      </div>

      {/* The session's spend, and the one control that ends it. Bottom-left is where the
          reference puts the account; here the account is the meter. */}
      <div className="flex items-center gap-2 border-t border-edge px-3 py-3">
        <span
          className="flex h-8 w-8 items-center justify-center rounded-full bg-soft text-[12px]"
          title="this session, estimated from container time"
        >
          ⏱
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[12px] text-zinc-800">{money(studio.sessionCost)}</span>
          <span className="block text-[10px] text-zinc-400">this session</span>
        </span>
        <button
          onClick={() => void studio.guard(() => api.stopApp())}
          title="interrupt anything running and stop the GPU container immediately"
          className="rounded-full px-2.5 py-1 text-[11px] text-zinc-400 transition-colors
            hover:bg-red-50 hover:text-red-600"
        >
          ■ stop
        </button>
      </div>
    </aside>
  );
}
