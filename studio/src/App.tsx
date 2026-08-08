import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useState } from "react";
import { BeatModal } from "./canvas/BeatModal";
import { Canvas } from "./canvas/Canvas";
import { StoryPanel } from "./panels/StoryPanel";
import { ReelRail } from "./panels/ReelRail";
import { TopBar } from "./panels/TopBar";
import { StudioContext, useStudio, useStudioState } from "./useStudio";

export default function App() {
  const studio = useStudioState();

  // A file dropped anywhere but a node would otherwise make the browser navigate to it,
  // throwing away the whole session. Nodes stop propagation by handling their own drop.
  useEffect(() => {
    const swallow = (event: DragEvent) => event.preventDefault();
    window.addEventListener("dragover", swallow);
    window.addEventListener("drop", swallow);
    return () => {
      window.removeEventListener("dragover", swallow);
      window.removeEventListener("drop", swallow);
    };
  }, []);

  return (
    <StudioContext.Provider value={studio}>
      <div className="flex h-full flex-col">
        <TopBar />
        {!studio.authOk ? <AuthWarning /> : null}
        <ErrorBanner />

        <div className="flex min-h-0 flex-1">
          <ReelRail />
          <div className="flex min-w-0 flex-1 flex-col">
            {studio.board ? (
              <ReactFlowProvider>
                <div className="min-h-0 flex-1">
                  <Canvas />
                </div>
              </ReactFlowProvider>
            ) : (
              <Empty />
            )}
            <LogDrawer />
          </div>
          {studio.board ? <StoryPanel /> : null}
        </div>
      </div>
      {/* Outside the canvas on purpose: a fixed overlay inside a node would be positioned
          against React Flow's transformed viewport and pan away with it. */}
      <BeatModal />
    </StudioContext.Provider>
  );
}

function Empty() {
  return (
    <div className="flex flex-1 items-center justify-center">
      <div className="max-w-sm text-center">
        <p className="mb-2 text-2xl">🎞</p>
        <p className="text-sm text-zinc-400">Pick a reel, or start one with + new.</p>
        <p className="mt-2 text-[11px] leading-relaxed text-zinc-600">
          Writing the script and the stills is free. Only rendering costs money, and the price
          is always on the button before you press it.
        </p>
      </div>
    </div>
  );
}

function AuthWarning() {
  return (
    <div
      className="shrink-0 border-b border-[#f59e0b]/30 bg-[#f59e0b]/10 px-3 py-1.5
        text-[11px] leading-relaxed text-[#f59e0b]"
    >
      No Modal proxy token set and the deployment is not public, so renders will fail with a
      401. Mint one at modal.com/settings/proxy-auth-tokens, then put{" "}
      <code>MODAL_PROXY_TOKEN_ID</code> and <code>MODAL_PROXY_TOKEN_SECRET</code> in{" "}
      <code>.env</code> (or export them) and restart studio.py — the file is read at startup,
      so a token added while it is running will not be picked up.
    </div>
  );
}

function ErrorBanner() {
  const { error, setError } = useStudio();
  if (!error) return null;
  return (
    <div className="flex shrink-0 items-start gap-2 border-b border-red-900/50 bg-red-950/40 px-3 py-1.5">
      <span className="flex-1 text-[11px] leading-relaxed text-red-300">{error}</span>
      <button onClick={() => setError(null)} className="text-red-400 hover:text-red-200">
        ×
      </button>
    </div>
  );
}

/** The raw log. Collapsed by default; the people who want it know they want it. */
function LogDrawer() {
  const { log, activeJob } = useStudio();
  const [open, setOpen] = useState(false);
  const lines = activeJob?.log.length ? activeJob.log : log;
  const last = lines[lines.length - 1] ?? "";

  return (
    <div className="shrink-0 border-t border-[#26262e] bg-[#16161b]">
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-[#1f1f26]"
      >
        <span className="text-[10px] text-zinc-500">{open ? "▾" : "▸"} log</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-zinc-500">{last}</span>
      </button>
      {open ? (
        <div className="thin h-40 overflow-y-auto border-t border-[#26262e] px-3 py-2">
          {lines.map((line, index) => (
            <div key={index} className="font-mono text-[10px] leading-relaxed text-zinc-500">
              {line}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
