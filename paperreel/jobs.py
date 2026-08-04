"""One worker, one container, one event stream.

Long operations (planning, asset generation, rendering) run here as jobs so the HTTP layer
can answer immediately and the browser can watch via SSE. Jobs are serialised deliberately:
there is exactly one GPU container, ComfyUI executes one graph at a time, and chaining is
serial by construction. A queue is the honest model, not a limitation to engineer around.

The container clock starts at deploy and stops at teardown -- not at the first sampling
step -- because that is when Modal starts and stops billing. Showing anything narrower
would under-report the cost.
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config

MAX_LOG_LINES = 400  # per job, kept in memory for late-joining browsers


@dataclass
class Job:
    id: str
    kind: str
    slug: str
    detail: dict = field(default_factory=dict)
    state: str = "queued"  # queued | running | done | error | cancelled
    log: list[str] = field(default_factory=list)
    error: str | None = None
    result: dict | None = None
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    # Live render telemetry, replaced wholesale as beats progress.
    phase: str = "waiting"
    beat: int | None = None
    beat_index: int = 0
    beat_total: int = 0
    step: int = 0
    step_max: int = 0
    beat_started_at: float | None = None
    cancelling: bool = False

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "slug": self.slug,
            "detail": self.detail,
            "state": self.state,
            "error": self.error,
            "result": self.result,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "phase": self.phase,
            "beat": self.beat,
            "beat_index": self.beat_index,
            "beat_total": self.beat_total,
            "step": self.step,
            "step_max": self.step_max,
            "beat_started_at": self.beat_started_at,
            "cancelling": self.cancelling,
            "log": self.log[-MAX_LOG_LINES:],
        }


class Container:
    """Tracks GPU lifetime, because that is what the bill is measured against."""

    def __init__(self) -> None:
        self.state = "cold"  # cold | deploying | warm | stopping
        self.since: float | None = None
        self.billed_seconds = 0.0  # completed lifetimes this process

    def mark(self, state: str) -> None:
        if state == "deploying" and self.since is None:
            self.since = time.monotonic()
        if state == "cold" and self.since is not None:
            self.billed_seconds += time.monotonic() - self.since
            self.since = None
        self.state = state

    @property
    def live_seconds(self) -> float:
        return 0.0 if self.since is None else time.monotonic() - self.since

    def to_json(self) -> dict:
        seconds = self.billed_seconds + self.live_seconds
        return {
            "state": self.state,
            "live_seconds": round(self.live_seconds, 1),
            "session_seconds": round(seconds, 1),
            "session_cost": round(config.estimate_cost(seconds), 4),
        }


class Runner:
    """Job queue, event fan-out, and container bookkeeping."""

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.container = Container()
        self.handlers: dict[str, Callable[[Job, Runner], dict | None]] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._subscribers: list[queue.Queue[dict]] = []
        self._lock = threading.Lock()
        self._sequence = itertools.count()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    # ## Events

    def subscribe(self) -> queue.Queue[dict]:
        channel: queue.Queue[dict] = queue.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.append(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue[dict]) -> None:
        with self._lock:
            if channel in self._subscribers:
                self._subscribers.remove(channel)

    def publish(self, event: dict) -> None:
        event = {"seq": next(self._sequence), "at": time.time(), **event}
        with self._lock:
            channels = list(self._subscribers)
        for channel in channels:
            try:
                channel.put_nowait(event)
            except queue.Full:
                pass  # a browser that cannot keep up refetches on reconnect

    def publish_job(self, job: Job) -> None:
        self.publish({"type": "job", "job": job.to_json()})

    def publish_board(self, slug: str) -> None:
        self.publish({"type": "board", "slug": slug})

    def publish_container(self) -> None:
        self.publish({"type": "container", "container": self.container.to_json()})

    # ## Submitting

    def register(self, kind: str, handler: Callable[[Job, Runner], dict | None]) -> None:
        self.handlers[kind] = handler

    def submit(self, kind: str, slug: str, detail: dict | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, slug=slug, detail=detail or {})
        self.jobs[job.id] = job
        self.order.append(job.id)
        self._queue.put(job.id)
        self.publish_job(job)
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.jobs[job_id]
        if job.state == "queued":
            job.state = "cancelled"
            job.finished_at = time.time()
        elif job.state == "running":
            # Cooperative: the render loop checks this between beats, and the API layer
            # interrupts ComfyUI so an in-flight graph does not write a partial file.
            job.cancelling = True
            job.log.append("[cancel] requested")
        self.publish_job(job)
        return job

    def active(self) -> Job | None:
        for job_id in reversed(self.order):
            if self.jobs[job_id].state == "running":
                return self.jobs[job_id]
        return None

    def recent(self, limit: int = 20) -> list[dict]:
        return [self.jobs[i].to_json() for i in self.order[-limit:]]

    # ## Worker

    def log(self, job: Job, line: str) -> None:
        job.log.append(line)
        del job.log[:-MAX_LOG_LINES]
        self.publish({"type": "log", "job_id": job.id, "line": line})

    def update(self, job: Job, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(job, key, value)
        self.publish_job(job)

    def _run(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.jobs[job_id]
            if job.state == "cancelled":
                continue
            handler = self.handlers.get(job.kind)
            if handler is None:
                job.state = "error"
                job.error = f"no handler for {job.kind}"
                self.publish_job(job)
                continue

            job.state = "running"
            job.started_at = time.time()
            self.publish_job(job)
            try:
                job.result = handler(job, self)
                job.state = "cancelled" if job.cancelling else "done"
            except Exception as error:  # noqa: BLE001 - surfaced to the browser verbatim
                job.state = "error"
                job.error = f"{type(error).__name__}: {error}"
                self.log(job, f"[error] {job.error}")
            finally:
                job.finished_at = time.time()
                job.phase = job.state
                self.publish_job(job)
                self.publish_board(job.slug)
                self.publish_container()


runner = Runner()
