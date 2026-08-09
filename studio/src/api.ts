import type {
  Board,
  ChatTurn,
  Estimate,
  GeminiImageModel,
  GeminiImageSize,
  Job,
  ReelSummary,
  Source,
  StageKind,
} from "./types";

type GeminiOptions = { model?: GeminiImageModel; imageSize?: GeminiImageSize };
type DrawOptions = GeminiOptions & { prompt?: string };

const UNREACHABLE =
  "the studio server is not answering on 127.0.0.1:8787 — restart it with `uv run studio.py`";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData sets its own multipart boundary; forcing a JSON content-type breaks it.
  const isForm = init?.body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: init?.body && !isForm ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    // fetch only rejects when the request never got an answer at all.
    throw new Error(UNREACHABLE);
  }
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 300);
    // The Vite proxy turns a refused connection into a bodyless 500. Left as "500:" that
    // reads like a server bug, when the server is simply gone -- and a click on ▶ render
    // then looks like it did nothing at all.
    if (!detail.trim()) throw new Error(`${response.status}: ${UNREACHABLE}`);
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

const patch = <T,>(path: string, body: unknown) =>
  call<T>(path, { method: "PATCH", body: JSON.stringify(body) });

export const api = {
  reels: () => call<{ reels: ReelSummary[] }>("/api/reels").then((r) => r.reels),

  createReel: (concept: string, beats: number, seconds: number) =>
    post<{ job: Job }>("/api/reels", { concept, beats, seconds }).then((r) => r.job),

  /** Adopt a script written outside the studio. No model turn, so the reel exists on return. */
  importReel: (script: string, manualStills: boolean) =>
    post<{ slug: string; board: Board; notes: string[] }>("/api/reels/import", {
      script,
      manual_stills: manualStills,
    }),

  board: (slug: string) =>
    call<{ board: Board; chat: ChatTurn[] }>(`/api/reels/${slug}`),

  patchBoard: (slug: string, body: Partial<Board>) =>
    patch<{ board: Board }>(`/api/reels/${slug}`, body).then((r) => r.board),

  patchBeat: (slug: string, n: number, body: Record<string, unknown>) =>
    patch<{ board: Board }>(`/api/reels/${slug}/beats/${n}`, body).then((r) => r.board),

  addBeat: (slug: string, body: Record<string, unknown>) =>
    post<{ board: Board }>(`/api/reels/${slug}/beats`, body).then((r) => r.board),

  removeBeat: (slug: string, n: number) =>
    call<{ board: Board }>(`/api/reels/${slug}/beats/${n}`, { method: "DELETE" }).then(
      (r) => r.board,
    ),

  estimate: (slug: string, beats?: number[], draft = false) =>
    post<Estimate>(`/api/reels/${slug}/estimate`, { beats, draft }),

  chat: (slug: string, message: string, selection: number[]) =>
    post<{ job: Job }>(`/api/reels/${slug}/chat`, { message, selection }).then((r) => r.job),

  assets: (slug: string, beats?: number[], gemini?: GeminiOptions) =>
    post<{ job: Job }>(`/api/reels/${slug}/assets`, { beats, ...gemini }).then((r) => r.job),

  /**
   * Talk about one beat's still. The model is shown the picture itself alongside everything it
   * is drawn from, rewrites that beat's `asset_prompt`, and usually renders it again through
   * Gemini. The automatic review deliberately does not run on what comes back:
   * half of what a director asks for here is a departure from the reference.
   *
   * `pictures` are attachments, and they are not context for one turn: they are stored on the
   * beat exactly as ⤒ add picture stores them, which is the only way an image reaches the
   * still renderer at all. So they carry that button's consequence too — the beat moves onto
   * the reference join.
   */
  stillChat: (slug: string, n: number, message: string, pictures: File[] = []) => {
    const form = new FormData();
    form.append("message", message);
    for (const file of pictures) form.append("files", file);
    return call<{ job: Job }>(`/api/reels/${slug}/beats/${n}/asset/chat`, {
      method: "POST",
      body: form,
    }).then((r) => r.job);
  },

  /**
   * Have the model rewrite one beat's scene or action from a note about it. The board's own
   * chat can do the same thing, but it has to work out which beat and which line were meant
   * first; here both are in the URL. Marks the beat stale, like typing the change would.
   */
  reviseBeat: (slug: string, n: number, field: "scene" | "action", message: string) =>
    post<{ job: Job }>(`/api/reels/${slug}/beats/${n}/text`, { field, message }).then(
      (r) => r.job,
    ),

  /**
   * `source` says what the picture is for: "reference" makes it the composition this beat opens
   * on, alongside the reel's cast reference (the ordinary cut); "asset" makes it an exact opening
   * keyframe instead; "bridge" keeps the continuation and makes it the frame the clip has to
   * arrive at.
   */
  uploadAsset: (slug: string, n: number, file: File, source: Source = "reference") => {
    const form = new FormData();
    form.append("file", file);
    form.append("source", source);
    return call<{ board: Board }>(`/api/reels/${slug}/beats/${n}/asset`, {
      method: "POST",
      body: form,
    }).then((r) => r.board);
  },

  /**
   * Add reference pictures to a beat and put it on the reference join. Appends, because the
   * prompt names them <Picture 1>..<Picture N> by position -- the server refuses anything
   * past the cap rather than reordering what is already there.
   */
  uploadRefs: (slug: string, n: number, files: File[]) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return call<{ board: Board; stored: number }>(`/api/reels/${slug}/beats/${n}/refs`, {
      method: "POST",
      body: form,
    }).then((r) => r.board);
  },

  /**
   * Say things about one reference picture. `index` is 1-based, the number the prompt uses.
   *
   * `prompt` is what the model should take FROM the picture, and it marks the beat stale
   * because those words go into the render. `draw` is what Gemini is asked for when the picture
   * is drawn again, and it does not — it produces a picture, and the picture's own content hash
   * is already in the fingerprint, exactly as `asset_prompt` is left out because the still it
   * made is hashed.
   *
   * One call for both, because the route already meant "say things about picture `index`". Only
   * the keys given are written, so two controls can edit the two fields independently.
   */
  describeRef: (slug: string, n: number, index: number, body: { prompt?: string; draw?: string }) =>
    patch<{ board: Board }>(`/api/reels/${slug}/beats/${n}/refs/${index}`, body).then(
      (r) => r.board,
    ),

  /**
   * Draw a NEW reference picture from a prompt alone, and put the beat on the reference join.
   *
   * No empty slot is created on the way: the picture exists when its file does, so the tile
   * for one in flight comes from the job rather than from the board.
   */
  createRef: (slug: string, n: number, prompt: string, gemini?: GeminiOptions) =>
    post<{ job: Job }>(`/api/reels/${slug}/beats/${n}/refs/draw`, { prompt, ...gemini }).then((r) => r.job),

  /** Draw an existing picture again, from the prompt already stored on it. */
  drawRef: (slug: string, n: number, index: number, options?: DrawOptions) =>
    post<{ job: Job }>(`/api/reels/${slug}/beats/${n}/refs/${index}/draw`, options ?? {}).then((r) => r.job),

  /**
   * Say what should be different about one reference picture.
   *
   * JSON rather than multipart, unlike `stillChat`: there an attachment means "here is what I
   * mean" and is kept on the beat, because the still is drawn from the beat. Here the picture
   * IS the subject, so a file sent with the note would mint a tenth reference nobody asked for.
   */
  refChat: (slug: string, n: number, index: number, message: string) =>
    post<{ job: Job }>(`/api/reels/${slug}/beats/${n}/refs/${index}/chat`, { message }).then(
      (r) => r.job,
    ),

  /** `index` is 1-based: the number in the prompt, not the array position. */
  removeRef: (slug: string, n: number, index: number) =>
    call<{ board: Board }>(`/api/reels/${slug}/beats/${n}/refs/${index}`, {
      method: "DELETE",
    }).then((r) => r.board),

  // ## Staging — the reel's cast and sets
  //
  // Reel-scoped, which is what makes these different from every route above: no beat number,
  // and one binding call that says which scenes contain what.

  /** Mint one design. Free and synchronous — it creates an entry, it does not draw a sheet. */
  addStage: (slug: string, body: { kind: StageKind; name: string; note?: string; draw?: string }) =>
    post<{ board: Board; id: string }>(`/api/reels/${slug}/staging`, body),

  /**
   * Rename a design, say what it IS, or say what it should be drawn as.
   *
   * `name` and `note` reach the render — together they are the sentence every prompt is told
   * about this design — so editing either marks every scene that binds it stale. `draw` does
   * not, for the same reason `describeRef`'s does not: it produces a sheet, and the sheet's own
   * content hash is already in the fingerprint.
   */
  describeStage: (
    slug: string,
    id: string,
    body: { kind?: StageKind; name?: string; note?: string; draw?: string },
  ) => patch<{ board: Board }>(`/api/reels/${slug}/staging/${id}`, body).then((r) => r.board),

  /** Draw or redraw one sheet. `prompt` lets an uploaded sheet be edited in one action. */
  drawStage: (slug: string, id: string, options?: DrawOptions) =>
    post<{ job: Job }>(`/api/reels/${slug}/staging/${id}/draw`, options ?? {}).then((r) => r.job),

  /** Say what should be different about one sheet. No attachments: the sheet IS the subject. */
  stageChat: (slug: string, id: string, message: string) =>
    post<{ job: Job }>(`/api/reels/${slug}/staging/${id}/chat`, { message }).then((r) => r.job),

  uploadStageSheet: (slug: string, id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<{ board: Board }>(`/api/reels/${slug}/staging/${id}/sheet`, {
      method: "POST",
      body: form,
    }).then((r) => r.board);
  },

  /** Drop a design, its sheet, every binding to it, and every @-mention of it. */
  removeStage: (slug: string, id: string) =>
    call<{ board: Board }>(`/api/reels/${slug}/staging/${id}`, { method: "DELETE" }).then(
      (r) => r.board,
    ),

  /**
   * Say which designs one scene contains, in the order they are numbered. Replaces rather than
   * appends — the control is a set of toggles, and that is one answer.
   *
   * Unlike adding a picture this never moves the join, so there is nothing to warn about: a
   * picture only reaches a render through the reference join, while a bound design reaches every
   * join — as <Picture i> where there are picture slots, and as a sentence everywhere else.
   */
  bindStage: (slug: string, n: number, ids: string[]) =>
    call<{ board: Board; staging: string[] }>(`/api/reels/${slug}/beats/${n}/staging`, {
      method: "PUT",
      body: JSON.stringify({ ids }),
    }).then((r) => r.board),

  /**
   * Have the local model write the shot grammar for the reel: shot size, angle, camera move per
   * scene. Free, and the default is every scene rather than the blank ones — the sizes are judged
   * against each other, so a pass over one scene cannot see the four wide shots before it.
   */
  writePanels: (slug: string, beats?: number[]) =>
    post<{ job: Job }>(`/api/reels/${slug}/panels/text`, { beats }).then((r) => r.job),

  /** Draw the panels: the scenes named, or every scene with text and no sketch yet. */
  drawPanels: (slug: string, beats?: number[]) =>
    post<{ job: Job }>(`/api/reels/${slug}/panels`, { beats }).then((r) => r.job),

  /** Draw or redraw one scene's panel, optionally saving new shot grammar for it first. */
  drawPanel: (slug: string, n: number, panel?: string) =>
    post<{ job: Job }>(`/api/reels/${slug}/beats/${n}/panel`, { panel }).then((r) => r.job),

  /** Throw one panel away. Nothing is conditioned on it, so nothing downstream changes. */
  removePanel: (slug: string, n: number) =>
    call<{ board: Board }>(`/api/reels/${slug}/beats/${n}/panel`, { method: "DELETE" }).then(
      (r) => r.board,
    ),

  uploadReference: (slug: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<{ board: Board }>(`/api/reels/${slug}/reference`, {
      method: "POST",
      body: form,
    }).then((r) => r.board);
  },

  clearReference: (slug: string) =>
    call<{ board: Board }>(`/api/reels/${slug}/reference`, { method: "DELETE" }).then(
      (r) => r.board,
    ),

  /**
   * Throw away a beat's rendered clip. The beat goes back to `ready` and everything chained
   * below it reads as following a change. The file is moved into the reel's `.discarded/`,
   * not deleted.
   */
  discardClip: (slug: string, n: number) =>
    call<{ board: Board; discarded: string }>(`/api/reels/${slug}/beats/${n}/video`, {
      method: "DELETE",
    }).then((r) => r.board),

  caption: (slug: string) =>
    post<{ job: Job }>(`/api/reels/${slug}/caption`).then((r) => r.job),

  render: (slug: string, beats?: number[], draft = false) =>
    post<{ job: Job; estimate: Estimate }>(`/api/reels/${slug}/render`, { beats, draft }),

  cancel: (jobId: string) => post<{ job: Job }>(`/api/jobs/${jobId}/cancel`).then((r) => r.job),

  stopApp: () => post<unknown>("/api/app/stop"),

  status: () =>
    call<{
      auth: boolean;
      backend: string;
      /** "none" means the image server is not running, so stills have to be uploads. */
      stills: { backend: "papercut" | "none"; papercut_url: string };
      /** The local model. Without it there is no script, no conversation and no caption. */
      language: { url: string; model: string; ready: boolean };
    }>("/api/status"),
};

/** Seconds -> "4:12", for the container clock. */
export function clock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export const money = (value: number) => `$${value.toFixed(2)}`;
