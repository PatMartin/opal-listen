"""Thread-safe fan-out of pipeline events to asyncio subscribers (SSE clients).

Backpressure policy (every subscriber queue is bounded at ``max_queue``):

- Incoming event is **disposable** (`debug_only`: audio levels, partial transcripts): on a
  full queue the oldest *disposable* message is evicted; if the queue holds only important
  events the incoming disposable is dropped instead — diagnostics never evict results.
- Incoming event is **important** (`result`, `status`, …): the oldest disposable message is
  evicted first; an all-important full queue drops its oldest entry (the stream is
  observational — a hopelessly stalled consumer loses history, bounded, and is told).

Drops are counted per subscriber and in ``dropped_total`` (surfaced in
``PipelineStatus.metrics`` as ``sse_dropped_events``), and a synthetic ``dropped`` event is
injected into the stream at the next opportunity so the client can observe its own loss.

``publish`` never blocks: delivery is scheduled on each subscriber's loop, so a slow or
disconnected consumer cannot interfere with audio capture.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 200) -> None:
        self._subscribers: dict[asyncio.Queue[dict[str, Any]], tuple[asyncio.AbstractEventLoop, bool]] = {}
        self._dropped: dict[asyncio.Queue[dict[str, Any]], int] = {}
        self._reported: dict[asyncio.Queue[dict[str, Any]], int] = {}
        self._lock = threading.Lock()
        self._max_queue = max_queue
        self.dropped_total = 0

    def subscribe(self, debug: bool = False) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subscribers[queue] = (asyncio.get_running_loop(), debug)
            self._dropped[queue] = 0
            self._reported[queue] = 0
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)
            self._dropped.pop(queue, None)
            self._reported.pop(queue, None)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: str, data: dict[str, Any], debug_only: bool = False) -> None:
        message = {"event": event, "data": data, "disposable": debug_only}
        with self._lock:
            targets = list(self._subscribers.items())
        for queue, (loop, debug) in targets:
            if debug_only and not debug:
                continue
            with contextlib.suppress(RuntimeError):  # loop closed
                loop.call_soon_threadsafe(self._put, queue, message)

    def _drain(self, queue: asyncio.Queue[dict[str, Any]]) -> list[dict[str, Any]]:
        items = []
        while True:
            try:
                items.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                return items

    def _requeue(self, queue: asyncio.Queue[dict[str, Any]], items: list[dict[str, Any]]) -> None:
        for m in items:
            try:
                queue.put_nowait(m)
            except asyncio.QueueFull:  # consumer raced the drain — count the loss
                self._note_drop(queue)

    def _note_drop(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if queue in self._dropped:
                self._dropped[queue] += 1
        self.dropped_total += 1

    def _put(self, queue: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
        """Enqueue `message` under the bounded drop policy. Runs on the subscriber's loop."""
        if queue.full():
            items = self._drain(queue)
            evict_at: int | None = next((i for i, m in enumerate(items) if m.get("disposable")), None)
            if evict_at is None and message.get("disposable"):
                # all-important queue: a diagnostic must not evict a result — drop incoming
                self._requeue(queue, items)
                self._note_drop(queue)
                return
            items.pop(evict_at if evict_at is not None else 0)
            self._requeue(queue, items)
            self._note_drop(queue)
        with contextlib.suppress(asyncio.QueueFull):  # consumer raced us; message lost, not fatal
            queue.put_nowait(message)
        # inject the cumulative drop notice when there is room: a disposable event itself,
        # so it may be evicted by later events without harm
        dropped = self._dropped.get(queue, 0)
        if dropped > self._reported.get(queue, 0):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait({"event": "dropped", "data": {"dropped": dropped}, "disposable": True})
                self._reported[queue] = dropped
