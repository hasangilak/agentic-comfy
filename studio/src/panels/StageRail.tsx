import { money } from "../api";
import { STAGES, STAGE_JOBS, type Stage } from "../route";
import type { Board } from "../types";
import { useStudio } from "../useStudio";

/**
 * Where the film is up to, and the way between the four stages of making it.
 *
 * `RailRow`'s own docstring used to end "ours has nothing to navigate to". It does now — but
 * this is still the left column rather than a bar across the top, and for the reason the top
 * bar was killed: the Storyboard grid and the Assets still both want the height, and stage
 * navigation costs none of it here.
 *
 * **Every readout below is derived on the client from fields `to_json` already publishes.**
 * Nothing here is persisted, nothing here is a new server field, and no stage is ever gated:
 * `storyboard.py` can stop at any stage, an imported script may arrive with its stills already
 * made, and a `manual_stills` board skips the third stage entirely. A rail that locked a stage
 * would be the studio lying about a design whose whole claim is that the stages are separable.
 * What replaces a gate is the "waiting on" strip at the top of each page.
 */
export function StageRail() {
  const studio = useStudio();
  const board = studio.board;
  if (!board) return null;

  const at = studio.resolvedStage;
  const running = studio.activeJob?.slug === board.slug ? studio.activeJob : null;

  return (
    <div className="mt-4 space-y-0.5 px-3">
      <div className="px-2.5 pb-1 text-[11px] font-medium text-zinc-400">Stages</div>
      {STAGES.map((stage) => {
        const read = readout(stage.id, board);
        // A crew job spans stages, so it is in no `STAGE_JOBS` list -- and putting it in all
        // three would light all three at once, which is the opposite of what the dot is for.
        // The server writes the stage it is currently in as the first word of `Job.phase`, so
        // that is read instead. A lone `agent` job is not attributed at all: an agent works
        // more than one stage now, so its name does not say which one it is on.
        const live = running
          ? STAGE_JOBS[stage.id].includes(running.kind) ||
            (running.kind === "crew" && running.phase.startsWith(`${stage.id} · `))
          : false;
        const here = stage.id === at;
        return (
          <button
            key={stage.id}
            onClick={() => studio.goStage(stage.id)}
            title={stage.blurb + (read.hint ? ` — ${read.hint}` : "")}
            className={`flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left
              transition-colors ${here ? "bg-soft" : "hover:bg-hover"}`}
          >
            <span className="w-4 shrink-0 text-center text-[13px] text-zinc-400">
              {live ? (
                <span className="inline-block h-2 w-2 rounded-full bg-warm live-dot" />
              ) : (
                stage.glyph
              )}
            </span>
            <span
              className={`min-w-0 flex-1 truncate text-[13px] ${
                here ? "font-medium text-zinc-900" : "text-zinc-700"
              }`}
            >
              {stage.label}
            </span>
            <span
              className={`shrink-0 text-[11px] ${read.warn ? "text-warm" : "text-zinc-400"}`}
            >
              {read.value}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/**
 * One stage's state of play. The Studio row is the only one that ever names money, which is
 * the same rule the money bar keeps: the price is on the thing that spends it.
 */
function readout(stage: Stage, board: Board): { value: string; warn: boolean; hint: string } {
  const total = board.beats.length;
  switch (stage) {
    case "script": {
      if (!total) return { value: "empty", warn: true, hint: "no scenes yet" };
      const seconds = board.beats.reduce((sum, beat) => sum + beat.actual_seconds, 0);
      return {
        value: `${total} · ${seconds.toFixed(0)}s`,
        warn: !board.style_bible.trim(),
        hint: board.style_bible.trim() ? "free to edit" : "no style bible written",
      };
    }
    case "storyboard": {
      // The same two counts the rail carried before the stages existed: how many scenes have a
      // shot written, and how many have a sketch drawn.
      const written = board.beats.filter((beat) => beat.panel?.trim()).length;
      const drawn = board.beats.filter((beat) => beat.panel_url).length;
      return {
        value: total ? `${drawn}/${total}` : "—",
        warn: Boolean(total) && !written,
        hint: `${written} of ${total} shots written, ${drawn} drawn · ${board.staging.length} designs · free`,
      };
    }
    case "assets": {
      if (board.manual_stills) return { value: "manual", warn: false, hint: "your own stills" };
      const missing = board.assets_needed.length;
      return {
        value: total ? `${total - missing}/${total}` : "—",
        warn: missing > 0,
        hint: missing ? `${missing} without the still they open on · cents each` : "all drawn",
      };
    }
    case "studio": {
      const pending = board.pending.length;
      if (!pending) {
        return {
          value: board.reel ? "reel up" : "—",
          warn: false,
          hint: board.reel ? "stitched and ready" : "nothing to render",
        };
      }
      return {
        value: money(board.pending_cost.predicted_cost),
        warn: false,
        hint: `${pending} scene${pending === 1 ? "" : "s"} to render — the only stage that spends`,
      };
    }
  }
}
