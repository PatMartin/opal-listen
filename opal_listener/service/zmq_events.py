"""ZeroMQ event-bus publisher used by the server to announce recognition events (e.g. intent matches).

This is independent of the SSE `EventBus` in events.py: it publishes onto an external ZeroMQ PUB/SUB
(or XPUB/XSUB broker) endpoint shared with other OPAL suite services.

Wire protocol (opal-event `zeromq_protocol.md`): every message is exactly two frames — the UTF-8
topic (matching `TOPIC_PATTERN`) and a UTF-8 JSON event envelope whose `topic` equals frame 1 and
whose `data` carries the payload. Producers connect PUB to the broker's XSUB "publishers" endpoint
(default tcp://127.0.0.1:5555); the combined frames must not exceed MAX_EVENT_BYTES.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Protocol

from opal_listener.service.event_envelope import build_event_envelope

log = logging.getLogger(__name__)

# opal-event wire rules (see opal-event specs/zeromq_protocol.md and parse_and_normalize).
TOPIC_PATTERN = r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*$"
TOPIC_MAX_LENGTH = 255
MAX_EVENT_BYTES = 1_048_576


class EventPublisher(Protocol):
    def connect(self) -> None: ...

    def publish(self, topic: str, payload: dict[str, Any]) -> None: ...

    def _record_failure(self, message: str) -> None:
        with self._lock:
            self.delivery_failures += 1
            self.last_error = message
        log.warning(message)

    def stats(self) -> dict[str, float]:
        """Delivery accounting surfaced in PipelineStatus.metrics (PUB is fire-and-forget;
        `events_delivered` counts frames accepted by the socket, not confirmed receipts)."""
        return {
            "events_accepted": float(self.accepted),
            "events_delivered": float(self.delivered),
            "event_delivery_failures": float(self.delivery_failures),
        }

    def close(self) -> None: ...


class ZmqEventPublisher:
    """Publishes JSON-encoded events to a ZeroMQ PUB socket connected to `xpub_url`.

    `connect()` is called eagerly by `ListenerService.startup()` so the TCP connection and
    subscription handshake have the whole server-startup window to settle — avoiding the classic
    ZeroMQ "slow joiner" problem (where messages sent immediately after `connect()` can be
    silently dropped) for the very first real event, unlike the CLI's one-shot `event publish`,
    which instead sleeps briefly before its single send. `publish()` also connects lazily if
    `connect()` was never called. Publish failures are logged and swallowed: event delivery is
    best-effort and must never break recognition.
    """

    def __init__(self, xpub_url: str) -> None:
        self._xpub_url = xpub_url
        self._lock = threading.Lock()
        self._context: Any = None
        self._socket: Any = None
        # Observable delivery accounting. ZMQ PUB is fire-and-forget: 'delivered' means the
        # frames were handed to the socket, not that a subscriber received them.
        self.accepted = 0
        self.delivered = 0
        self.delivery_failures = 0
        self.last_error: str | None = None

    def connect(self) -> None:
        with self._lock:
            self._ensure_socket()

    def _ensure_socket(self) -> Any:
        if self._socket is None:
            import zmq

            self._context = zmq.Context()
            self._socket = self._context.socket(zmq.PUB)
            self._socket.connect(self._xpub_url)
        return self._socket

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            envelope = build_event_envelope(topic, payload)
            frames = [topic.encode("utf-8"), json.dumps(envelope, default=str).encode("utf-8")]
            if sum(len(frame) for frame in frames) > MAX_EVENT_BYTES:
                self._record_failure(f"dropping event '{topic}': exceeds {MAX_EVENT_BYTES} bytes")
                return
            with self._lock:
                socket = self._ensure_socket()
                log.info("sending ZeroMQ event to topic '%s' via %s: %s", topic, self._xpub_url, envelope)
                self.accepted += 1
                socket.send_multipart(frames)
                self.delivered += 1  # handed to the socket; subscriber receipt is unverifiable
        except Exception as exc:  # noqa: BLE001 - event delivery must never break recognition
            self._record_failure(f"failed to publish event '{topic}' to {self._xpub_url}: {exc!r}")

    def _record_failure(self, message: str) -> None:
        with self._lock:
            self.delivery_failures += 1
            self.last_error = message
        log.warning(message)

    def stats(self) -> dict[str, float]:
        """Delivery accounting surfaced in PipelineStatus.metrics (PUB is fire-and-forget;
        `events_delivered` counts frames accepted by the socket, not confirmed receipts)."""
        return {
            "events_accepted": float(self.accepted),
            "events_delivered": float(self.delivered),
            "event_delivery_failures": float(self.delivery_failures),
        }

    def close(self) -> None:
        with self._lock:
            if self._socket is not None:
                self._socket.close(linger=0)
                self._socket = None
            if self._context is not None:
                self._context.term()
                self._context = None
