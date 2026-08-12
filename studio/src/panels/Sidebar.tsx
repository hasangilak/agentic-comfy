import { api, clock, money } from "../api";
import { useStudio } from "../useStudio";
import { RailRow } from "../ui";
import { CrewPanel } from "./CrewPanel";
import { StageRail } from "./StageRail";
import { TrashReel } from "./TrashReel";

const CONTAINER_LOOK = {
  cold: { dot: "bg-zinc-300", label: "cold", hint: "no GPU running, nothing billing" },
  deploying: { dot: "bg-warm live-dot", label: "starting", hint: "billing has begun" },
  warm: { dot: "bg-live live-dot", label: "warm", hint: "GPU running and billing" },
  stopping: { dot: "bg-warm", label: "stopping", hint: "tearing the container down" },
};

/**
 * The rail: where the film is up to, what is running, and the one button that makes something
 * new.
 *
 * It replaced a full-width top bar. The bar spent a whole row of the window on state that
 * changes every few minutes -- container, clock, session cost -- and pushed the canvas down;
 * those three now sit here as readouts, and the two controls that are used *while looking at
 * the board* (render, cancel) float over the canvas instead, where the thing they spend money
 * on is.
 *
 * Stage navigation joined them here for the same reason and against the same temptation: it is
 * navigation rather than state, so `RailRow`'s "ours has nothing to navigate to" no longer
 * holds -- but a bar across the top would cost the Storyboard grid and the Assets still the
 * height that makes them readable.
 *
 * The two ways to make a reel used to live here as a 24 rem collapsible. They are on the start
 * screen now, at the size of the decision they are.
 */
export function Sidebar() {
  const studio = useStudio();
  const look = CONTAINER_LOOK[studio.container.state];

  return (
    // `h-full min-h-0` is what lets the middle scroller actually constrain: without them a
    // flex child grows with CrewPanel / the reel list and body (overflow:hidden) just clips it.
    <aside className="flex h-full min-h-0 w-64 shrink-0 flex-col overflow-hidden border-r border-edge bg-panel">
      <div className="shrink-0">
        <div className="flex items-center gap-2.5 px-4 py-4">
          <span
            className="flex h-8 w-8 items-center justify-center rounded-xl bg-solid text-[15px]"
            title="paper-cutout stop motion, one shot at a time"
          >
            🎞
          </span>
          <span className="truncate text-[15px] font-semibold text-zinc-900">Paper Reel</span>
        </div>

        <div className="px-3 pb-1">
          <button
            onClick={() => void studio.go(null)}
            className="flex w-full items-center gap-2 rounded-full bg-solid px-3 py-2.5 text-[13px]
              font-medium text-white transition-colors hover:bg-zinc-800"
          >
            <span className="text-base leading-none">＋</span>
            New reel
          </button>
        </div>
      </div>

      {/* Stages, crew, services and the reel list all share one scroller. Only the reel list
          used to scroll, so a tall CrewPanel pushed the meter and the reels off-screen with
          nowhere to recover them. */}
      <div className="thin min-h-0 flex-1 overflow-y-auto">
        <StageRail />

        {/* Under the stages rather than beside them, because a cast is a property of a stage:
            the rail says which stages are left and this says who works them. It is also where
            a crew run is STARTED, so the thing that spends and the thing that reports are one
            panel -- the same rule the money bar keeps for the render. */}
        <CrewPanel />

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

        <div className="mt-5 px-4 pb-1.5 text-[11px] font-medium text-zinc-400">Recent reels</div>

        <div className="px-2 pb-2">
          {studio.reels.map((reel) => (
            <div
              key={reel.slug}
              className={`mb-0.5 flex w-full items-center gap-1 rounded-xl p-1.5
                ${reel.slug === studio.slug ? "bg-soft" : "hover:bg-hover"}`}
            >
              <button
                onClick={() => void studio.openReel(reel.slug)}
                className="flex min-w-0 flex-1 items-center gap-2.5 text-left transition-colors"
              >
                {reel.thumb ? (
                  <img src={reel.thumb} alt="" className="h-9 w-9 rounded-lg object-cover" />
                ) : (
                  <div className="h-9 w-9 rounded-lg bg-softer" />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[12px] text-zinc-800">{reel.title}</span>
                  <span className="block text-[10px] text-zinc-400">
                    {/* A reel with no beats is one that was started and not written yet — a real
                        state now that a conversation can create the board before the script. */}
                    {reel.beats ? `${reel.beats} beats` : "draft"} · {money(reel.spent)}
                  </span>
                </span>
              </button>
              <TrashReel slug={reel.slug} className="shrink-0 px-1 py-0.5" />
            </div>
          ))}
          {!studio.reels.length ? (
            <p className="px-2 py-1 text-[11px] leading-relaxed text-zinc-400">
              No reels yet. ＋ New reel is the way in.
            </p>
          ) : null}
        </div>
      </div>

      {/* The session's spend, and the one control that ends it. Bottom-left is where the
          reference puts the account; here the account is the meter. */}
      <div className="flex shrink-0 items-center gap-2 border-t border-edge px-3 py-3">
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
