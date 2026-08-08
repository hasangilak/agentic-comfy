import type { ReactNode } from "react";
import type { BeatState } from "./types";

export function Button({
  children,
  onClick,
  tone = "quiet",
  disabled,
  title,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: "quiet" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  title?: string;
  className?: string;
}) {
  // The primary action is black, not amber. On a white ground the warm accent is a *state*
  // colour -- a cut, a missing still -- and spending it on the button as well made "render",
  // "generate" and "this needs a still" one colour, which is the distinction the money bar
  // exists to make.
  const tones = {
    quiet: "bg-soft hover:bg-softer text-zinc-700",
    primary: "bg-solid hover:bg-zinc-800 text-white font-medium",
    danger: "bg-panel hover:bg-red-50 text-red-600 border border-red-200",
    ghost: "hover:bg-soft text-zinc-500 hover:text-zinc-700",
  };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`rounded-full px-3 py-1.5 text-xs transition-colors disabled:cursor-not-allowed
        disabled:opacity-40 ${tones[tone]} ${className}`}
    >
      {children}
    </button>
  );
}

/** One visual language for beat state, shared by nodes and wires. */
export const STATE_LOOK: Record<
  BeatState,
  { label: string; dot: string; ring: string; hint: string }
> = {
  planned: {
    label: "planned",
    dot: "bg-zinc-300",
    ring: "border-edge",
    hint: "no action written yet",
  },
  needs_asset: {
    label: "needs a still",
    dot: "bg-warm",
    ring: "border-warm/40",
    hint: "this shot opens on its own image — drop one in, or generate it",
  },
  ready: {
    label: "ready",
    dot: "bg-sky-500",
    ring: "border-sky-200",
    hint: "has everything it needs to render",
  },
  rendering: {
    label: "rendering",
    dot: "bg-live live-dot",
    ring: "border-live/50",
    hint: "on the GPU right now",
  },
  rendered: {
    label: "rendered",
    dot: "bg-live",
    ring: "border-edge",
    hint: "done and paid for",
  },
  stale: {
    label: "edited",
    dot: "bg-stale",
    ring: "border-stale/40",
    hint: "you changed this since it rendered — it will re-render",
  },
  invalidated: {
    label: "follows a change",
    dot: "bg-stale/60",
    ring: "border-stale/25",
    hint: "the beat it continues from changed, so its first frame will differ",
  },
};

export function Badge({ state }: { state: BeatState }) {
  const look = STATE_LOOK[state];
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] text-zinc-500" title={look.hint}>
      <span className={`h-1.5 w-1.5 rounded-full ${look.dot}`} />
      {look.label}
    </span>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] uppercase tracking-wide text-zinc-400">{label}</span>
      {children}
    </label>
  );
}

export const inputClass =
  "w-full resize-none rounded-xl bg-ink border border-edge px-2.5 py-2 text-xs " +
  "text-zinc-800 outline-none focus:border-zinc-400 focus:bg-panel placeholder:text-zinc-400 " +
  "transition-colors";

/**
 * A row that looks like navigation and is actually a readout. The reference design's left rail
 * is a stack of icon rows; ours has nothing to navigate to -- one board is open at a time -- so
 * the same shape carries the three things that can silently not be running.
 */
export function RailRow({
  icon,
  label,
  value,
  tone = "quiet",
  title,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  value?: ReactNode;
  tone?: "quiet" | "warn";
  title?: string;
  onClick?: () => void;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      onClick={onClick}
      title={title}
      className={`flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left transition-colors
        ${onClick ? "hover:bg-hover" : ""}`}
    >
      <span className="w-4 shrink-0 text-center text-[13px] text-zinc-400">{icon}</span>
      <span className="min-w-0 flex-1 truncate text-[13px] text-zinc-700">{label}</span>
      {value === undefined ? null : (
        <span
          className={`shrink-0 text-[11px] ${tone === "warn" ? "text-warm" : "text-zinc-400"}`}
        >
          {value}
        </span>
      )}
    </Tag>
  );
}
