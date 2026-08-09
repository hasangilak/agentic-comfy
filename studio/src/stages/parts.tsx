import type { ReactNode } from "react";
import { ActiveJob } from "../panels/JobStrip";
import type { Stage } from "../route";
import { STAGE_JOBS } from "../route";

/**
 * The shared furniture of a stage page.
 *
 * Every stage answers the same two questions in the same place: what is this stage, and what is
 * it waiting on. The second one is what replaces a gated rail — no stage is ever locked, so the
 * studio has to say what is missing instead of refusing to open.
 */

export function StagePage({
  stage,
  title,
  blurb,
  waiting,
  children,
}: {
  stage: Stage;
  title: string;
  blurb: string;
  waiting?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="thin flex h-full flex-col overflow-y-auto">
      <div className="sticky top-0 z-10 shrink-0 border-b border-edge bg-panel/95 px-6 py-3.5 backdrop-blur">
        <div className="flex items-baseline gap-3">
          <h1 className="text-[15px] font-semibold text-zinc-900">{title}</h1>
          <span className="min-w-0 flex-1 truncate text-[11px] text-zinc-400">{blurb}</span>
          <ActiveJob kinds={STAGE_JOBS[stage]} />
        </div>
        {waiting ? <div className="mt-2.5">{waiting}</div> : null}
      </div>
      <div className="min-h-0 flex-1 px-6 py-5">{children}</div>
    </div>
  );
}

/**
 * One sentence about what the stage is short of, and the button that fixes it. The reference
 * design's "needs approval to run" strip, doing the job it does there — and a nudge rather than
 * a gate, because everything on the first three stages is free or cents.
 */
export function WaitingOn({
  children,
  action,
  tone = "warn",
}: {
  children: ReactNode;
  action?: ReactNode;
  tone?: "warn" | "quiet";
}) {
  return (
    <div
      className={`flex items-center gap-3 rounded-2xl border px-3.5 py-2.5 ${
        tone === "warn" ? "border-warm/30 bg-warm/5" : "border-edge bg-ink"
      }`}
    >
      <span
        className={`min-w-0 flex-1 text-[11px] leading-relaxed ${
          tone === "warn" ? "text-warm" : "text-zinc-500"
        }`}
      >
        {children}
      </span>
      {action}
    </div>
  );
}
