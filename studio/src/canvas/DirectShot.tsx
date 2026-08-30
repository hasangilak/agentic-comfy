import type { Beat, Job } from "../types";
import { useBusy, useStudio } from "../useStudio";
import { api } from "../api";

/**
 * One-click rewrite of a beat's action so MiniMax-H3 can shoot it.
 *
 * `revise` is "do what the director said about this line". This has no note: the system
 * prompt *is* the instruction (playback order, one gesture that fits the duration, a named
 * ending pose, no camera in the line). The six-part wrapper stays `build_prompt`'s.
 *
 * Shared by the expanded scene and the canvas node so the two buttons cannot drift. The
 * reply is shown from the job rather than from the transcript — same reason `ReviseField`
 * does: the chat is behind this view, and a rewrite you cannot see the reasoning for reads
 * as the studio changing your words on its own.
 */

export function canDirect(beat: Beat): boolean {
  return Boolean(
    beat.action?.trim() || beat.scene?.trim() || beat.blocking?.trim() || beat.panel?.trim(),
  );
}

function useDirectShot(beat: Beat) {
  const studio = useStudio();
  const board = studio.board!;
  const busy = useBusy("direct", (detail) => detail.beat === beat.n);
  const answered = Object.values(studio.jobs)
    .filter(
      (job: Job) =>
        job.kind === "direct" &&
        job.slug === board.slug &&
        job.detail.beat === beat.n &&
        job.state === "done",
    )
    .sort((a, b) => (a.finished_at ?? 0) - (b.finished_at ?? 0))
    .pop();
  const reply = (answered?.result as { reply?: string } | null)?.reply ?? "";

  const send = (flush?: () => void) => {
    if (busy || !canDirect(beat)) return;
    flush?.();
    void studio.guard(() => api.directBeat(board.slug, beat.n));
  };

  return { busy, reply, send, ready: studio.model.ready };
}

export function DirectButton({
  beat,
  flush,
  className = "",
}: {
  beat: Beat;
  flush?: () => void;
  className?: string;
}) {
  const { busy, send, ready } = useDirectShot(beat);
  const allowed = canDirect(beat);
  return (
    <button
      type="button"
      onClick={() => send(flush)}
      disabled={busy || !allowed}
      className={`text-[10px] text-zinc-500 hover:text-warm disabled:cursor-not-allowed
        disabled:opacity-40 ${className}`}
      title={
        !allowed
          ? "write what moves first, or a scene / panel the shot can be directed from"
          : !ready
            ? "the model is not running — nothing to direct with"
            : "rewrite the action so MiniMax-H3 can shoot it: visible moves in order, one " +
              "thing at a time, a clear ending. Free. Marks the scene for re-rendering"
      }
    >
      {busy ? "directing…" : "Direct this shot"}
    </button>
  );
}

export function DirectReply({ beat }: { beat: Beat }) {
  const { reply } = useDirectShot(beat);
  if (!reply) return null;
  return <p className="text-[10px] leading-snug text-zinc-500">{reply}</p>;
}
