import type { Beat, StageEntry } from "./types";

/**
 * The two pictures lists a beat has, and the one place either numbering is worked out.
 *
 * There are two because the server has two, and they are ordered differently. The VIDEO model
 * is given `Board.pictures_for` — this beat's own still, then the reel's cast reference, then
 * the designs this scene binds, then the director's uploads — and addresses them as
 * `<Picture N>`. The STILL model is given `Board.still_pictures` — cast reference FIRST, then
 * the bound designs that are not sets, then the uploads, capped at four — and is given no tags
 * at all. The same picture is a different number in each, and in the two fields that feed them.
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

/**
 * How a design's id is written wherever picture ids and design ids share one namespace — this
 * module's `PictureRef.id`, and the server's `Board.mentions` dict. Mirrors
 * `config.STAGE_MENTION_PREFIX`.
 *
 * They are minted independently — a beat's `ref_ids` and the reel's designs are two lists with
 * no shared counter — so a bare six-character body could name either, and a design that happened
 * to collide with a picture on scene 3 would resolve onto whichever was seen last.
 */
export const STAGE_PREFIX = "stage:";
export const stageId = (id: string) => `${STAGE_PREFIX}${id}`;

/** The literal a field stores to name one picture. Mirrors `config.mention_token`. */
export function mentionToken(id: string): string {
  if (id === "cast") return "@cast";
  if (id.startsWith(STAGE_PREFIX)) return `@stage:${id.slice(STAGE_PREFIX.length)}`;
  return `@ref:${id}`;
}

/** Mirrors `config.MENTION_RE`. Kept in sync by hand; there is no shared schema. */
export const MENTION_RE = /@(?:ref:([0-9a-f]{4,12})|stage:([0-9a-f]{4,12})|(cast))(?![\w:])/g;

/** Every picture named in a text, in order, duplicates kept. Namespaced as `mentionToken` reads. */
export function mentionsIn(text: string): string[] {
  return [...(text ?? "").matchAll(MENTION_RE)].map((m) =>
    m[1] ?? (m[2] ? stageId(m[2]) : "cast"),
  );
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
 * The designs this scene binds that reach THIS render as pictures. Mirrors
 * `Board.staging_pictures`, whose `for_still` argument is the one asymmetry: a set sheet is a
 * picture for the clip and prose for the still, because four slots (one already the cast) do not
 * hold three characters and a clearing.
 *
 * A design with no sheet drawn yet is skipped here and reaches both renders as words instead,
 * which is what makes writing the bible useful before anything has been drawn.
 */
function stagePictures(beat: Beat, staging: StageEntry[], forStill: boolean): PictureRef[] {
  const byId = new Map(staging.map((entry) => [entry.id, entry]));
  return (beat.staging ?? [])
    .map((id) => byId.get(id))
    .filter((entry): entry is StageEntry => Boolean(entry?.sheet))
    .filter((entry) => !(forStill && entry.kind === "environment"))
    .map((entry) => ({
      index: null,
      id: stageId(entry.id),
      url: entry.sheet,
      note: entry.role,
      tag: "",
      label: entry.name,
      token: mentionToken(stageId(entry.id)),
    }));
}

/**
 * What the VIDEO model is given, in `<Picture N>` order. Mirrors `Board.pictures_for`.
 *
 * Empty off the reference join, exactly as the server's `uses_refs` guard makes it
 * (`board.py`, `pictures_for`). A picture on a chained or keyframe beat conditions nothing, and
 * a menu that offered it would be promising the model something it never sees.
 *
 * `staging` is the reel's whole bible; this picks out what THIS scene binds. Passed in rather
 * than reached for, so the numbering and the list it numbers come from one board read.
 */
export function videoPictures(beat: Beat, staging: StageEntry[] = []): PictureRef[] {
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
  return [...autos, ...stagePictures(beat, staging, false), ...uploads(beat)]
    .map((picture, at) => ({ ...picture, tag: `<Picture ${at + 1}>` }));
}

/**
 * What the STILL model is given. Mirrors `Board.still_pictures`, including its asymmetry.
 *
 * The cast reference is in **unconditionally**; the bound designs and the director's uploads are
 * in **only** on a reference join. Getting that backwards would have the @-menu naming pictures
 * Gemini is never handed. Everything past the cap stays in the list but is marked unavailable —
 * hiding it would make the menu disagree with the tray you are looking at.
 */
export function stillPictures(beat: Beat, staging: StageEntry[] = []): PictureRef[] {
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
  if (beat.source === "reference") {
    found.push(...stagePictures(beat, staging, true), ...uploads(beat));
  }
  // How many of this list the still renderer actually gets. Counted off the three numbers the
  // server publishes rather than off `max_still_refs`, because the image server may report a
  // lower cap than ours and those three are what it decided.
  const reaching = (cast ? 1 : 0) + beat.staging_still_refs + beat.still_refs;
  return found.map((picture, at) => ({
    ...picture,
    tag: `the ${ordinal(at + 1)} reference image`,
    ...(at >= reaching
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
