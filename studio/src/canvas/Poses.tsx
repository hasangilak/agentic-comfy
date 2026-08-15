import type { Beat } from "../types";
import { useStudio } from "../useStudio";

/**
 * The stop-motion poses this clip interpolates through: film-medium stills, opening first.
 *
 * Not the graphite panels. Those condition the still and never reach H3; these ARE what H3
 * is handed, up to nine, and a 240px card that only showed the opening still made a five-pose
 * stack read as one reference. Judged on Assets -- the canvas is the chain, not the still.
 *
 * Absent until there is more than the opening frame, so a board that never grew a sequence
 * looks exactly as it did.
 */
export function Poses({ beat }: { beat: Beat }) {
  const studio = useStudio();
  const urls = beat.poses ?? [];
  if (urls.length <= 1) return null;

  return (
    <div className="space-y-1">
      <button
        onClick={() => studio.goStage("assets")}
        className="nodrag flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left
          text-[10px] uppercase tracking-wide text-zinc-500 hover:bg-soft"
        title={
          "stop-motion poses this clip interpolates through. The video model is handed these, " +
          "not the graphite panels. Judged on Assets"
        }
      >
        <span>poses · {urls.length}</span>
        <span className="ml-auto shrink-0 font-normal normal-case text-zinc-400">→</span>
      </button>
      <div className="nodrag nowheel flex gap-1 overflow-x-auto">
        {urls.map((url, at) => (
          <img
            key={`${url}-${at}`}
            src={url}
            alt=""
            title={`pose ${at + 1} of ${urls.length}`}
            className={`h-16 w-10 shrink-0 rounded border border-edge bg-ink object-cover ${
              at === 0 ? "ring-1 ring-solid" : ""
            }`}
          />
        ))}
      </div>
    </div>
  );
}
