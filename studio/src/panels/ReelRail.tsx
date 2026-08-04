import { useState } from "react";
import { api, money } from "../api";
import { useStudio } from "../useStudio";
import { Button, inputClass } from "../ui";

/** Reel picker plus the one entry point that creates a board from nothing. */
export function ReelRail() {
  const studio = useStudio();
  const [concept, setConcept] = useState("");
  const [beats, setBeats] = useState(4);
  const [seconds, setSeconds] = useState(10);
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
            Free — agy writes the script and the shot list. Nothing renders yet.
          </p>
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
