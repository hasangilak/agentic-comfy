import type { Source } from "./types";

/**
 * How a join reads outside the canvas.
 *
 * The canvas draws a join as a wire — green for an unbroken take, dashed amber for a cut — and
 * that is the best answer where two scenes are side by side. Every other stage shows one scene
 * at a time, where there is no wire to read, so it needs a word instead. `SequenceNode`'s
 * `JOIN_HELP` stays where it is: it is the sentence on the button that CHANGES the join, and
 * changing the chain is the Studio's job.
 */
export const JOIN_LOOK: Record<Source, { short: string; tone: string; hint: string }> = {
  chain: {
    short: "↳ continues",
    tone: "text-live",
    hint: "the same take carrying on from the previous clip's last frame — needs no still",
  },
  bridge: {
    short: "↳ arrives on a still",
    tone: "text-live",
    hint: "continues from the previous clip AND has to land on this scene's own still",
  },
  reference: {
    short: "◈ cut, close",
    tone: "text-warm",
    hint: "opens close to its own still; sheets and poses ride the clip. A later beat of the same shot on this join holds the previous clip as identity",
  },
  asset: {
    short: "◈ cut, exact",
    tone: "text-warm",
    hint: "a clean cut whose first frame IS this scene's still, pixel for pixel",
  },
};
