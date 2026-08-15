import { api } from "../api";
import { panelUrls } from "../beat";
import type { Beat } from "../types";
import { useBusy, useStudio } from "../useStudio";

/**
 * The scene's storyboard panels: rough grey sketches of the shot through the action, and the
 * one line of shot grammar the opening is drawn from.
 *
 * They are not stills and must not read as such. Panels condition the still as composition
 * sketches and are handed to H3 never, and they are in no fingerprint — so nothing here can
 * mark a beat stale or change what a render costs. That is why they are drawn on the cheapest
 * model, and why the sketch is deliberately not in the film's medium: a cheap paper-cutout mini
 * would read as a finished frame.
 *
 * Shown on every join, unlike the reference pictures. A panel is a drawing of the shot that
 * the still is then drawn from, so no join can make one pointless — which is the whole reason
 * `pictures.drawable` refuses on a chained beat and `panels.drawable` does not.
 */
export function Panel({ beat }: { beat: Beat }) {
  const studio = useStudio();
  const board = studio.board!;
  const busy = useBusy(
    "panel_draw",
    (detail) => !Array.isArray(detail.beats) || detail.beats.includes(beat.n),
  );
  const writing = useBusy(
    "panel_write",
    (detail) => !Array.isArray(detail.beats) || detail.beats.includes(beat.n),
  );
  const shot = beat.panel?.trim() ?? "";
  const extras = (beat.panel_frames ?? []).filter((line) => line.trim());
  const urls = panelUrls(beat);

  if (!shot && !urls.length) return null;

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <span
          className="text-[10px] uppercase tracking-wide text-zinc-500"
          title={
            "rough sketches of this shot through the action, drawn on the cheapest model. " +
            "Planning pictures: nothing in the clip is rendered from them, so redrawing " +
            "costs the render nothing"
          }
        >
          {urls.length > 1 ? `panels · ${urls.length}` : "panel"}
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
                ? urls.length
                  ? "draw these panels again"
                  : "draw these panels"
                : "no shot written for this scene yet — write the storyboard from the sidebar"
            }
            className="nodrag text-[10px] text-zinc-400 hover:text-warm disabled:opacity-30"
          >
            ✦
          </button>
          {urls.length ? (
            <button
              onClick={() => void studio.guard(() => api.removePanel(board.slug, beat.n))}
              disabled={busy}
              title="throw these panels away. Nothing in the clip is conditioned on them"
              className="nodrag text-[10px] text-zinc-400 hover:text-red-600 disabled:opacity-30"
            >
              ✕
            </button>
          ) : null}
        </div>
      </div>

      {urls.length ? (
        <div className="nodrag flex gap-1">
          {urls.map((url) => (
            <img
              key={url}
              src={url}
              alt=""
              className="h-28 min-w-0 flex-1 rounded border border-edge bg-ink object-contain"
            />
          ))}
        </div>
      ) : null}

      {shot ? (
        <p className="text-[10px] leading-snug text-zinc-500" title={shot}>
          {shot}
        </p>
      ) : null}
      {extras.map((line) => (
        <p key={line} className="text-[10px] leading-snug text-zinc-400" title={line}>
          {line}
        </p>
      ))}
    </div>
  );
}
