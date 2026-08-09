import type { Job } from "../types";
import { useStudio } from "../useStudio";

/**
 * What is running, in one line.
 *
 * A render's stages differ in length by an order of magnitude, so one bar across all of them
 * would misrepresent progress; this shows which stage, and what already succeeded. Everything
 * else is a single label, because everything else is one call.
 *
 * Lifted out of `CanvasToolbar` when the studio became four stages: the money bar is still the
 * only place a render is *started*, but every stage now has work of its own to report, and a
 * per-stage copy of this would drift into four vocabularies for one queue.
 */
const LABELS: Record<string, string> = {
  develop: "talking it through",
  plan: "writing the script",
  chat: "the model is thinking",
  revise: "rewriting the line",
  caption: "writing the caption",
  panel_write: "writing the shots",
  panel_draw: "drawing a panel",
  stage_draw: "drawing a design sheet",
  stage_chat: "looking at the design",
  asset: "generating a still",
  still_chat: "looking at the still",
  ref_draw: "drawing a picture",
  ref_chat: "looking at the picture",
};

const RENDER_STAGES = ["deploying", "booting", "rendering", "stitching"];

export function JobStrip({ job }: { job: Job }) {
  if (job.kind !== "render") {
    const step = job.step_max ? ` ${job.step}/${job.step_max}` : "";
    const beat = job.beat ? ` · scene ${job.beat}` : "";
    return (
      <span className="px-1 text-[11px] text-warm">
        {LABELS[job.kind] ?? job.phase}
        {beat}
        {step}…
      </span>
    );
  }
  const at = RENDER_STAGES.indexOf(job.phase);
  return (
    <div className="flex items-center gap-1.5 px-1 text-[10px]">
      {RENDER_STAGES.map((stage, index) => (
        <span
          key={stage}
          className={index < at ? "text-live" : index === at ? "text-warm" : "text-zinc-300"}
        >
          {index < at ? "✓" : index === at ? "◐" : "○"} {stage}
        </span>
      ))}
      {job.beat_total ? (
        <span className="text-zinc-500">
          beat {job.beat_index}/{job.beat_total}
          {job.step_max ? ` · step ${job.step}/${job.step_max}` : ""}
        </span>
      ) : null}
    </div>
  );
}

/**
 * The same strip, as a stage page's header slot: nothing at all when this board has nothing
 * running, so a quiet page stays quiet.
 */
export function ActiveJob({ kinds }: { kinds?: string[] }) {
  const studio = useStudio();
  const job = studio.activeJob;
  if (!job || job.slug !== studio.board?.slug) return null;
  if (kinds && !kinds.includes(job.kind)) return null;
  return <JobStrip job={job} />;
}
