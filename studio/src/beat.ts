import type { Beat, StageEntry } from "./types";

/**
 * The two pictures lists a beat has, and the one place either numbering is worked out.
 *
 * There are two because the server has two, and they are ordered differently. The VIDEO model
 * is given `Board.pictures_for` — this beat's own still, then the reel's cast reference only
 * when identity sheets are missing, then the designs this scene binds (characters before sets),
 * then the director's uploads — and addresses them as `<Picture N>`. Empty on chain and bridge.
 * An asset cut that binds character sheets is not empty: H3 has no keyframe socket for a
 * turnaround, so that cut is ref2va with the still as Picture 1. The STILL model is given
 * `Board.still_pictures` — identity sheets (or the cast still when there are none), then this
 * beat's storyboard panels when they fit, then the previous pose, a set, then uploads on a
 * reference join, capped at nine — and
 * is given no tags at all. The same picture is a different number in each, and in the two
 * fields that feed them.
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
  const found = (beat.auto_refs ?? []).find((a) => {
    if (!a.url) return false;
    if (a.kind === "cast") return true;
    // Boards published before `kind` existed: the cast slot is the auto-ref that is not this
    // beat's own still. A pose sequence would fool that test, which is why `kind` exists.
    if (a.kind) return false;
    return a.url !== beat.asset;
  });
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
 * `Board.staging_pictures`. On the still side environments sort last so the four-slot cap
 * drops the set first; a set that fits is a picture, because prose invents a new geometry
 * every shot.
 *
 * A design with no sheet drawn yet is skipped here and reaches both renders as words instead,
 * which is what makes writing the bible useful before anything has been drawn.
 */
