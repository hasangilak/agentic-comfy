// Mirrors what paperreel/board.py and paperreel/jobs.py serialise.

/**
 * Where a beat's frames come from -- the meaning of the wire on the canvas.
 *
 *   reference -- the default cut, on a different checkpoint: its own still as <Picture 1>
 *                and the reel's cast reference as <Picture 2>, with room for `max_refs` in
 *                total. No keyframe, so it cannot continue from anything -- but the cast is
 *                re-asserted through every sampling step instead of only at frame zero.
 *   chain     -- the previous clip's last frame as the first frame: a continuation
 *   bridge    -- both, so the clip continues AND lands on a still of its own
 *   asset     -- its own still as an exact first keyframe, and nothing else. The cut for a
 *                beat whose opening frame has to land precisely.
 */
export type Source = "reference" | "chain" | "bridge" | "asset";

export const GEMINI_IMAGE_MODELS = [
  { id: "gemini-3-pro-image", label: "Nano Banana Pro", blurb: "Highest quality and creative control." },
  { id: "gemini-3.1-flash-image", label: "Nano Banana 2", blurb: "Best balance of quality, speed, and references." },
  { id: "gemini-3.1-flash-lite-image", label: "Nano Banana 2 Lite", blurb: "Fastest and cheapest; 1K output only." },
] as const;
export type GeminiImageModel = (typeof GEMINI_IMAGE_MODELS)[number]["id"];
export const GEMINI_IMAGE_SIZES = ["1K", "2K", "4K"] as const;
export type GeminiImageSize = (typeof GEMINI_IMAGE_SIZES)[number];
export const DEFAULT_GEMINI_IMAGE_MODEL: GeminiImageModel = "gemini-3-pro-image";
export const DEFAULT_GEMINI_IMAGE_SIZE: GeminiImageSize = "2K";

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
  gemini_model?: GeminiImageModel;
  gemini_image_size?: GeminiImageSize;
  seconds: number;
  /** Snapped onto the model's frame grid -- the truth, which `seconds` only asks for. */
  frames: number;
  actual_seconds: number;
  source: Source;
  state: BeatState;
  asset: string | null;
  /** Width/height of the uploaded or generated still, for the crop warning. */
  asset_aspect: number | null;
  /** The conversation about this still — the director's notes and the review's verdicts. */
  asset_chat: AssetTurn[];
  /**
   * The shot grammar this scene's storyboard panel is drawn from — shot size, angle, camera move,
   * where the subject sits. Written by the local model in one pass over the whole reel, then
   * hand-editable.
   *
   * It reaches no renderer, so editing it marks nothing stale. That is the whole difference
   * between a panel and everything else on this beat that holds a prompt.
   */
  panel: string;
  /** The panel itself: a rough grey sketch of the shot, drawn on the cheapest model. */
  panel_url: string | null;
  /** The frame this beat actually opened on. A chained beat has no still of its own. */
  frame: string | null;
  /** And, on a bridge, the frame it was told to arrive at. Null on every other join. */
  end_frame: string | null;
  /**
   * The reference pictures the DIRECTOR added — the ones that can be removed and described.
   * Not the whole conditioning set: `auto_refs` come first in the prompt's numbering, so
   * refs[0] is <Picture ref_offset + 1>.
   */
  refs: string[];
  /**
   * What each picture is FOR, same order and same length as `refs`; "" where nothing has
   * been said. It goes into the render, so editing one marks the beat stale.
   */
  ref_prompts: string[];
  /**
   * What each picture was last DRAWN from — the Gemini prompt, the analogue of `asset_prompt`
   * for a still. "" on one that was uploaded rather than drawn. Same order, same length.
   *
   * A different field from `ref_prompts` on purpose: that says what the picture is FOR and
   * reaches both renderers, this says what to draw and reaches neither. "A close-up of an
   * iron-grey club on flat black" is a good draw prompt and a terrible end to the sentence
   * "<Picture 3> is …".
   */
  ref_draws: string[];
  /** The conversation about each picture, one transcript per picture. Same order and length. */
  ref_chats: AssetTurn[][];
  /**
   * A stable handle per picture. The position is not one: removing a picture renumbers every
   * file after it, so `refs[2]` addresses a different image afterwards. Selection keys off
   * these, and so does an @-mention.
   */
  ref_ids: string[];
  /**
   * The slots that filled themselves: this beat's own still as the composition it opens on,
   * and the reel's cast reference. Read-only — they follow the still and the reference rather
   * than being editable here. Empty on every join but a reference cut.
   */
  auto_refs: { url: string | null; note: string }[];
  /** How far those push the director's pictures down the numbering. `auto_refs.length`. */
  ref_offset: number;
  /** Uploads this beat can still take: `max_refs` less the automatic ones. */
  ref_slots: number;
  /**
   * How many of `refs` also condition the STILL, counted from the first. The still renderer
   * takes far fewer pictures than the video model — `max_still_refs` including the reel's cast
   * reference — so this is usually smaller than `refs.length`.
   */
  still_refs: number;
  /**
   * Which of the reel's designs this scene contains, in the order they are numbered. Ids into
   * `Board.staging`, not copies: the designs themselves are reel-level, and a second copy per
   * beat is a second thing to keep in step.
   */
  staging: string[];
  /**
   * How many of those actually reach the clip, and the still, as PICTURES. They sit between
   * `auto_refs` and `refs` in the numbering, which is why `ref_offset` counts them.
   *
   * The two differ on purpose: the still renderer takes four pictures where the video model
   * takes nine, so a set sheet is dropped from the still and reaches it as prose instead. A
   * node that showed the binding without showing that would be claiming something untrue.
   */
  staging_refs: number;
  staging_still_refs: number;
  /**
   * What the designs this render was NOT handed as pictures say instead, as the model gets it.
   *
   * Two, because the two renders answer differently and that difference is the whole design: the
   * clip has nine picture slots and usually needs no prose at all, while the still has four and
   * hands the sets over as words.
   */
  staging_text: string;
  staging_still_text: string;
  /** Whether this beat's still is wired as the composition it opens on. */
  opens_on: boolean;
  /**
   * Reference scenes only: the tail of the previous clip goes in as a reference VIDEO. It is
   * how this join gets continuity at all — ref2va has no keyframe slot to hand a frame to —
   * and it makes the scene depend on the one before it again, so upstream re-renders
   * invalidate it.
   */
  carry: boolean;
  /** The cut tail actually sent, once it has been. */
  carry_clip: string | null;
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

