import type { Beat, Board } from "./types";

/**
 * Binding a design to a scene, in one place.
 *
 * `PUT /beats/{n}/staging` **replaces** rather than appends, so every control that toggles one
 * design has to send the whole next array. Two surfaces do that now — the chips in `BeatModal`
 * and the storyboard grid, which binds one design across the whole reel at once — and the rule
 * that keeps binding safe is subtle enough that a second copy of it would be a bug waiting:
 * a design goes on the END when it is added, so binding one never renumbers the pictures
 * already there. Same reason `uploadRefs` appends.
 */
export function nextBinding(bound: string[], id: string): string[] {
  return bound.includes(id) ? bound.filter((other) => other !== id) : [...bound, id];
}

/** Which scenes contain a design. The binding lives on the beat, so this is the whole answer. */
export function scenesFor(board: Board, id: string): number[] {
  return board.beats.filter((beat) => beat.staging?.includes(id)).map((beat) => beat.n);
}

/** The same, as the sentence the panels say — or the absence, said plainly. */
export function sceneList(board: Board, id: string): string {
  const scenes = scenesFor(board, id);
  if (!scenes.length) return "in no scene yet";
  return `${scenes.length === 1 ? "scene" : "scenes"} ${scenes.join(", ")}`;
}

/** The names a scene's bindings resolve to, in the order the prompts number them. */
export function boundNames(board: Board, beat: Beat): string[] {
  return (beat.staging ?? [])
    .map((id) => board.staging.find((entry) => entry.id === id)?.name)
    .filter((name): name is string => Boolean(name));
}
