import { api } from "../api";
import type { Beat } from "../types";
import { useBusy, useStudio } from "../useStudio";

/**
 * The scene's storyboard panel: a rough grey sketch of the shot, and the one line of shot grammar
 * it is drawn from.
 *
 * It is not a still and must not read as one. A panel reaches no renderer — it conditions nothing,
 * it is handed to no model, and it is in no fingerprint — so nothing here can mark a beat stale or
 * change what a render costs. That is why it is drawn on the cheapest model, and why the sketch is
 * deliberately not in the film's medium: a cheap paper-cutout mini would read as a finished frame.
 *
 * Shown on every join, unlike the reference pictures. A panel is a drawing OF the shot rather than
 * an input TO it, so no join can make one pointless — which is the whole reason `pictures.drawable`
 * refuses on a chained beat and `panels.drawable` does not.
 */
export function Panel({ beat }: { beat: Beat }) {
  const studio = useStudio();
  const board = studio.board!;
  // `beats` is null on a "draw everything" job, which covers this scene too.
  const busy = useBusy(
    "panel_draw",
    (detail) => !Array.isArray(detail.beats) || detail.beats.includes(beat.n),
  );
  const writing = useBusy(
    "panel_write",
    (detail) => !Array.isArray(detail.beats) || detail.beats.includes(beat.n),
  );
  const shot = beat.panel?.trim() ?? "";

  // Nothing written and nothing drawn: the row would be an empty control for a feature this board
  // has not used. The sidebar is where a storyboard is started, for the whole reel at once, because
  // the shot sizes are only worth anything judged against each other.
  if (!shot && !beat.panel_url) return null;

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <span
          className="text-[10px] uppercase tracking-wide text-zinc-500"
          title={
            "a rough sketch of this shot, drawn on the cheapest model. It is a planning picture: " +
            "nothing is rendered from it, so redrawing one costs the render nothing"
          }
        >
          panel
        </span>
        {busy || writing ? (
          <span className="text-[10px] text-live">{writing ? "writing…" : "drawing…"}</span>
        ) : null}
        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={() => void studio.guard(() => api.drawPanel(board.slug, beat.n))}
            disabled={busy || writing || !shot}
            title={
              shot
                ? beat.panel_url
                  ? "draw this panel again"
                  : "draw this panel"
                : "no shot written for this scene yet — write the storyboard from the sidebar"
            }
            className="nodrag text-[10px] text-zinc-400 hover:text-warm disabled:opacity-30"
          >
            ✦
          </button>
          {beat.panel_url ? (
            <button
              onClick={() => void studio.guard(() => api.removePanel(board.slug, beat.n))}
              disabled={busy}
              title="throw this panel away. Nothing is conditioned on it"
              className="nodrag text-[10px] text-zinc-400 hover:text-red-600 disabled:opacity-30"
            >
              ✕
            </button>
          ) : null}
        </div>
      </div>

      {beat.panel_url ? (
        <img
          src={beat.panel_url}
          alt=""
          // object-contain, not cover: the framing IS the content of a panel, so cropping one to
          // fit the node would remove the only thing it was drawn to show.
          className="nodrag h-28 w-full rounded border border-edge bg-ink object-contain"
        />
      ) : null}

      {shot ? (
        <p className="text-[10px] leading-snug text-zinc-500" title={shot}>
          {shot}
        </p>
      ) : null}
    </div>
  );
}
