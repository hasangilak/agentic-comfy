import { useState } from "react";
import { api, money } from "../api";
import { useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";

/**
 * Reel picker plus the two entry points that create a board from nothing.
 *
 * They exist for opposite situations. "write it for me" hands the local model the same
 * authoring prompt a human would paste into an AI (prompts/40s-paper-cutout-script.md) and
 * turns a one-line concept into a shot list, which is the fastest way to have something on
 * the canvas. "paste a script" takes a script that already exists -- written by hand, or with
 * an AI somewhere else -- and adopts it verbatim, because talking a model into a script you
 * have already written is slower and lossier than handing it over.
 */
export function ReelRail() {
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

  const planning = Object.values(studio.jobs).some(
    (job) => job.kind === "plan" && job.state === "running",
  );

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
    <div className="flex w-52 shrink-0 flex-col border-r border-[#26262e] bg-[#16161b]">
      <div className="flex items-center gap-2 border-b border-[#26262e] px-3 py-2">
        <span className="text-[10px] uppercase tracking-wide text-zinc-500">reels</span>
        <Button tone="ghost" className="ml-auto" onClick={() => setOpen((value) => !value)}>
          {open ? "×" : "+ new"}
        </Button>
      </div>

      {open ? (
        <div className="space-y-2 border-b border-[#26262e] p-3">
          <div className="flex gap-1 text-[10px]">
            {(["write", "paste"] as const).map((option) => (
              <button
                key={option}
                onClick={() => setMode(option)}
                className={`flex-1 rounded px-1.5 py-1 ${
                  mode === option
                    ? "bg-[#26262e] text-zinc-200"
                    : "text-zinc-500 hover:bg-[#1f1f26]"
                }`}
              >
                {option === "write" ? "write it for me" : "paste a script"}
              </button>
            ))}
          </div>

          {mode === "write" ? (
            <>
              <textarea
                className={`${inputClass} h-20`}
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
                    className="w-10 rounded bg-[#0d0d10] border border-[#26262e] px-1 py-0.5 text-zinc-200"
                  />
                  beats
                </label>
                <span className="flex items-center gap-1">
                  {[5, 10].map((option) => (
                    <button
                      key={option}
                      onClick={() => setSeconds(option)}
                      className={`rounded px-1.5 py-0.5 ${
                        seconds === option
                          ? "bg-[#d99a4e] font-medium text-[#1a1208]"
                          : "bg-[#26262e] text-zinc-400 hover:bg-[#32323c]"
                      }`}
                    >
                      {option}s
                    </button>
                  ))}
                  each
                </span>
              </div>
              <Button tone="primary" onClick={create} disabled={planning || !concept.trim()}>
                {planning ? "writing…" : `write ${beats * seconds}s script`}
              </Button>
              <p className="text-[10px] leading-snug text-zinc-600">
                Free — the local model writes the script, picks where the cuts go, then
                marks its own work against the brief. A few minutes; nothing renders yet.
              </p>
            </>
          ) : (
            <>
              <textarea
                className={`${inputClass} thin h-28 font-mono`}
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
                  className="mt-0.5 h-3 w-3 accent-[#d99a4e]"
                />
                <span>I'll supply the stills — don't generate any</span>
              </label>
              <div className="flex items-center gap-2">
                <Button tone="primary" onClick={() => void adopt()} disabled={importing || !pasted.trim()}>
                  {importing ? "importing…" : "import script"}
                </Button>
                <label
                  className="cursor-pointer rounded bg-[#26262e] px-2 py-1 text-xs text-zinc-200
                    transition-colors hover:bg-[#32323c]"
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
                <div className="space-y-1 rounded border border-[#f59e0b]/30 bg-[#f59e0b]/10 p-2">
                  {notes.map((note) => (
                    <p key={note} className="text-[10px] leading-snug text-[#f59e0b]">
                      {note}
                    </p>
                  ))}
                  <p className="text-[10px] leading-snug text-zinc-500">
                    Imported anyway — all of it is fixable on the canvas for free.
                  </p>
                </div>
              ) : (
                <p className="text-[10px] leading-snug text-zinc-600">
                  Free — no model turn at all. Beat order, lengths and cuts arrive exactly
                  as written. <code>prompts/40s-paper-cutout-script.md</code> is the prompt
                  that gets an AI to write one — it is the same brief “write it for me” uses.
                </p>
              )}
            </>
          )}
        </div>
      ) : null}

      <div className="thin flex-1 overflow-y-auto p-2">
        {studio.reels.map((reel) => (
          <button
            key={reel.slug}
            onClick={() => void studio.openReel(reel.slug)}
            className={`mb-1 flex w-full items-center gap-2 rounded p-1.5 text-left
              ${reel.slug === studio.slug ? "bg-[#26262e]" : "hover:bg-[#1f1f26]"}`}
          >
            {reel.thumb ? (
              <img src={reel.thumb} alt="" className="h-9 w-6 rounded object-cover" />
            ) : (
              <div className="h-9 w-6 rounded bg-[#0d0d10]" />
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[11px] text-zinc-300">{reel.title}</span>
              <span className="block text-[10px] text-zinc-600">
                {reel.beats} beats · {money(reel.spent)}
              </span>
            </span>
          </button>
        ))}
        {!studio.reels.length ? (
          <p className="p-2 text-[11px] leading-relaxed text-zinc-600">
            No reels yet. Start one with + new.
          </p>
        ) : null}
      </div>
    </div>
  );
}