/**
 * One line of a single still's conversation. Both writers land here: the director asking for a
 * change, and the automatic review saying what it made of a render nobody had looked at yet.
 */
export interface AssetTurn {
  /** "qwen" is only ever read: boards written before the studio moved to Gemini carry it. */
  role: "user" | "gemini" | "qwen";
  text: string;
  /** The rewritten `asset_prompt`, on a turn that changed it. */
  prompt?: string;
  /** Whether that turn ended in the still being drawn again. */
  regenerated?: boolean;
  /**
   * What the automatic review made of the still, on a turn the reviewer wrote. Absent on a
   * director's turn, and absent on every turn of a board written before `stills.remember`
   * started stamping it — which reads as "not reviewed", the truth about those.
   */
  verdict?: "pass" | "kept" | "rewritten";
  /** Why it was not, when it should have been — the image server being down, usually. */
  error?: string;
}

/**
 * What kind of thing a design is. It is not decoration — it decides what the sheet is drawn as,
 * what shape it is drawn at, and whether it reaches the still renderer as an image or as words.
 * An environment is the one that differs on all three.
 */
export type StageKind = "character" | "environment" | "prop";

/**
 * One entry in the reel's design bible: named, written down, drawn once, bound to the scenes
 * that contain it.
 *
 * The layer between the style bible (one paragraph, reel-wide, words only) and a beat's own
 * reference pictures (images, one beat). Reel-scoped is the whole difference: a picture uploaded
 * to scene 3 cannot be used by scene 7 without being uploaded again.
 */
export interface StageEntry {
  id: string;
  kind: StageKind;
  /** What the prompts call it. Always leads `role`, so the model can tie it to the action line. */
  name: string;
  /** What it IS, in the director's words. Reaches both renderers — editing it marks beats stale. */
  note: string;
  /** What Gemini is asked for when the sheet is drawn. Reaches neither renderer. */
  draw: string;
  chat: AssetTurn[];
  /** The design sheet itself. Null until it has been drawn or uploaded. */
  sheet: string | null;
  /** The sentence the prompts are actually told, name included. `name` + `note`, derived. */
  role: string;
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
  /** The reel's cast and sets, designed once and bound to the scenes that use them. */
  staging: StageEntry[];
  stage_kinds: StageKind[];
  max_staging: number;
  beats: Beat[];
  /**
   * Every drawn panel on one numbered contact sheet — the whole film read at once, which is what a
   * storyboard is. Null until one has been built. Rebuilt from scratch whenever a panel changes.
   */
  panel_sheet: string | null;
  canvas: { nodes?: Record<string, { x: number; y: number }> };
  reel: string | null;
  /** What "render everything that needs it" would cover, chain cascade included. */
  pending: number[];
  pending_cost: Estimate;
  draft_cost: Estimate;
  spent: number;
  /** Every beat short of the still it renders from, whichever join it is on. */
  assets_needed: number[];
  /**
   * What is thin about this script — a missing style bible, a cut with no prompt. Advice, not
   * errors: every one of them is fixable for free. Derived on every read, so it clears itself
   * as the gaps are filled rather than being a message that was true once.
   */
  notes: string[];
  /** The model's hard cap on pictures per beat: nine. Per-beat `ref_slots` is what a node shows. */
  max_refs: number;
  /**
   * And the still renderer's cap, cast reference included — a beat's first `still_refs` pictures
   * are drawn into its still as well as into its clip. The image server may report a lower one.
   */
  max_still_refs: number;
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
  kind:
    | "plan"
    /** One turn of the interview that becomes a script. The board exists from turn one. */
    | "develop"
    | "chat"
    | "asset"
    | "still_chat"
    | "revise"
    /** Draw one reference picture. `detail.index` is null when it is a new one. */
    | "ref_draw"
    /** One turn about one reference picture, its redraw included. */
    | "ref_chat"
    /** Draw one of the reel's design sheets. `detail.id` names it. */
    | "stage_draw"
    /** One turn about one design sheet, its redraw included. */
    | "stage_chat"
    /** Write the shot grammar for the whole reel. Local model, no image, free. */
    | "panel_write"
    /** Draw storyboard panels and rebuild the sheet. `detail.beats` is null for "all of them". */
    | "panel_draw"
    | "caption"
    | "render";
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
  /**
   * "studio" is the board speaking for itself -- e.g. what an imported script arrived as.
   * "agy" and "qwen" are only ever read, never written: they are what the model was called
   * before this studio moved to Gemini, and boards written back then still carry them in
   * their transcript.
   */
  role: "user" | "gemini" | "qwen" | "agy" | "studio";
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
