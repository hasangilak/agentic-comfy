import { useEffect, useState } from "react";
import { BeatModal } from "./canvas/BeatModal";
import { StagingPanel } from "./canvas/StagingPanel";
import { ChatPanel } from "./panels/ChatPanel";
import { Sidebar } from "./panels/Sidebar";
import { Assets } from "./stages/Assets";
import { Script } from "./stages/Script";
import { Start } from "./stages/Start";
import { Storyboard } from "./stages/Storyboard";
import { Studio } from "./stages/Studio";
import { StudioContext, useStudio, useStudioState } from "./useStudio";

/**
 * Three columns: what exists, what you are working on, and what you are saying about it.
 *
 * The window used to carry a fourth band -- a full-width bar of container state and money --
 * across the top. It is gone: those readouts live in the rail, and the controls that spend
 * money float over the board they would spend it on. What is left is one sheet of paper per
 * column.
 *
 * The middle column is one of four stage pages now rather than always the canvas. Stage
 * navigation is in the rail with the readouts, NOT in a bar across the top: that bar was killed
 * for spending a row of the window on state, and the Storyboard grid and the Assets still both
 * want the height back.
 */
export default function App() {
  const studio = useStudioState();
  const [chatOpen, setChatOpen] = useState(true);

  // The Assets stage deliberately has no board conversation: the agent cannot see a picture and
  // `stills.converse` can, so two conversations about one still -- one of them blind -- is worse
  // than one. On the Script stage the transcript IS the page, so a second copy in the rail would
  // be the same conversation twice.
  const stage = studio.resolvedStage;
  const showChat = Boolean(studio.board) && (stage === "storyboard" || stage === "studio");

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
      <div className="flex h-full bg-ink">
        <Sidebar />

        <main className="flex min-w-0 flex-1 flex-col gap-2.5 p-3">
          {!studio.authOk ? <AuthWarning /> : null}
          <ErrorBanner />

          <div className="lift relative min-h-0 flex-1 overflow-hidden rounded-2xl border border-edge bg-panel">
            <StageView />
          </div>

          <LogDrawer />
        </main>

        {showChat ? (
          chatOpen ? (
            <ChatPanel onCollapse={() => setChatOpen(false)} />
          ) : (
            <button
              onClick={() => setChatOpen(true)}
              title="show the story editor"
              className="w-9 shrink-0 border-l border-edge bg-panel text-[13px] text-zinc-400
                transition-colors hover:bg-hover hover:text-zinc-700"
            >
              ⇤
            </button>
          )
        ) : null}
      </div>
      {/* Outside the canvas on purpose: a fixed overlay inside a node would be positioned
          against React Flow's transformed viewport and pan away with it. Both of these are
          opened from nodes and rendered here for that one reason. */}
      <BeatModal />
      <StagingPanel />
    </StudioContext.Provider>
  );
}

/**
 * Which stage page the URL is asking for. No board means the start screen -- there is no
 * boardless dead end any more, because the boardless state IS the first stage.
 */
function StageView() {
  const studio = useStudio();
  if (!studio.board) return <Start />;
  switch (studio.resolvedStage) {
    case "script":
      return <Script />;
    case "storyboard":
      return <Storyboard />;
    case "assets":
      return <Assets />;
    case "studio":
      return <Studio />;
  }
}

function AuthWarning() {
  return (
    <div
      className="shrink-0 rounded-2xl border border-stale/30 bg-stale/5 px-3 py-2
        text-[11px] leading-relaxed text-stale"
    >
      No Modal proxy token set and the deployment is not public, so renders will fail with a 401.
      Mint one at modal.com/settings/proxy-auth-tokens, then put{" "}
      <code>MODAL_PROXY_TOKEN_ID</code> and <code>MODAL_PROXY_TOKEN_SECRET</code> in{" "}
      <code>.env</code> (or export them) and restart studio.py — the file is read at startup, so
      a token added while it is running will not be picked up.
    </div>
  );
}

function ErrorBanner() {
  const { error, setError } = useStudio();
  if (!error) return null;
  return (
    <div className="flex shrink-0 items-start gap-2 rounded-2xl border border-red-200 bg-red-50 px-3 py-2">
      <span className="flex-1 text-[11px] leading-relaxed text-red-600">{error}</span>
      <button onClick={() => setError(null)} className="text-red-600 hover:text-red-700">
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
    <div className="shrink-0 overflow-hidden rounded-2xl border border-edge bg-panel">
      <button
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-hover"
      >
        <span className="text-[10px] text-zinc-400">{open ? "▾" : "▸"} log</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-zinc-400">{last}</span>
      </button>
      {open ? (
        <div className="thin h-40 overflow-y-auto border-t border-edge bg-ink px-3 py-2">
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
