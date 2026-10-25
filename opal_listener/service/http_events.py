"""HTTP event-bus publisher used by the server to announce recognition events to opal-event via REST.

Implements the EventPublisher protocol and dispatches events via HTTP POST to opal-event's
`/api/v1/events/` endpoint using an asynchronous worker queue so that network latency or
opal-event downtime never blocks recognition or audio capture.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from opal_listener.service.opal_context import (
    CAUSATION_HEADER,
    REQUEST_HEADER,
    SESSION_HEADER,
    TRANSACTION_HEADER,
    new_opaque_id,
)
from opal_listener.service.zmq_events import EventPublisher

log = logging.getLogger(__name__)

DEFAULT_QUEUE_CAPACITY = 1000
DEFAULT_HTTP_TIMEOUT = 5.0
DEFAULT_SOURCE = "opal-listen"
SCHEMA_VERSION = "2.0"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(maxsize=queue_capacity)
        self._worker_thread: threading.Thread | None = None
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    def connect(self) -> None:
        with self._lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self._stopped.clear()
                self._client = httpx.Client(base_url=self._base_url, timeout=self._timeout)
                self._worker_thread = threading.Thread(
                    target=self._worker_loop,
                    name="http-event-publisher",
                    daemon=True,
                )
                self._worker_thread.start()

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        with self._lock:
            if self._worker_thread is None or not self._worker_thread.is_alive():
                self.connect()

        try:
            self._queue.put_nowait((topic, payload))
        except queue.Full:
            # drop_newest policy: log warning and drop to never block audio/recognition
            log.warning("HTTP event publisher queue is full (%d items); dropping event for %s", self._queue.maxsize, topic)

    def _worker_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                item = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue

            if item is None:
                self._queue.task_done()
                break

            topic, payload = item
            try:
                self._send_event(topic, payload)
            except Exception:  # noqa: BLE001 - event delivery must never crash the worker thread
                log.warning("failed to send event '%s' to %s", topic, self._base_url, exc_info=True)
            finally:
                self._queue.task_done()

    def _send_event(self, topic: str, payload: dict[str, Any]) -> None:
        client = self._client
        if client is None:
            return

        session_id = payload.get("session_id")
        transaction_id = new_opaque_id()
        request_id = new_opaque_id()

        # Build OPAL v1.3 EventContext metadata
        metadata: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "transaction_id": transaction_id,
            "request_id": request_id,
            "causation_id": None,
            "depth": 0,
        }

        # Combine EventContext metadata and recognition result payload into data object
        data_payload = {
            "metadata": metadata,
            **payload,
        }

        event_input = {
            "id": str(uuid.uuid4()),
            "topic": topic,
            "source": DEFAULT_SOURCE,
            "timestamp": _now_iso(),
            "data": data_payload,
        }

        headers: dict[str, str] = {
            TRANSACTION_HEADER: transaction_id,
            REQUEST_HEADER: request_id,
        }
        if session_id:
            headers[SESSION_HEADER] = str(session_id)
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        log.info("sending HTTP event to topic '%s' at %s/api/v1/events/: %s", topic, self._base_url, event_input)
        response = client.post("/api/v1/events/", json=event_input, headers=headers)
        if response.status_code >= 400:
            log.warning(
                "opal-event POST /api/v1/events/ returned status %d: %s",
                response.status_code,
                response.text,
            )

    def close(self) -> None:
        self._stopped.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

        with self._lock:
            worker = self._worker_thread
            client = self._client
            self._worker_thread = None
            self._client = None

        if worker and worker.is_alive():
            worker.join(timeout=2.0)

        if client:
            client.close()


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
