import type { Board } from "./types";

/**
 * Where the browser is, and nowhere else.
 *
 * Every read of `window.location` and every `pushState` in this app goes through this file.
 * That is the point of it: the studio has no router dependency -- the route space is one path
 * param, one stage segment and one search key, against a package.json whose whole virtue is
 * that it has three dependencies -- but a hand-rolled parser scattered across the store is how
 * a second authority on the URL gets written by accident. Swapping in a real router later is
 * this file plus its call sites.
 */

/**
 * The four stages of making a reel, in the order they are made.
 *
 * They are stages of the WORK, not of the document: `storyboard.json` is one file whatever
 * stage you are looking at, and every stage is reachable at any time. See `resolveStage`.
 */
export type Stage = "script" | "storyboard" | "assets" | "studio";

export const STAGES: { id: Stage; label: string; glyph: string; blurb: string }[] = [
  {
    id: "script",
    label: "Script",
    glyph: "✎",
    blurb: "talk the film into existence, or paste one you already have",
  },
  {
    id: "storyboard",
    label: "Storyboard",
    glyph: "▦",
    blurb: "named cast, then how each shot is framed, then the sheets",
  },
  {
    id: "assets",
    label: "Assets",
    glyph: "🖼",
    blurb: "the still every shot opens on, and what each one is drawn from",
  },
  {
    id: "studio",
    label: "Studio",
    glyph: "🎬",
    blurb: "the chain, the price, and the render",
  },
];

const STAGE_IDS = STAGES.map((stage) => stage.id);

export type Route =
  | { at: "start" }
  | { at: "reel"; slug: string; stage: Stage | null; shot: number | null };

const isStage = (value: string): value is Stage => (STAGE_IDS as string[]).includes(value);

/** `/reels/<slug>` and `/reels/<slug>/<stage>`; anything else is the start screen. */
export function parseRoute(location: Location): Route {
  const match = location.pathname.match(/^\/reels\/([^/]+)(?:\/([^/]+))?\/?$/);
  if (!match) return { at: "start" };
  let slug: string;
  try {
    slug = decodeURIComponent(match[1]);
  } catch {
    return { at: "start" };
  }
  const segment = match[2];
  const shot = Number(new URLSearchParams(location.search).get("shot"));
  return {
    at: "reel",
    slug,
    // An unknown segment reads as "no stage given" rather than as a 404: the board is still
    // the thing being addressed, and `resolveStage` has an answer for that.
    stage: segment && isStage(segment) ? segment : null,
    shot: Number.isInteger(shot) && shot > 0 ? shot : null,
  };
}

export function buildRoute(route: Route): string {
  if (route.at === "start") return "/";
  const base = `/reels/${encodeURIComponent(route.slug)}`;
  const path = route.stage ? `${base}/${route.stage}` : base;
  return route.shot === null ? path : `${path}?shot=${route.shot}`;
}

/**
 * Which stage `/reels/<slug>` means, when the URL does not say.
 *
 * Derived from the board every time, never from a stored "last visited" -- there is no second
 * database here, and a stage the studio remembers is exactly the kind of state that drifts from
 * a hand-edited `storyboard.json`. All four reads are fields `to_json` already publishes.
 */
export function resolveStage(board: Board | null): Stage {
  if (!board || !board.beats.length) return "script";
  if (!allPanelsWritten(board)) return "storyboard";
  if (needsLock(board)) return "storyboard";
  if (!board.manual_stills && board.assets_needed.length) return "assets";
  return "studio";
}

/** Every beat has a panel line. One of four is not a storyboard. */
export function allPanelsWritten(board: Board): boolean {
  return board.beats.length > 0 && board.beats.every((beat) => Boolean(beat.panel?.trim()));
}

/**
 * A gated storyboard that has panels but has not run the lock pass yet.
 * Boards that never gated (empty cursor) are unchanged: hand-written panels are enough.
 * Mirrors `crew.needs_lock`.
 */
export function needsLock(board: Board): boolean {
  if (!allPanelsWritten(board)) return false;
  const done = board.crew?.done ?? [];
  const awaiting = board.crew?.awaiting ?? null;
  if (done.includes("lock")) return false;
  if (!done.length && awaiting === null) return false;
  return true;
}

/** Stills may be generated: every panel is written, and a gated board has locked the roster. */
export function stillsAllowed(board: Board): boolean {
  return allPanelsWritten(board) && !needsLock(board);
}

/**
 * Which stage a running job belongs to, so a rail row can say that something of its own is in
 * flight. `render` is the only kind that spends the GPU and the only one on `studio`.
 */
export const STAGE_JOBS: Record<Stage, string[]> = {
  script: ["develop", "plan", "chat", "revise", "caption", "crew", "agent"],
  storyboard: ["panel_write", "panel_draw", "stage_draw", "stage_chat", "chat", "crew", "agent"],
  assets: ["asset", "still_chat", "ref_draw", "ref_chat", "compose", "assemble", "crew", "agent"],
  studio: ["render", "assemble", "chat", "crew", "agent"],
};
