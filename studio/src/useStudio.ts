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
import { buildRoute, parseRoute, resolveStage, type Stage } from "./route";
import type { Board, ChatTurn, Container, Job, ReelSummary, StudioEvent, ActivityEvent } from "./types";

const COLD: Container = {
  state: "cold",
  live_seconds: 0,
  session_seconds: 0,
  session_cost: 0,
};

export function useStudioState() {
  const [reels, setReels] = useState<ReelSummary[]>([]);
  const [slug, setSlug] = useState<string | null>(null);
  // Which stage the URL asked for, or null when it only named the reel. Kept separate from the
  // resolved stage below so that `/reels/x` keeps meaning "wherever this reel is up to" as the
  // board changes underneath it, rather than freezing on whatever it meant at first paint.
  const [stage, setStage] = useState<Stage | null>(null);
  const [board, setBoard] = useState<Board | null>(null);
  const [chat, setChat] = useState<ChatTurn[]>([]);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [container, setContainer] = useState<Container>(COLD);
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  /** Live activity events for in-flight agent jobs, keyed by job id. */
  const [liveActivity, setLiveActivity] = useState<Record<string, ActivityEvent[]>>({});
  const [selection, setSelection] = useState<number[]>([]);
  const [renderSelection, setRenderSelection] = useState<number[]>([]);
  // Which beat is open full-screen, if any. Shared state rather than the node's own, because
  // the modal is rendered at the top of the app: inside a node it would live under React
  // Flow's transformed viewport, where `position: fixed` is measured from the panned and
  // zoomed layer instead of from the window.
  const [expanded, setExpanded] = useState<number | null>(null);
  // Whether the design bible is open, and which design is selected in it. Shared for the same
  // reason `expanded` is -- it is rendered at the top of the app rather than inside the node
  // that opens it -- and keyed by id rather than by position, because deleting a design would
  // otherwise leave the panel editing whichever one slid up into the slot.
  const [stagingOpen, setStagingOpen] = useState(false);
  const [stagingPick, setStagingPick] = useState<string | null>(null);
  const [authOk, setAuthOk] = useState(true);
  // Until /api/status answers. Must match paperreel.config.RATE_PER_SEC (B200 + 8 cores + 128 GiB).
  const [ratePerSecond, setRatePerSecond] = useState(0.00212496);
  // The two local services this studio orchestrates, and both can simply not be running.
  // Optimistic defaults: the copy that depends on them is a warning, and flashing "the image
  // server is down" for the few hundred milliseconds before /api/status answers would train
  // the user to ignore it.
  const [stillsBackend, setStillsBackend] = useState<"papercut" | "none">("papercut");
  const [model, setModel] = useState<{ model: string; ready: boolean }>({
    model: "",
    ready: true,
  });

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

  const selectReel = useCallback(
    async (target: string) => {
      setSlug(target);
      slugRef.current = target;
      setBoard(null);
      setChat([]);
      setSelection([]);
      setRenderSelection([]);
      setExpanded(null);
      setStagingOpen(false);
      setStagingPick(null);
      await refreshBoard(target);
    },
    [refreshBoard],
  );

  const clearReel = useCallback(() => {
    setSlug(null);
    slugRef.current = null;
    setStage(null);
    setBoard(null);
    setChat([]);
    setSelection([]);
    setRenderSelection([]);
    setExpanded(null);
    setStagingOpen(false);
    setStagingPick(null);
  }, []);

  /**
   * Go somewhere. The one navigation call: it pushes the URL and moves the store to match.
   *
   * Omitting `stage` is meaningful and is not the same as passing `"script"` -- it addresses
   * the reel rather than a stage of it, and `resolveStage` answers from the board.
   */
  const go = useCallback(
    async (target: string | null, next?: Stage) => {
      if (!target) {
        if (window.location.pathname !== "/") window.history.pushState({}, "", "/");
        clearReel();
        return;
      }
      const path = buildRoute({ at: "reel", slug: target, stage: next ?? null, shot: null });
      if (window.location.pathname + window.location.search !== path) {
        window.history.pushState({}, "", path);
      }
      setStage(next ?? null);
      if (target !== slugRef.current) await selectReel(target);
    },
    [clearReel, selectReel],
  );

  /** Move between stages of the reel already open. No refetch: the board is already here. */
  const goStage = useCallback(
    (next: Stage) => {
      if (!slugRef.current) return;
      void go(slugRef.current, next);
    },
    [go],
  );

  // Kept for every call site that only ever meant "open this reel". Landing on no stage is
  // deliberate: a reel opened from the rail should show whatever it is waiting on.
  const openReel = useCallback(
    async (target: string, next?: Stage) => {
      await go(target, next);
    },
    [go],
  );

  // A board is an addressable page, not transient picker state. Restore it on a direct
  // visit/refresh and keep the studio in sync with browser back and forward navigation.
  useEffect(() => {
    const followLocation = () => {
      const route = parseRoute(window.location);
      if (route.at !== "reel") {
        clearReel();
        return;
      }
      setStage(route.stage);
      if (route.slug !== slugRef.current) {
        void selectReel(route.slug).then(() => setExpanded(route.shot));
        return;
      }
      setExpanded(route.shot);
    };

    followLocation();
    window.addEventListener("popstate", followLocation);
    return () => window.removeEventListener("popstate", followLocation);
  }, [clearReel, selectReel]);

  /**
   * The expanded scene is addressable, so a link to one survives a reload -- but it is not a
   * page: `replaceState`, not `pushState`, or closing the modal would need a Back press and
   * the browser's history would fill up with every scene anyone glanced at.
   */
  const openShot = useCallback((n: number | null) => {
    setExpanded(n);
    const route = parseRoute(window.location);
    if (route.at !== "reel") return;
    window.history.replaceState({}, "", buildRoute({ ...route, shot: n }));
  }, []);

  /**
   * Beat numbers are positional IDs: insert or remove a scene and every number after it means a
   * different scene. Anything holding one has to let go.
   *
   * Here rather than in `Canvas` because the canvas is one stage of four now, and the modal it
   * used to guard can be opened from three of them -- a guard that only fires while React Flow
   * is mounted is a guard that silently stops working.
   */
  const beatCount = useRef<number | null>(null);
  useEffect(() => {
    const count = board?.beats.length ?? null;
    if (beatCount.current !== null && count !== null && beatCount.current !== count) {
      setSelection([]);
      setRenderSelection([]);
      openShot(null);
    }
    beatCount.current = count;
  }, [board, openShot]);

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
          if (event.job.state !== "running") {
            setLiveActivity((current) => {
              const next = { ...current };
              delete next[event.job.id];
              return next;
            });
          }
          if (event.job.kind === "plan" && event.job.state === "done") {
            const created = (event.job.result as { slug?: string } | null)?.slug;
            void refreshReels();
            // Onto the next stage rather than onto the reel: the script it just wrote is the
            // thing that was asked for, and what to do with it is storyboard it.
            if (created) void openReel(created, "storyboard");
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
        case "activity": {
          const { job_id: jobId, event: activityEvent } = event;
          setLiveActivity((current) => {
            const list = [...(current[jobId] ?? [])];
            const index = list.findIndex((item) => item.id === activityEvent.id);
            if (index >= 0) list[index] = activityEvent;
            else list.push(activityEvent);
            return { ...current, [jobId]: list };
          });
          setJobs((current) => {
            const job = current[jobId];
            if (!job) return current;
            const activity = [...(job.activity ?? [])];
            const index = activity.findIndex((item) => item.id === activityEvent.id);
            if (index >= 0) activity[index] = activityEvent;
            else activity.push(activityEvent);
            return { ...current, [jobId]: { ...job, activity } };
          });
          break;
        }
      }
    };
    source.onerror = () => setError("lost the studio event stream; is studio.py running?");
    return () => source.close();
  }, [openReel, refreshBoard, refreshReels]);

  const refreshStatus = useCallback(async () => {
    try {
      const status = await api.status();
      setAuthOk(status.auth);
      if (status.rate_per_second > 0) setRatePerSecond(status.rate_per_second);
      setStillsBackend(status.stills?.backend ?? "none");
      setModel({
        model: status.language?.model ?? "",
        ready: status.language?.ready ?? false,
      });
    } catch {
      setAuthOk(true);
    }
  }, []);

  // Re-probed whenever a job settles, not only at boot: the image server is a separate
  // process the user starts and stops, so a studio left open across a `make images` would
  // otherwise keep warning about a quota that stopped applying an hour ago.
  useEffect(() => {
    void refreshReels();
    void refreshStatus();
  }, [refreshReels, refreshStatus]);

  const activeJob = useMemo(
    () => Object.values(jobs).find((job) => job.state === "running") ?? null,
    [jobs],
  );

  const agentJob = useMemo(() => {
    if (!slug) return null;
    return (
      Object.values(jobs).find(
        (job) =>
          job.slug === slug &&
          job.state === "running" &&
          (job.kind === "chat" || job.kind === "crew" || job.kind === "agent"),
      ) ?? null
    );
  }, [jobs, slug]);

  const agentActivity = useMemo(() => {
    if (!agentJob) return [];
    return liveActivity[agentJob.id] ?? agentJob.activity ?? [];
  }, [agentJob, liveActivity]);

  useEffect(() => {
    if (!activeJob) void refreshStatus();
  }, [activeJob, refreshStatus]);

  /** The container clock, interpolated between heartbeats. */
  const liveSeconds =
    container.state === "cold"
      ? 0
      : container.live_seconds + (now - stampedAt.current) / 1000;
  const sessionCost =
    container.session_cost +
    (container.state === "cold" ? 0 : ((now - stampedAt.current) / 1000) * ratePerSecond);

  const guard = useCallback(async (work: () => Promise<unknown>) => {
    try {
      await work();
      setError(null);
    } catch (problem) {
      setError(String(problem));
    }
  }, []);

  /**
   * Soft-delete a reel (moves to `.trash/`). If it is the one on screen, go home so the shell
   * is not left holding a board whose directory no longer exists.
   */
  const trashReel = useCallback(
    async (target: string) => {
      await guard(async () => {
        await api.deleteReel(target);
        if (target === slugRef.current) await go(null);
        await refreshReels();
      });
    },
    [go, guard, refreshReels],
  );

  return {
    reels,
    slug,
    stage,
    /** What the shell should actually render: the URL's stage, or the board's own answer. */
    resolvedStage: stage ?? resolveStage(board),
    board,
    chat,
    jobs,
    activeJob,
    agentJob,
    agentActivity,
    liveActivity,
    container,
    liveSeconds,
    sessionCost,
    log,
    error,
    selection,
    renderSelection,
    expanded,
    stagingOpen,
    stagingPick,
    authOk,
    stillsBackend,
    model,
    setSelection,
    setRenderSelection,
    // The URL-syncing one, deliberately under the plain setter's name: every call site means
    // "show this scene", and every one of them should be linkable.
    setExpanded: openShot,
    setStagingOpen,
    setStagingPick,
    setError,
    go,
    goStage,
    openReel,
    trashReel,
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

/**
 * Is a job of this kind in flight for the thing `match` describes?
 *
 * The same four lines — kind, slug, queued-or-running, then something about `detail` — were
 * written out in four components before this, and the picture work would have made it six. The
 * slug is always the current board's: a job on another reel cannot make a control here busy.
 */
export function useBusy(
  kind: Job["kind"],
  match: (detail: Record<string, unknown>) => boolean,
): boolean {
  const studio = useStudio();
  const slug = studio.board?.slug;
  return Object.values(studio.jobs).some(
    (job) =>
      job.kind === kind &&
      job.slug === slug &&
      (job.state === "queued" || job.state === "running") &&
      match(job.detail),
  );
}
