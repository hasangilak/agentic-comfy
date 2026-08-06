import type { Board, ChatTurn, Estimate, Job, ReelSummary, Source } from "./types";

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

  /** Adopt a script written outside the studio. No agy turn, so the reel exists on return. */
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

  assets: (slug: string, beats?: number[]) =>
    post<{ job: Job }>(`/api/reels/${slug}/assets`, { beats }).then((r) => r.job),

  /**
   * `source` says which keyframe slot the picture is for: "asset" makes it this beat's
   * opening frame (a cut), "bridge" keeps the continuation and makes it the frame the clip
   * has to arrive at.
   */
  uploadAsset: (slug: string, n: number, file: File, source: Source = "asset") => {
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

  /** `index` is 1-based: the number in the prompt, not the array position. */
  removeRef: (slug: string, n: number, index: number) =>
    call<{ board: Board }>(`/api/reels/${slug}/beats/${n}/refs/${index}`, {
      method: "DELETE",
    }).then((r) => r.board),

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

  caption: (slug: string) =>
    post<{ job: Job }>(`/api/reels/${slug}/caption`).then((r) => r.job),

  render: (slug: string, beats?: number[], draft = false) =>
    post<{ job: Job; estimate: Estimate }>(`/api/reels/${slug}/render`, { beats, draft }),

  cancel: (jobId: string) => post<{ job: Job }>(`/api/jobs/${jobId}/cancel`).then((r) => r.job),

  stopApp: () => post<unknown>("/api/app/stop"),

  status: () => call<{ auth: boolean; backend: string }>("/api/status"),
};

/** Seconds -> "4:12", for the container clock. */
export function clock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export const money = (value: number) => `$${value.toFixed(2)}`;
