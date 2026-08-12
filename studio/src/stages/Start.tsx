import { useState } from "react";
import { money } from "../api";
import { useStudio } from "../useStudio";
import { TrashReel } from "../panels/TrashReel";
import { PasteAScript, TalkItThrough, WriteItForMe } from "./NewReel";

/**
 * The page with no reel open.
 *
 * It replaced an empty state inside the canvas card that said "pick a reel, or start one on the
 * left" — an instruction to look somewhere else, which is what an empty state should never be.
 * Starting a film is the most important thing this app does and it now happens at page size.
 *
 * Recent reels are here as thumbnails rather than only in the rail: on a cold start they are
 * the most likely destination, and a 36 px thumbnail in a 256 px column is not how you
 * recognise a film you made last week.
 */
export function Start() {
  const studio = useStudio();
  // Three ways to do one job, at equal billing. They used to be two, behind a segmented toggle
  // in a 24 rem rail; "talk it through" is the default because the four questions it asks are
  // the four decisions that shape the film, and the other two both answer them for you.
  const [mode, setMode] = useState<"talk" | "write" | "paste">("talk");

  return (
    <div className="thin h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-8 py-12">
        <p className="text-3xl">🎞</p>
        <h1 className="mt-3 text-2xl font-semibold text-zinc-900">Start a reel</h1>
        <p className="mt-1.5 max-w-lg text-[13px] leading-relaxed text-zinc-500">
          Forty seconds of paper-cutout stop motion, made in four stages: the script, the
          storyboard, the still each shot opens on, then the render. Only the last one costs
          money, and the price is always on the button before you press it.
        </p>

        <div className="mt-8 rounded-2xl border border-edge bg-panel p-5">
          <div className="mb-4 flex gap-1 rounded-full bg-ink p-0.5 text-[11px]">
            {(
              [
                ["talk", "talk it through"],
                ["write", "write it for me"],
                ["paste", "paste a script"],
              ] as const
            ).map(([option, label]) => (
              <button
                key={option}
                onClick={() => setMode(option)}
                className={`flex-1 rounded-full px-3 py-1.5 transition-colors ${
                  mode === option ? "bg-solid text-white" : "text-zinc-500 hover:text-zinc-800"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {mode === "talk" ? (
            <TalkItThrough />
          ) : mode === "write" ? (
            <WriteItForMe />
          ) : (
            <PasteAScript />
          )}
        </div>

        <div className="mt-10">
          <div className="mb-2.5 text-[11px] font-medium text-zinc-400">Recent reels</div>
          {studio.reels.length ? (
            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3">
              {studio.reels.map((reel) => (
                <div
                  key={reel.slug}
                  className="lift relative overflow-hidden rounded-2xl border border-edge bg-panel
                    transition-colors hover:bg-hover"
                >
                  <TrashReel
                    slug={reel.slug}
                    className="absolute right-2 top-2 z-10 rounded-full bg-panel/90 px-1.5 py-0.5
                      shadow-sm"
                  />
                  <button
                    onClick={() => void studio.openReel(reel.slug)}
                    className="w-full text-left"
                  >
                    {reel.thumb ? (
                      <img src={reel.thumb} alt="" className="h-32 w-full object-cover" />
                    ) : (
                      <div className="flex h-32 w-full items-center justify-center bg-ink text-xl text-zinc-300">
                        🎞
                      </div>
                    )}
                    <div className="px-3 py-2">
                      <div className="truncate text-[12px] text-zinc-800">{reel.title}</div>
                      <div className="text-[10px] text-zinc-400">
                        {reel.beats ? `${reel.beats} beats` : "draft"} · {money(reel.spent)}
                      </div>
                    </div>
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-[12px] leading-relaxed text-zinc-400">
              Nothing yet. Whichever way you start above, the reel exists before anything is
              rendered — you can leave it half-written and come back to it.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
