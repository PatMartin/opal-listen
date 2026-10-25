"""ZeroMQ event-bus publisher used by the server to announce recognition events (e.g. intent matches).

This is independent of the SSE `EventBus` in events.py: it publishes onto an external ZeroMQ PUB/SUB
(or XPUB/XSUB broker) endpoint, the same bus `opal listen event publish` writes to from the CLI.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Protocol

log = logging.getLogger(__name__)


class EventPublisher(Protocol):
    def connect(self) -> None: ...

    def publish(self, topic: str, payload: dict[str, Any]) -> None: ...

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
            with self._lock:
                socket = self._ensure_socket()
                message = json.dumps(payload, default=str).encode("utf-8")
                log.info("sending ZeroMQ event to topic '%s' via %s: %s", topic, self._xpub_url, payload)
                socket.send_multipart([topic.encode("utf-8"), message])
        except Exception:  # noqa: BLE001 - event delivery must never break recognition
            log.warning("failed to publish event '%s' to %s", topic, self._xpub_url, exc_info=True)

    def close(self) -> None:
        with self._lock:
            if self._socket is not None:
                self._socket.close(linger=0)
                self._socket = None
            if self._context is not None:
                self._context.term()
                self._context = None