function stagePictures(beat: Beat, staging: StageEntry[], _forStill: boolean): PictureRef[] {
  const byId = new Map(staging.map((entry) => [entry.id, entry]));
  const found = (beat.staging ?? [])
    .map((id) => byId.get(id))
    .filter((entry): entry is StageEntry => Boolean(entry?.sheet));
  // Both renders: characters and props first, so a tight cap drops the set. Mirrors
  // `Board.staging_pictures`. `forStill` still names which render the caller is asking for.
  found.sort((a, b) => Number(a.kind === "environment") - Number(b.kind === "environment"));
  return found.map((entry) => ({
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
 * Empty on chain and bridge, matching `Board.wires_refs`: those joins keep a keyframe latent
 * and cannot mix with pictures. An asset cut that binds character or prop sheets is not empty
 * — fl2va has no socket for a turnaround, so that cut is ref2va with the still as Picture 1.
 *
 * `staging` is the reel's whole bible; this picks out what THIS scene binds. Passed in rather
 * than reached for, so the numbering and the list it numbers come from one board read.
 */
export function videoPictures(beat: Beat, staging: StageEntry[] = []): PictureRef[] {
  const identity = (beat.staging ?? [])
    .map((id) => staging.find((entry) => entry.id === id))
    .some((entry) => Boolean(entry?.sheet) && entry?.kind !== "environment");
  if (beat.source !== "reference" && !(beat.source === "asset" && identity)) return [];
  const autos: PictureRef[] = (beat.auto_refs ?? []).map((auto, at) => {
    const kind =
      auto.kind ??
      (at === 0 && beat.opens_on ? "opening" : auto.url === beat.asset ? "opening" : "cast");
    return {
      index: null,
      id: kind === "cast" ? "cast" : null,
      url: auto.url,
      note: auto.note,
      tag: "",
      label:
        kind === "opening"
          ? "opening still"
          : kind === "pose"
            ? `pose ${at + 1}`
            : "cast reference",
      token: kind === "cast" ? "@cast" : null,
    };
  });
  const uploaded = beat.source === "reference" ? uploads(beat) : [];
  return [...autos, ...stagePictures(beat, staging, false), ...uploaded]
    .map((picture, at) => ({ ...picture, tag: `<Picture ${at + 1}>` }));
}

/**
 * What the STILL model is given. Mirrors `Board.still_pictures`.
 *
 * Identity sheets (character/prop) are join-agnostic. The composed cast still is in only when
 * those sheets are missing. Storyboard panels sit after identity when the cap is at least two
 * — never first, so an older image server that reads only the first reference does not turn
 * the still into a pencil drawing. The previous pose comes before the set. Director uploads
 * are in only on a reference join. Everything past the cap stays in the list but is marked
 * unavailable — hiding it would make the menu disagree with the tray you are looking at.
 */
export function panelUrls(beat: Beat): string[] {
  if (beat.panel_urls?.length) return beat.panel_urls.filter(Boolean);
  return beat.panel_url ? [beat.panel_url] : [];
}

export function stillPictures(beat: Beat, staging: StageEntry[] = []): PictureRef[] {
  const hasIdentity = (beat.staging ?? [])
    .map((id) => staging.find((entry) => entry.id === id))
    .some((entry) => Boolean(entry?.sheet) && entry?.kind !== "environment");
  const cast = hasIdentity ? null : castRef(beat);
  const identity: PictureRef[] = [];
  if (cast) {
    identity.push({
      index: null,
      id: "cast",
      url: cast.url,
      note: cast.note,
      tag: "",
      label: "cast reference",
      token: "@cast",
    });
  }
  const staged = stagePictures(beat, staging, true);
  const identitySheets = staged.filter((picture) => {
    const id = picture.id?.startsWith(STAGE_PREFIX) ? picture.id.slice(STAGE_PREFIX.length) : "";
    return staging.find((entry) => entry.id === id)?.kind !== "environment";
  });
  const envSheets = staged.filter((picture) => {
    const id = picture.id?.startsWith(STAGE_PREFIX) ? picture.id.slice(STAGE_PREFIX.length) : "";
    return staging.find((entry) => entry.id === id)?.kind === "environment";
  });
  identity.push(...identitySheets);
  // Previous pose before the set, matching `Board.still_pictures`.
  const rest: PictureRef[] = [];
  if (
    beat.previous_pose &&
    !identity.some((picture) => picture.url === beat.previous_pose)
  ) {
    rest.push({
      index: null,
      id: null,
      url: beat.previous_pose,
      note: "the last pose of the previous shot -- same puppets and materials, not this shot's camera",
      tag: "",
      label: "previous shot's last pose",
      token: null,
    });
  }
  rest.push(...envSheets);
  if (beat.source === "reference") {
    rest.push(...uploads(beat));
  }
  const urls = panelUrls(beat);
  const panels: PictureRef[] = urls.map((url, at) => ({
    index: null,
    id: null,
    url,
    note:
      urls.length > 1
        ? `storyboard panel ${at + 1} of ${urls.length} -- a graphite sketch of this shot at that moment of the action. Match the composition in sequence. Do not copy the pencil medium`
        : "this beat's storyboard panel -- a graphite sketch of this shot's framing, angle, and who stands where. Match that composition. Do not copy the pencil medium",
    tag: "",
    label: urls.length > 1 ? `storyboard panel ${at + 1}` : "storyboard panel",
    token: null,
  }));
  const panelCount = Number(beat.still_panel) || 0;
  const prevExtra =
    beat.previous_pose && beat.previous_pose !== cast?.url ? 1 : 0;
  const reaching =
    (beat.still_cast ? 1 : 0) +
    beat.staging_still_refs +
    beat.still_refs +
    prevExtra +
    panelCount;
  const panelsShown = panels.slice(0, panelCount);
  const room = Math.max(0, reaching - panelsShown.length);
  const identityKept = identity.slice(0, room);
  const restKept = rest.slice(0, Math.max(0, room - identityKept.length));
  const found = [
    ...identityKept,
    ...panelsShown,
    ...restKept,
    ...identity.slice(identityKept.length),
    ...panels.slice(panelsShown.length),
    ...rest.slice(restKept.length),
  ];
  return found.map((picture, at) => ({
    ...picture,
    tag: `the ${ordinal(at + 1)} reference image`,
    ...(at >= reaching
      ? {
          unavailable: picture.label.startsWith("storyboard panel")
            ? "past the still renderer's cap — this sketch was not sent"
            : "past the still renderer's cap — this one reaches the clip only",
        }
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
