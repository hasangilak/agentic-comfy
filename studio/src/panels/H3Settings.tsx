import { useEffect, useState } from "react";
import { api } from "../api";
import { useStudio } from "../useStudio";
import { inputClass } from "../ui";

/**
 * MiniMax-H3 sampling, next to the button that spends it.
 *
 * H3 itself has no temperature socket -- steps, seed and a baked-in flow shift of 12/3 are
 * what MiniMax ships. Temperature here is ComfyUI TemporalScoreRescaling's k: 1 omits the
 * node (unchanged sampling), lower is sharper, higher is smoother. Unmeasured on this model.
 *
 * Steps and seed already lived on the board with no control; they sit here because a
 * temperature slider next to a frozen "8 steps" readout would be the incomplete half of
 * the same decision.
 */
export function H3Settings() {
  const studio = useStudio();
  const board = studio.board!;
  const [open, setOpen] = useState(false);
  const [lo, hi] = board.temperature_range ?? [0.1, 2];
  const current = board.temperature ?? 1;
  const moved = current !== 1;
  const [temp, setTemp] = useState(current);
  useEffect(() => setTemp(current), [current]);

  const commitTemp = (next: number) => {
    const clamped = Math.round(Math.max(lo, Math.min(hi, next)) * 100) / 100;
    setTemp(clamped);
    if (clamped !== current) {
      void studio.guard(() => api.patchBoard(board.slug, { temperature: clamped }));
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((next) => !next)}
        className={`rounded-full px-2.5 py-1 text-[11px] transition-colors
          ${open ? "bg-soft text-zinc-800" : "text-zinc-500 hover:bg-soft hover:text-zinc-700"}`}
        title="MiniMax-H3 sampling: temperature, steps, seed"
      >
        H3{moved ? ` ${current.toFixed(2)}` : ""}
      </button>
      {open ? (
        <div
          className="lift absolute left-1/2 top-full z-20 mt-2 w-64 -translate-x-1/2
            space-y-2.5 rounded-xl border border-edge bg-panel p-3"
        >
          <p className="text-[10px] uppercase tracking-wide text-zinc-400">MiniMax-H3 sampling</p>
          <label className="block">
            <span className="mb-1 flex items-center justify-between text-[10px] text-zinc-600">
              <span>temperature</span>
              <span className="font-mono text-zinc-800">{temp.toFixed(2)}</span>
            </span>
            <input
              type="range"
              min={lo}
              max={hi}
              step={0.05}
              value={temp}
              onChange={(event) => setTemp(Number(event.target.value))}
              onPointerUp={(event) =>
                commitTemp(Number((event.target as HTMLInputElement).value))
              }
              onKeyUp={(event) =>
                commitTemp(Number((event.target as HTMLInputElement).value))
              }
              className="w-full accent-zinc-800"
              title="1 is the default (unchanged sampling). Lower is sharper; higher is smoother."
            />
            <span className="mt-0.5 block text-[10px] leading-snug text-zinc-400">
              1 is unchanged. Lower is sharper; higher is smoother. Marks every scene edited.
            </span>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <NumberField
              label="steps"
              value={board.steps}
              min={1}
              max={30}
              title="8 is the measured default; 20 costs about 70% more"
              onCommit={(next) =>
                void studio.guard(() => api.patchBoard(board.slug, { steps: next }))
              }
            />
            <NumberField
              label="seed"
              value={board.seed}
              min={0}
              max={999_999_999}
              title="base seed; each scene adds its number"
              onCommit={(next) =>
                void studio.guard(() => api.patchBoard(board.slug, { seed: next }))
              }
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  title,
  onCommit,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  title: string;
  onCommit: (next: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);

  const commit = () => {
    const parsed = Number(draft);
    if (!Number.isFinite(parsed)) {
      setDraft(String(value));
      return;
    }
    const next = Math.max(min, Math.min(max, Math.round(parsed)));
    setDraft(String(next));
    if (next !== value) onCommit(next);
  };

  return (
    <label className="block" title={title}>
      <span className="mb-1 block text-[10px] text-zinc-600">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === "Enter") (event.target as HTMLInputElement).blur();
        }}
        className={`${inputClass} h-8 py-1 text-[11px]`}
      />
    </label>
  );
}
