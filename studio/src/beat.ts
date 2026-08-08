import type { Beat } from "./types";

/**
 * The two pictures lists a beat has, and the one place either numbering is worked out.
 *
 * There are two because the server has two, and they are ordered differently. The VIDEO model
 * is given `Board.pictures_for` — this beat's own still, then the reel's cast reference, then
 * the director's uploads — and addresses them as `<Picture N>`. The STILL model is given
 * `Board.still_pictures` — cast reference FIRST, then the uploads, capped at four — and is
 * given no tags at all. The same picture is a different number in each, and in the two fields
 * that feed them.
 *
 * This module mirrors those two methods and must not drift from them; `board.py` is the
 * authority, and every guard below quotes the line it is mirroring. Before this existed the
 * arithmetic was hand-rolled in three components, and the @-mention picker would have made it
 * five.
 */
export interface PictureRef {
  /** How the API addresses the file: 1-based upload index. null on an automatic slot. */
  index: number | null;
  /** Stable across a removal, unlike `index`. What a mention token carries. */
  id: string | null;
  url: string | null;
  /** What the picture is FOR, in the director's words. */
  note: string;
  /** What the prompt THIS list feeds calls it. The whole reason there are two lists. */
  tag: string;
  /** What the canvas calls it. */
  label: string;
  /** null when it cannot be mentioned — an automatic slot has no stable handle. */
  token: string | null;
  /** Why it is in the list but greyed: shown rather than hidden, so the menu never lies. */
  unavailable?: string;
}

/** The literal a field stores to name one picture. Mirrors `config.mention_token`. */
export function mentionToken(id: string): string {
  return id === "cast" ? "@cast" : `@ref:${id}`;
}

/** Mirrors `config.MENTION_RE`. Kept in sync by hand; there is no shared schema. */
export const MENTION_RE = /@(?:ref:([0-9a-f]{4,12})|(cast))(?![\w:])/g;

/** Every picture named in a text, in order, duplicates kept. */
export function mentionsIn(text: string): string[] {
  return [...(text ?? "").matchAll(MENTION_RE)].map((m) => m[1] ?? "cast");
}

/**
 * The reel's cast reference as this beat sees it, or null on the beat whose own still IS it.
 *
 * The `url !== beat.asset` test is what encodes that exception — the server drops the cast slot
 * on that beat rather than showing it a picture of itself.
 */
export function castRef(beat: Beat): { url: string; note: string } | null {
  const found = (beat.auto_refs ?? []).find((a) => a.url && a.url !== beat.asset);
  return found?.url ? { url: found.url, note: found.note } : null;
}

/** How many more pictures this beat can take, less anything staged in a composer. */
export function slotsLeft(beat: Beat, staged = 0): number {
  return Math.max(0, beat.ref_slots - (beat.refs?.length ?? 0) - staged);
}

function uploads(beat: Beat): PictureRef[] {
  return (beat.refs ?? []).map((url, at) => ({
    index: at + 1,
    id: beat.ref_ids?.[at] ?? null,
    url,
    note: beat.ref_prompts?.[at] ?? "",
    tag: "",
    label: `picture ${beat.ref_offset + at + 1}`,
    token: beat.ref_ids?.[at] ? mentionToken(beat.ref_ids[at]) : null,
  }));
}

/**
 * What the VIDEO model is given, in `<Picture N>` order. Mirrors `Board.pictures_for`.
 *
 * Empty off the reference join, exactly as the server's `uses_refs` guard makes it
 * (`board.py`, `pictures_for`). A picture on a chained or keyframe beat conditions nothing, and
 * a menu that offered it would be promising the model something it never sees.
 */
export function videoPictures(beat: Beat): PictureRef[] {
  if (beat.source !== "reference") return [];
  const autos: PictureRef[] = (beat.auto_refs ?? []).map((auto, at) => ({
    index: null,
    id: auto.url === beat.asset ? null : "cast",
    url: auto.url,
    note: auto.note,
    tag: "",
    // The first automatic slot is this beat's own still when it opens on one; the other is the
    // cast. `opens_on` is the flag the server publishes rather than something inferred here.
    label: at === 0 && beat.opens_on ? "opening still" : "cast reference",
    token: auto.url === beat.asset ? null : "@cast",
  }));
  return [...autos, ...uploads(beat)]
    .map((picture, at) => ({ ...picture, tag: `<Picture ${at + 1}>` }));
}

/**
 * What the STILL model is given. Mirrors `Board.still_pictures`, including its asymmetry.
 *
 * The cast reference is in **unconditionally**; the director's uploads are in **only** on a
 * reference join. Getting that backwards would have the @-menu naming pictures Gemini is never
 * handed. Everything past `still_refs` stays in the list but is marked unavailable — hiding it
 * would make the menu disagree with the tray you are looking at.
 */
export function stillPictures(beat: Beat): PictureRef[] {
  const cast = castRef(beat);
  const found: PictureRef[] = cast
    ? [{
        index: null,
        id: "cast",
        url: cast.url,
        note: cast.note,
        tag: "",
        label: "cast reference",
        token: "@cast",
      }]
    : [];
  if (beat.source === "reference") found.push(...uploads(beat));
  return found.map((picture, at) => ({
    ...picture,
    tag: `the ${ordinal(at + 1)} reference image`,
    ...(picture.index !== null && picture.index > beat.still_refs
      ? { unavailable: "past the still renderer's cap — this one reaches the clip only" }
      : {}),
  }));
}

function ordinal(position: number): string {
  if (position % 100 >= 10 && position % 100 <= 20) return `${position}th`;
  return `${position}${{ 1: "st", 2: "nd", 3: "rd" }[position % 10] ?? "th"}`;
}

/**
 * The consequence of adding a picture to this beat, or null when there is none.
 *
 * Storing a picture moves the beat onto the reference join (`api.store_refs`), and what that
 * costs depends on the join it is leaving. One place, because there are three controls that can
 * add a picture and the warning has to say the same thing at all of them.
 */
export function joinWarning(beat: Beat): string | null {
  if (beat.source === "reference") return null;
  if (beat.source === "chain" || beat.source === "bridge") {
    return `scene ${beat.n} becomes a clean cut — it stops continuing from scene ${beat.n - 1}`;
  }
  return (
    `scene ${beat.n} stops opening on its still exactly — the still becomes something the ` +
    `clip is conditioned towards rather than the frame it starts on`
  );
}
