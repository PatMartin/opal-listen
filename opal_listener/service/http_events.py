"""HTTP event-bus publisher used by the server to announce recognition events to opal-event via REST.

Implements the EventPublisher protocol and dispatches events via HTTP POST to opal-event's
`/api/v1/events/` endpoint using an asynchronous worker queue so that network latency or
opal-event downtime never blocks recognition or audio capture.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

import httpx

from opal_listener.service.event_envelope import build_event_envelope
from opal_listener.service.opal_context import (
    REQUEST_HEADER,
    SESSION_HEADER,
    TRANSACTION_HEADER,
)
from opal_listener.service.zmq_events import EventPublisher

log = logging.getLogger(__name__)

DEFAULT_QUEUE_CAPACITY = 1000
DEFAULT_HTTP_TIMEOUT = 5.0


class HttpEventPublisher:
    """Publishes JSON events to opal-event REST API (/api/v1/events/) via a background queue worker.

    Conforms to the EventPublisher protocol.
    Publishes an EventInput envelope containing:
      - id: UUID
      - topic: topic string (e.g. opal.listen.intent.match)
      - source: "opal-listen"
      - timestamp: RFC3339 UTC
      - data: dict containing OPAL v1.3 EventContext metadata and recognition result payload.

    Also attaches standard OPAL correlation headers (X-Opal-*).
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        token: str | None = None,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=queue_capacity)
        self._worker_thread: threading.Thread | None = None
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        # Observable delivery accounting: failures are counted and the last one kept for inspection.
        self.delivery_failures = 0
        self.last_error: str | None = None

    def connect(self) -> None:
        """Start the delivery worker. Idempotent; also used to reconnect after close()."""
        with self._lock:
            self._start_worker_locked()

    def _start_worker_locked(self) -> None:
        """Create the client/worker if none is running. Caller must hold self._lock."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._stopped.clear()
        if self._client is None:
            self._client = httpx.Client(base_url=self._base_url, timeout=self._timeout)
        worker = threading.Thread(target=self._worker_loop, name="http-event-publisher", daemon=True)
        self._worker_thread = worker
        try:
            worker.start()
        except Exception:
            self._worker_thread = None
            if self._client is not None:
                self._client.close()
                self._client = None
            raise

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        # Worker check and enqueue happen under the same lock: a publish racing a worker shutdown
        # either attaches to the live worker or starts a new one — never deadlocks or spawns a
        # second worker. put_nowait means audio/recognition never blocks on HTTP latency.
        with self._lock:
            self._start_worker_locked()
            try:
                self._queue.put_nowait((topic, payload))
            except queue.Full:
                # drop_newest policy: count, warn and drop to never block audio/recognition
                self.delivery_failures += 1
                self.last_error = f"queue full ({self._queue.maxsize}); dropped event for '{topic}'"
                log.warning(self.last_error)

    def _worker_loop(self) -> None:
        try:
            while True:
                try:
                    topic, payload = self._queue.get(timeout=0.25)
                except queue.Empty:
                    if self._stopped.is_set():
                        return
                    continue
                try:
                    self._deliver(topic, payload)
                finally:
                    self._queue.task_done()
        finally:
            self._retire_worker()

    def _retire_worker(self) -> None:
        """Worker exit: unregister and close the client. Only ever runs in the worker thread, so
        the HTTP client is never closed while a request is in flight."""
        with self._lock:
            client = self._client
            self._client = None
            if self._worker_thread is threading.current_thread():
                self._worker_thread = None
        if client is not None:
            client.close()

    def _deliver(self, topic: str, payload: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            self._record_failure(f"no active HTTP client; dropped event for '{topic}'")
            return
        try:
            self._send_event(client, topic, payload)
        except Exception as exc:  # noqa: BLE001 - delivery failure must never kill the worker
            self._record_failure(f"failed to send event '{topic}' to {self._base_url}: {exc!r}")

    def _record_failure(self, message: str) -> None:
        with self._lock:
            self.delivery_failures += 1
            self.last_error = message
        log.warning(message)

    def _send_event(self, client: httpx.Client, topic: str, payload: dict[str, Any]) -> None:
        event_input = build_event_envelope(topic, payload)
        # The HTTP transport also mirrors the envelope's OPAL correlation ids as request headers.
        metadata = event_input["data"]["metadata"]
        headers: dict[str, str] = {
            TRANSACTION_HEADER: metadata["transaction_id"],
            REQUEST_HEADER: metadata["request_id"],
        }
        if metadata["session_id"]:
            headers[SESSION_HEADER] = str(metadata["session_id"])
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        log.info("sending HTTP event to topic '%s' at %s/api/v1/events/: %s", topic, self._base_url, event_input)
        response = client.post("/api/v1/events/", json=event_input, headers=headers)
        if response.status_code >= 400:
            self._record_failure(
                f"opal-event POST /api/v1/events/ returned status {response.status_code}: {response.text[:200]}"
            )

    def close(self) -> None:
        """Signal the worker to drain and stop, then wait briefly. If it is still running (e.g.
        inside a slow request) the shutdown is reported via last_error/log and the worker closes
        the client itself on exit — the client is never closed underneath it."""
        self._stopped.set()
        with self._lock:
            worker = self._worker_thread
        if worker is None:
            return
        worker.join(timeout=2.0)
        if worker.is_alive():
            message = "HTTP event publisher worker did not stop within 2s; delivery continues in background"
            with self._lock:
                self.last_error = message
            log.warning(message)


class CompositeEventPublisher:
    """Dispatches events across multiple EventPublisher instances (e.g. HTTP and ZeroMQ)."""

    def __init__(self, publishers: list[EventPublisher]) -> None:
        self._publishers = publishers

    def connect(self) -> None:
        for pub in self._publishers:
            pub.connect()

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        for pub in self._publishers:
            pub.publish(topic, payload)

    def close(self) -> None:
        for pub in self._publishers:
            pub.close()
