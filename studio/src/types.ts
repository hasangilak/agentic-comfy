// Mirrors what paperreel/board.py and paperreel/jobs.py serialise.

/**
 * Where a beat's frames come from -- the meaning of the wire on the canvas. The model takes
 * a first and a last keyframe, so there are three joins, not two:
 *
 *   asset  -- its own still as the first frame: a cut
 *   chain  -- the previous clip's last frame as the first frame: a continuation
 *   bridge -- both, so the clip continues AND lands on a still of its own
 */
export type Source = "asset" | "chain" | "bridge";

export type BeatState =
  | "planned" // no action written yet
  | "needs_asset" // wants its own still, hasn't got one
  | "ready" // has everything it needs
  | "rendering"
  | "rendered"
  | "stale" // rendered, but its own prompt/length/seed changed since
  | "invalidated"; // rendered, but the beat it continues from changed

export interface Beat {
  n: number;
  scene: string;
  action: string;
  asset_prompt: string;
  seconds: number;
  /** Snapped onto the model's frame grid -- the truth, which `seconds` only asks for. */
  frames: number;
  actual_seconds: number;
  source: Source;
  state: BeatState;
  asset: string | null;
  /** Width/height of the uploaded or generated still, for the crop warning. */
  asset_aspect: number | null;
  /** The frame this beat actually opened on. A chained beat has no still of its own. */
  frame: string | null;
  /** And, on a bridge, the frame it was told to arrive at. Null on every other join. */
  end_frame: string | null;
  video: string | null;
  predicted_seconds: number;
  predicted_cost: number;
  render: {
    at: string;
    render_seconds: number;
    cost: number;
    seed: number;
    frames: number;
  } | null;
}

export interface Estimate {
  predicted_seconds: number;
  predicted_cost: number;
  video_seconds: number;
  beats?: number[];
  frames?: number[];
}

export interface Board {
  slug: string;
  title: string;
  style_bible: string;
  caption: string;
  seconds: number;
  steps: number;
  seed: number;
  mute: boolean;
  /** The only lengths a beat may have. One button per entry on the node. */
  lengths: number[];
  /** Aspect the model renders at; stills far from this get cropped. */
  gen_aspect: number;
  /** The stills are the user's own work: no generate affordance, and the API refuses to run one. */
  manual_stills: boolean;
  /** The still every cut's image is generated from, so the cast survives a scene change. */
  reference: string | null;
  /** False when it is only beat 1's own still standing in. */
  reference_explicit: boolean;
  beats: Beat[];
  canvas: { nodes?: Record<string, { x: number; y: number }> };
  reel: string | null;
  /** What "render everything that needs it" would cover, chain cascade included. */
  pending: number[];
  pending_cost: Estimate;
  draft_cost: Estimate;
  spent: number;
  assets_needed: number[];
}

export interface ReelSummary {
  slug: string;
  title: string;
  beats: number;
  spent: number;
  thumb: string | null;
  reel: string | null;
}

export interface Job {
  id: string;
  kind: "plan" | "chat" | "asset" | "caption" | "render";
  slug: string;
  detail: Record<string, unknown>;
  state: "queued" | "running" | "done" | "error" | "cancelled";
  error: string | null;
  result: Record<string, unknown> | null;
  phase: string;
  beat: number | null;
  beat_index: number;
  beat_total: number;
  step: number;
  step_max: number;
  beat_started_at: number | null;
  started_at: number | null;
  finished_at: number | null;
  cancelling: boolean;
  log: string[];
}

export interface Container {
  state: "cold" | "deploying" | "warm" | "stopping";
  live_seconds: number;
  session_seconds: number;
  session_cost: number;
}

export interface ChatTurn {
  /** "studio" is the board speaking for itself -- e.g. what an imported script arrived as. */
  role: "user" | "agy" | "studio";
  text: string;
  selection?: number[];
  ops?: { op: string; n?: number; summary: string }[];
}

export type StudioEvent =
  | { type: "hello"; container: Container; jobs: Job[] }
  | { type: "tick"; container: Container }
  | { type: "container"; container: Container }
  | { type: "job"; job: Job }
  | { type: "log"; job_id: string | null; line: string }
  | { type: "board"; slug: string }
  | { type: "progress"; job_id: string; beat: number | null; step: number; step_max: number };
