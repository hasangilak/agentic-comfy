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
  const tones = {
    quiet: "bg-[#26262e] hover:bg-[#32323c] text-zinc-200",
    primary: "bg-[#d99a4e] hover:bg-[#e5a95c] text-[#1a1208] font-medium",
    danger: "bg-[#3b1d1d] hover:bg-[#4d2424] text-red-300 border border-red-900/60",
    ghost: "hover:bg-[#26262e] text-zinc-400",
  };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`rounded px-2 py-1 text-xs transition-colors disabled:cursor-not-allowed
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
    dot: "bg-zinc-600",
    ring: "border-[#26262e]",
    hint: "no action written yet",
  },
  needs_asset: {
    label: "needs a still",
    dot: "bg-[#d99a4e]",
    ring: "border-[#d99a4e]/50",
    hint: "this shot opens on its own image — drop one in, or generate it",
  },
  ready: {
    label: "ready",
    dot: "bg-sky-500",
    ring: "border-sky-900/60",
    hint: "has everything it needs to render",
  },
  rendering: {
    label: "rendering",
    dot: "bg-[#4ade80] live-dot",
    ring: "border-[#4ade80]/60",
    hint: "on the GPU right now",
  },
  rendered: {
    label: "rendered",
    dot: "bg-[#4ade80]",
    ring: "border-[#26262e]",
    hint: "done and paid for",
  },
  stale: {
    label: "edited",
    dot: "bg-[#f59e0b]",
    ring: "border-[#f59e0b]/50",
    hint: "you changed this since it rendered — it will re-render",
  },
  invalidated: {
    label: "follows a change",
    dot: "bg-[#f59e0b]/60",
    ring: "border-[#f59e0b]/30",
    hint: "the beat it continues from changed, so its first frame will differ",
  },
};

export function Badge({ state }: { state: BeatState }) {
  const look = STATE_LOOK[state];
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] text-zinc-400" title={look.hint}>
      <span className={`h-1.5 w-1.5 rounded-full ${look.dot}`} />
      {look.label}
    </span>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] uppercase tracking-wide text-zinc-500">{label}</span>
      {children}
    </label>
  );
}

export const inputClass =
  "w-full resize-none rounded bg-[#0d0d10] border border-[#26262e] px-2 py-1.5 text-xs " +
  "text-zinc-200 outline-none focus:border-[#d99a4e]/60 placeholder:text-zinc-600";
