"""Unit tests for HttpEventPublisher (sending matched intent events to opal-event via REST)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from opal_listener.app_settings import AppSettings
from opal_listener.service.event_envelope import DEFAULT_SOURCE, SCHEMA_VERSION
from opal_listener.service.http_events import HttpEventPublisher
from opal_listener.service.listener_service import default_event_publisher
from opal_listener.service.opal_context import (
    REQUEST_HEADER,
    SESSION_HEADER,
    TRANSACTION_HEADER,
)
from opal_listener.service.zmq_events import ZmqEventPublisher


def _wait_until(predicate: Any, timeout: float = 2.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_http_event_publisher_publishes_event_successfully() -> None:
    received_requests: list[httpx.Request] = []
    received_bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_requests.append(request)
        received_bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(202, json={"id": "rec-1", "status": "accepted", "published_at": "2026-10-21T00:00:00Z"})

    transport = httpx.MockTransport(handler)
    publisher = HttpEventPublisher(base_url="http://mock-opal-event:8080", token="secret-token")
    publisher.connect()
    # Inject mock client using transport
    publisher._client = httpx.Client(base_url="http://mock-opal-event:8080", transport=transport)

    sample_payload = {
        "session_id": "test-session-123",
        "intent_name": "light_turn_on",
        "matched": True,
        "transcript": "turn on the light",
        "slot_values": {"room": "kitchen"},
    }

    try:
        publisher.publish("opal.listen.intent.match", sample_payload)
        assert _wait_until(lambda: len(received_requests) == 1), "Event was not received by mock server"

        req = received_requests[0]
        assert req.url.path == "/api/v1/events/"
        assert req.headers["Authorization"] == "Bearer secret-token"
        assert req.headers[SESSION_HEADER] == "test-session-123"
        assert TRANSACTION_HEADER in req.headers
        assert REQUEST_HEADER in req.headers

        body = received_bodies[0]
        assert body["topic"] == "opal.listen.intent.match"
        assert body["source"] == DEFAULT_SOURCE
        assert "id" in body and "timestamp" in body

        data = body["data"]
        assert data["intent_name"] == "light_turn_on"
        assert data["matched"] is True

        metadata = data["metadata"]
        assert metadata["schema_version"] == SCHEMA_VERSION
        assert metadata["session_id"] == "test-session-123"
        assert metadata["transaction_id"] == req.headers[TRANSACTION_HEADER]
        assert metadata["request_id"] == req.headers[REQUEST_HEADER]
        assert metadata["depth"] == 0
    finally:
        publisher.close()


def test_http_event_publisher_handles_server_errors_gracefully() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal error"})

    transport = httpx.MockTransport(handler)
    publisher = HttpEventPublisher(base_url="http://mock-opal-event:8080")
    publisher.connect()
    publisher._client = httpx.Client(base_url="http://mock-opal-event:8080", transport=transport)

    try:
        # Should not raise any exception
        publisher.publish("opal.listen.intent.match", {"intent_name": "light_turn_on", "matched": True})
        time.sleep(0.3)
    finally:
        publisher.close()


def test_http_event_publisher_handles_network_failure_gracefully() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    publisher = HttpEventPublisher(base_url="http://mock-opal-event:8080")
    publisher.connect()
    publisher._client = httpx.Client(base_url="http://mock-opal-event:8080", transport=transport)

    try:
        # Should not raise any exception
        publisher.publish("opal.listen.intent.match", {"intent_name": "light_turn_on", "matched": True})
        time.sleep(0.3)
    finally:
        publisher.close()


def _mock_publisher(
    handler: Callable[[httpx.Request], httpx.Response], **kwargs: Any
) -> HttpEventPublisher:
    """Publisher whose HTTP client talks to a MockTransport (injected before the worker starts)."""
    publisher = HttpEventPublisher(base_url="http://mock-opal-event:8080", **kwargs)
    publisher._client = httpx.Client(
        base_url="http://mock-opal-event:8080", transport=httpx.MockTransport(handler)
    )
    return publisher


def _ok_handler(received: list[httpx.Request], delay: float = 0.0) -> Callable:
    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        if delay:
            time.sleep(delay)
        return httpx.Response(202, json={"id": "rec-1", "status": "accepted"})

    return handler


def test_publish_without_connect_lazily_starts_worker() -> None:
    received: list[httpx.Request] = []
    publisher = _mock_publisher(_ok_handler(received))
    try:
        publisher.publish("opal.listen.intent.match", {"intent_name": "light_turn_on"})
        assert _wait_until(lambda: len(received) == 1), "lazy-connected worker did not deliver"
        assert publisher._worker_thread is not None and publisher._worker_thread.is_alive()
    finally:
        publisher.close()


def test_concurrent_publishes_use_a_single_worker() -> None:
    received: list[httpx.Request] = []
    publisher = _mock_publisher(_ok_handler(received))
    seen_workers: list[threading.Thread | None] = []
    try:
        threads = [
            threading.Thread(
                target=lambda i=i: (publisher.publish("t", {"i": i}), seen_workers.append(publisher._worker_thread))
            )
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert _wait_until(lambda: len(received) == 8, timeout=5)
        assert len({w for w in seen_workers if w is not None}) == 1
    finally:
        publisher.close()


def test_connect_is_idempotent() -> None:
    publisher = _mock_publisher(_ok_handler([]))
    try:
        publisher.connect()
        first = publisher._worker_thread
        publisher.connect()
        assert publisher._worker_thread is first and first is not None and first.is_alive()
    finally:
        publisher.close()


def test_close_drains_queued_events() -> None:
    received: list[httpx.Request] = []
    publisher = _mock_publisher(_ok_handler(received, delay=0.05))
    for i in range(5):
        publisher.publish("t", {"i": i})
    publisher.close()
    assert len(received) == 5
    assert publisher._worker_thread is None
    assert publisher._client is None


def test_close_during_slow_request_reports_incomplete_shutdown() -> None:
    started = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        time.sleep(3.0)
        return httpx.Response(202, json={})

    publisher = _mock_publisher(handler)
    publisher.publish("t", {})
    assert started.wait(timeout=2), "worker never picked up the request"
    begin = time.monotonic()
    publisher.close()
    assert time.monotonic() - begin < 3.0, "close() waited for the whole in-flight request"
    worker = publisher._worker_thread
    assert worker is not None and worker.is_alive()
    assert publisher.last_error is not None
    # once the request completes the worker retires itself and closes the client
    assert _wait_until(lambda: publisher._worker_thread is None, timeout=5)
    assert publisher._client is None


def test_connection_refused_records_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    publisher = _mock_publisher(handler)
    try:
        publisher.publish("t", {})
        assert _wait_until(lambda: publisher.delivery_failures == 1)
        assert "failed to send" in (publisher.last_error or "")
        assert publisher._worker_thread is not None and publisher._worker_thread.is_alive()
    finally:
        publisher.close()


def test_http_500_records_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    publisher = _mock_publisher(handler)
    try:
        publisher.publish("t", {})
        assert _wait_until(lambda: publisher.delivery_failures == 1)
        assert "500" in (publisher.last_error or "")
    finally:
        publisher.close()


def test_full_queue_drops_newest_and_records_failures() -> None:
    started = threading.Event()
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        started.set()
        time.sleep(0.4)
        return httpx.Response(202, json={})

    publisher = _mock_publisher(handler, queue_capacity=2)
    try:
        publisher.publish("t", {"i": 0})
        assert started.wait(timeout=2), "worker never picked up the first request"
        for i in range(1, 5):
            publisher.publish("t", {"i": i})  # 2 queued, 2 dropped while the worker is busy
        assert publisher.delivery_failures == 2
        assert _wait_until(lambda: len(received) == 3, timeout=5)
    finally:
        publisher.close()


def test_publish_after_close_reconnects() -> None:
    received: list[httpx.Request] = []
    publisher = _mock_publisher(_ok_handler(received))
    publisher.publish("t", {"i": 0})
    assert _wait_until(lambda: len(received) == 1)
    publisher.close()
    assert publisher._worker_thread is None
    # inject a fresh mock client (the old one was closed by the retiring worker)
    publisher._client = httpx.Client(
        base_url="http://mock-opal-event:8080", transport=httpx.MockTransport(_ok_handler(received))
    )
    try:
        publisher.publish("t", {"i": 1})
        assert _wait_until(lambda: len(received) == 2)
        assert publisher._worker_thread is not None and publisher._worker_thread.is_alive()
    finally:
        publisher.close()


def test_default_event_publisher_selection() -> None:
    settings_http = AppSettings(event_transport="http")
    pub_http = default_event_publisher(settings_http)
    assert isinstance(pub_http, HttpEventPublisher)

    settings_zmq = AppSettings(event_transport="zmq")
    pub_zmq = default_event_publisher(settings_zmq)
    assert isinstance(pub_zmq, ZmqEventPublisher)

    # 'both' is rejected: the same event would be delivered twice and opal-event does not
    # deduplicate across transports, so every match could execute an action twice.
    settings_both = AppSettings(event_transport="both")
    with pytest.raises(ValueError, match="does not deduplicate"):
        default_event_publisher(settings_both)
