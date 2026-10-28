"""Bounded refinement worker: heavyweight STT refiners (e.g. faster-whisper) run off the
real-time audio capture loop.

Policy
- FIFO single worker: results finalize in capture order on the worker thread.
- Queue full: the command finalizes immediately on its draft transcript (degradation
  recorded as ``queue_full``).
- Timeout: a refine call exceeding the task snapshot's ``refiner_timeout_ms`` is abandoned;
  the stuck call is discarded by replacing the executor so later tasks are unaffected.
- Failure: any refiner error falls back to the draft transcript (degradation recorded).
- Shutdown: pending tasks drain within a bounded join; leftovers are discarded with a warning.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections.abc import Callable
from concurrent import futures
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opal_listener.service.providers.base import SttRefiner, Transcript

if TYPE_CHECKING:
    from opal_listener.service.pipeline import CommandOutcome
    from opal_listener.service.runtime_config import RuntimeSnapshot

log = logging.getLogger(__name__)

DEFAULT_QUEUE_CAPACITY = 8
DEFAULT_REFINE_TIMEOUT_MS = 5000
_SHUTDOWN_DRAIN_SECONDS = 5.0

FinalizeFn = Callable[["CommandOutcome", "RuntimeSnapshot", Transcript | None, str | None], None]
RefinerForFn = Callable[["RuntimeSnapshot"], SttRefiner | None]


@dataclass
class RefineTask:
    """One captured command plus the immutable snapshot it was recognized under."""

    outcome: CommandOutcome
    snapshot: RuntimeSnapshot


class RefinementWorker:
    def __init__(self, capacity: int, refiner_for: RefinerForFn, finalize: FinalizeFn) -> None:
        self._queue: queue.Queue[RefineTask | None] = queue.Queue(maxsize=capacity)
        self._refiner_for = refiner_for
        self._finalize = finalize
        self._thread: threading.Thread | None = None
        self._executor: futures.ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, task: RefineTask) -> bool:
        """Enqueue a captured command for refinement. Returns False when the queue is full."""
        with self._lock:
            if self._closed:
                return False
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="stt-refinement", daemon=True)
                self._thread.start()
        try:
            self._queue.put_nowait(task)
            return True
        except queue.Full:
            return False

    def _executor_submit(self, refiner: SttRefiner, task: RefineTask) -> futures.Future[Transcript]:
        if self._executor is None:
            self._executor = futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="refiner")
        return self._executor.submit(refiner.refine, task.outcome.pcm, task.outcome.transcript)

    def _reset_executor(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)  # abandon the stuck call; do not block the worker
        self._executor = futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="refiner")

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                return
            refined: Transcript | None = None
            error: str | None = None
            try:
                refiner = self._refiner_for(task.snapshot)
            except Exception as exc:
                refiner, error = None, f"refiner_unavailable: {exc}"
            if refiner is not None and task.outcome.transcript.text:
                timeout_s = int(task.snapshot.settings.get("refiner_timeout_ms", DEFAULT_REFINE_TIMEOUT_MS)) / 1000
                try:
                    refined = self._executor_submit(refiner, task).result(timeout=timeout_s)
                except futures.TimeoutError:
                    error = "timeout"
                    self._reset_executor()
                except Exception as exc:
                    error = f"refiner_error: {exc}"
            try:
                self._finalize(task.outcome, task.snapshot, refined, error)
            except Exception:
                log.exception("failed to finalize refined result")

    def close(self, timeout: float = _SHUTDOWN_DRAIN_SECONDS) -> None:
        """Drain pending tasks (bounded), then discard leftovers with a warning."""
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread is not None:
            with contextlib.suppress(queue.Full):  # sentinel just speeds up the drain
                self._queue.put(None, timeout=timeout)  # drains all tasks ahead of it
            thread.join(timeout=timeout)
            if thread.is_alive() or self._queue.qsize():
                log.warning(
                    "refinement worker shutdown incomplete; %d pending tasks discarded",
                    self._queue.qsize(),
                )
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
