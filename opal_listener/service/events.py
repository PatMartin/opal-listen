"""Thread-safe fan-out of pipeline events to asyncio subscribers (SSE clients)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from typing import Any


class EventBus:
    def __init__(self, max_queue: int = 200) -> None:
        self._subscribers: dict[asyncio.Queue[dict[str, Any]], tuple[asyncio.AbstractEventLoop, bool]] = {}
        self._lock = threading.Lock()
        self._max_queue = max_queue

    def subscribe(self, debug: bool = False) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subscribers[queue] = (asyncio.get_running_loop(), debug)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: str, data: dict[str, Any], debug_only: bool = False) -> None:
        message = {"event": event, "data": data}
        with self._lock:
            targets = list(self._subscribers.items())
        for queue, (loop, debug) in targets:
            if debug_only and not debug:
                continue
            with contextlib.suppress(RuntimeError):  # loop closed
                loop.call_soon_threadsafe(self._put, queue, message)

    @staticmethod
    def _put(queue: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(message)
