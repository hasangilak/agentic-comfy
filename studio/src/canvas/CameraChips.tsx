import type { Board } from "../types";

/**
 * The five locked-off angles, as chips. Same control on the scene, the expanded scene, and
 * the batch bar — one component so a sixth angle cannot appear in only one of them.
 *
 * `value` is the resolved key the board publishes (absent on disk means `eye`). An empty
 * string means a mixed selection: none of the chips look active, and the next click writes
 * one angle onto every take in the batch.
 */
export function CameraChips({
  board,
  value,
  onChange,
}: {
  board: Board;
  value: string;
  onChange: (camera: string) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      {(board.cameras ?? []).map((option) => {
        const active = value === option.id;
        return (
          <button
            key={option.id}
            type="button"
            onClick={() => onChange(option.id)}
            className={`rounded px-1.5 py-0.5 text-[11px] transition-colors ${
              active
                ? "bg-solid font-medium text-white"
                : "bg-soft text-zinc-600 hover:bg-softer"
            }`}
            title={active ? option.label : `switch to ${option.label}`}
          >
            {option.chip}
          </button>
        );
      })}
    </div>
  );
}
