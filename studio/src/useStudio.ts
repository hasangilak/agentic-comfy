import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "./api";
import type { Board, ChatTurn, Container, Job, ReelSummary, StudioEvent } from "./types";

const COLD: Container = {
  state: "cold",
  live_seconds: 0,
  session_seconds: 0,
  session_cost: 0,
};

export function useStudioState() {
  const [reels, setReels] = useState<ReelSummary[]>([]);
  const [slug, setSlug] = useState<string | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [chat, setChat] = useState<ChatTurn[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [container, setContainer] = useState<Container>(COLD);
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<number[]>([]);
  const [authOk, setAuthOk] = useState(true);

  // The container clock arrives every 2s over SSE. Interpolate locally so it counts up
  // smoothly -- a timer that jumps two seconds at a time reads as broken.
  const [now, setNow] = useState(() => Date.now());
  const stampedAt = useRef(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(timer);
  }, []);

  const slugRef = useRef<string | null>(null);
  slugRef.current = slug;

  const refreshReels = useCallback(async () => {
    try {
      setReels(await api.reels());
    } catch (problem) {
      setError(String(problem));
    }
  }, []);

  const refreshBoard = useCallback(async (target?: string) => {
    const which = target ?? slugRef.current;
    if (!which) return;
    try {
      const payload = await api.board(which);
      setBoard(payload.board);
      setChat(payload.chat);
    } catch (problem) {
      setError(String(problem));
    }
  }, []);

  const openReel = useCallback(
    async (target: string) => {
      setSlug(target);
      slugRef.current = target;
      setSelection([]);
      await refreshBoard(target);
    },
    [refreshBoard],
  );

  // ## The single event stream
  //
  // Job transitions, log lines, per-step progress, board invalidations and the container
  // heartbeat all arrive here. Board changes are announced, not pushed, so the client
  // refetches -- which keeps derived state (staleness, costs) computed in exactly one place.
  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as StudioEvent;
      switch (event.type) {
        case "hello":
          setContainer(event.container);
          stampedAt.current = Date.now();
          setJobs(Object.fromEntries(event.jobs.map((j) => [j.id, j])));
          break;
        case "tick":
        case "container":
          setContainer(event.container);
          stampedAt.current = Date.now();
          break;
        case "job":
          setJobs((current) => ({ ...current, [event.job.id]: event.job }));
          if (event.job.kind === "plan" && event.job.state === "done") {
            const created = (event.job.result as { slug?: string } | null)?.slug;
            void refreshReels();
            if (created) void openReel(created);
          }
          if (event.job.state === "error") setError(event.job.error);
          break;
        case "log":
          setLog((lines) => [...lines, event.line].slice(-300));
          break;
        case "board":
          if (event.slug === slugRef.current) void refreshBoard(event.slug);
          void refreshReels();
          break;
        case "progress":
          setJobs((current) => {
            const job = current[event.job_id];
            if (!job) return current;
            return {
              ...current,
              [event.job_id]: { ...job, step: event.step, step_max: event.step_max },
            };
          });
          break;
      }
    };
    source.onerror = () => setError("lost the studio event stream; is studio.py running?");
    return () => source.close();
  }, [openReel, refreshBoard, refreshReels]);

  useEffect(() => {
    void refreshReels();
    void api.status().then((s) => setAuthOk(s.auth)).catch(() => setAuthOk(true));
  }, [refreshReels]);

  const activeJob = useMemo(
    () => Object.values(jobs).find((job) => job.state === "running") ?? null,
    [jobs],
  );

  /** The container clock, interpolated between heartbeats. */
  const liveSeconds =
    container.state === "cold"
      ? 0
      : container.live_seconds + (now - stampedAt.current) / 1000;
  const sessionCost =
    container.session_cost +
    (container.state === "cold" ? 0 : ((now - stampedAt.current) / 1000) * 0.001089);

  const guard = useCallback(async (work: () => Promise<unknown>) => {
    try {
      await work();
      setError(null);
    } catch (problem) {
      setError(String(problem));
    }
  }, []);

  return {
    reels,
    slug,
    board,
    chat,
    jobs,
    activeJob,
    container,
    liveSeconds,
    sessionCost,
    log,
    error,
    selection,
    authOk,
    setSelection,
    setError,
    openReel,
    refreshBoard,
    refreshReels,
    guard,
  };
}

export type Studio = ReturnType<typeof useStudioState>;

export const StudioContext = createContext<Studio | null>(null);

export function useStudio(): Studio {
  const value = useContext(StudioContext);
  if (!value) throw new Error("useStudio outside a provider");
  return value;
}

/**
 * A text field that is edited locally and saved on a delay.
 *
 * The board refetches on every server event, so a naive controlled input would have its
 * value yanked out from under a typing user. This keeps the local draft authoritative
 * until it is committed, then follows the server again.
 */
export function useDraft(value: string, commit: (next: string) => void, delay = 700) {
  const [draft, setDraft] = useState(value);
  const dirty = useRef(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!dirty.current) setDraft(value);
  }, [value]);

  const change = (next: string) => {
    dirty.current = true;
    setDraft(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      dirty.current = false;
      commit(next);
    }, delay);
  };

  const flush = () => {
    window.clearTimeout(timer.current);
    if (dirty.current) {
      dirty.current = false;
      commit(draft);
    }
  };

  return { draft, change, flush };
}
