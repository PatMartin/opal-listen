"""Unit tests for HttpEventPublisher (sending matched intent events to opal-event via REST)."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from opal_listener.app_settings import AppSettings
from opal_listener.service.http_events import (
    DEFAULT_SOURCE,
    SCHEMA_VERSION,
    CompositeEventPublisher,
    HttpEventPublisher,
)
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


def test_default_event_publisher_selection() -> None:
    settings_http = AppSettings(event_transport="http")
    pub_http = default_event_publisher(settings_http)
    assert isinstance(pub_http, HttpEventPublisher)

    settings_zmq = AppSettings(event_transport="zmq")
    pub_zmq = default_event_publisher(settings_zmq)
    assert isinstance(pub_zmq, ZmqEventPublisher)

    settings_both = AppSettings(event_transport="both")
    pub_both = default_event_publisher(settings_both)
    assert isinstance(pub_both, CompositeEventPublisher)
