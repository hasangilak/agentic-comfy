import { useState } from "react";
import { api } from "../api";
import { useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";

/** Shown until `/api/agents` answers, so the start screen is not a blank pair of chips. */
const FALLBACK_MEDIUMS = [
  { key: "paper-cutout", name: "paper-cutout stop-motion" },
  { key: "claymation", name: "clay stop-motion" },
];

/**
 * What the puppets are physically made of — not a tint, the rules the script is written against.
 *
 * Paper is rigid and hinged; clay squashes and stretches. A clay film drafted under paper's
 * physics comes out stiff, which is why this is a create-time pick rather than a PATCH after
 * the model has already written the beats. `board.mediums` is the live catalogue; the fallback
 * is the two that ship, so a start screen that has not fetched yet still has something to tap.
 */
export function MediumPicker({
  value,
  options,
  onChange,
}: {
  value: string;
  options?: { key: string; name: string }[];
  onChange: (key: string) => void;
}) {
  const list = options?.length ? options : FALLBACK_MEDIUMS;
  return (
    <div>
      <span className="mb-1 block text-[10px] uppercase tracking-wide text-zinc-400">medium</span>
      <div className="grid grid-cols-2 gap-2">
        {list.map((entry) => {
          const selected = entry.key === value;
          return (
            <button
              key={entry.key}
              type="button"
              onClick={() => onChange(entry.key)}
              className={`rounded-xl border px-3 py-2.5 text-left transition-colors ${
                selected
                  ? "border-transparent bg-solid text-white"
                  : "border-edge bg-ink text-zinc-700 hover:border-zinc-300 hover:bg-hover"
              }`}
            >
              <span className="block text-[12px] font-medium capitalize">
                {entry.key.replace(/-/g, " ")}
              </span>
              <span
                className={`mt-0.5 block text-[10px] leading-snug ${
                  selected ? "text-white/70" : "text-zinc-400"
                }`}
              >
                {entry.key === "claymation"
                  ? "soft, heavy — shapes squash and stretch"
                  : entry.key === "paper-cutout"
                    ? "rigid, flat, hinged — shapes are swapped"
                    : entry.name}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * The ways a reel begins, as components rather than as a panel.
 *
 * They lived in a 24 rem collapsible in the left rail, which is the wrong size for the one
 * decision that shapes everything after it. Two surfaces show them now — the start screen and
 * the Script stage — so they are components, not a place.
 *
 * The two exist for opposite situations, and both are first-class. "Write it for me" hands the
 * model the same authoring prompt a human would paste into an AI elsewhere
 * (prompts/40s-stop-motion-script.md) and turns a one-line concept into a shot list. "Paste a
 * script" takes one that already exists and adopts it verbatim, because talking a model into a
 * script you have already written is slower and lossier than handing it over.
 */

/**
 * One line about the film, and then a conversation about it.
 *
 * The reel exists the moment this is sent — with no beats yet — so the page can move to the
 * interview while the model is still reading the brief. That the board comes first is the whole
 * design: `storyboard.json` stays the only store, and the transcript survives a reload.
 */
export function TalkItThrough({ medium }: { medium: string }) {
  const studio = useStudio();
  const [concept, setConcept] = useState("");
  const [starting, setStarting] = useState(false);

  const begin = async () => {
    const trimmed = concept.trim();
    if (!trimmed || starting) return;
    setStarting(true);
    try {
      const started = await api.developReel(trimmed, medium);
      studio.setError(null);
      setConcept("");
      await studio.refreshReels();
      await studio.openReel(started.slug, "script");
    } catch (problem) {
      studio.setError(String(problem));
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="space-y-2.5">
      <textarea
        className={`${inputClass} h-20`}
        value={concept}
        onChange={(event) => setConcept(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void begin();
          }
        }}
        placeholder={medium === "claymation" ? "a clay pig finds a hidden pond" : "a paper pig finds a hidden pond"}
        autoFocus
      />
      <Button
        tone="primary"
        className="w-full"
        onClick={() => void begin()}
        disabled={starting || !concept.trim()}
      >
        {starting ? "starting…" : "talk it through"}
      </Button>
      <p className="text-[10px] leading-snug text-zinc-400">
        It asks four questions first — how long the film runs and how that time is split,
        how many camera setups, who is in it, what the last frame leaves you with — and
        writes the script once you have answered. Say <em>defaults</em> at any point and it
        picks. Nothing renders.
      </p>
    </div>
  );
}

/** One concept, a beat count and a length: the model writes the whole script in one pass. */
export function WriteItForMe({
  medium,
  onStarted,
}: {
  medium: string;
  onStarted?: () => void;
}) {
  const studio = useStudio();
  const [concept, setConcept] = useState("");
  const [beats, setBeats] = useState(4);
  const [seconds, setSeconds] = useState(10);

  const planning = Object.values(studio.jobs).some(
    (job) => job.kind === "plan" && job.state === "running",
  );

  const create = () => {
    const trimmed = concept.trim();
    if (!trimmed) return;
    setConcept("");
    onStarted?.();
    void studio.guard(() => api.createReel(trimmed, beats, seconds, medium));
  };

  return (
    <div className="space-y-2.5">
      <textarea
        className={`${inputClass} h-20`}
        value={concept}
        onChange={(event) => setConcept(event.target.value)}
        placeholder={medium === "claymation" ? "a clay pig finds a hidden pond" : "a paper pig finds a hidden pond"}
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
                  : "bg-soft text-zinc-500 hover:text-zinc-800"
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
        The model writes the script, picks where the cuts go, then marks its own work against the
        brief. A few minutes; nothing renders yet. Every beat comes out the same length — the one
        thing this path settles for you.
      </p>
    </div>
  );
}

/** A script written outside the studio, adopted verbatim. No model turn at all. */
export function PasteAScript({
  medium,
  onImported,
}: {
  medium: string;
  onImported?: (notes: string[]) => void;
}) {
  const studio = useStudio();
  const [pasted, setPasted] = useState("");
  const [manualStills, setManualStills] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);

  const adopt = async () => {
    const text = pasted.trim();
    if (!text || importing) return;
    setImporting(true);
    setNotes([]);
    try {
      const result = await api.importReel(text, manualStills, medium);
      setPasted("");
      studio.setError(null);
      // Notes are worth reading before the page changes under them, so they are shown here as
      // well as followed. Every one of them is fixable for free.
      setNotes(result.notes);
      onImported?.(result.notes);
      await studio.refreshReels();
      await studio.openReel(result.slug, "storyboard");
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
    <div className="space-y-2.5">
      <textarea
        className={`${inputClass} thin h-40 font-mono`}
        value={pasted}
        onChange={(event) => setPasted(event.target.value)}
        placeholder={'{ "title": …, "style_bible": …, "beats": [ … ] }'}
        spellCheck={false}
      />
      {/* Decided here rather than after the fact, because the first thing an imported script
          otherwise offers is a button that generates the stills it describes. */}
      <label
        className="flex cursor-pointer items-start gap-1.5 text-[10px] leading-snug text-zinc-500"
        title="every generate control disappears and the server refuses to spend anything on
          stills for this reel — reversible later"
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
        <Button tone="primary" onClick={() => void adopt()} disabled={importing || !pasted.trim()}>
          {importing ? "importing…" : "import script"}
        </Button>
        <label
          className="cursor-pointer rounded-full bg-soft px-3 py-1.5 text-xs text-zinc-700
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
            Imported anyway — all of it is fixable for free.
          </p>
        </div>
      ) : (
        <p className="text-[10px] leading-snug text-zinc-400">
          Free — no model turn at all. Beat order, lengths and cuts arrive exactly as written.
          Pick the medium this script was written for — a clay script imported as paper fights
          its own reviewer. <code>prompts/40s-stop-motion-script.md</code> is the prompt that
          gets an AI to write one — the same brief “write it for me” uses.
        </p>
      )}
    </div>
  );
}
