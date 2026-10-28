"""Unit tests for ZmqEventPublisher (the server-side ZeroMQ event bus publisher)."""

from __future__ import annotations

import contextlib
import importlib
import json
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import zmq

from opal_listener.service.event_envelope import DEFAULT_SOURCE, SCHEMA_VERSION
from opal_listener.service.zmq_events import MAX_EVENT_BYTES, ZmqEventPublisher


def _run_proxy(frontend: zmq.Socket, backend: zmq.Socket) -> None:
    with contextlib.suppress(zmq.ZMQError):  # raised when torn down at the end of the test
        zmq.proxy(frontend, backend)


@contextlib.contextmanager
def zmq_broker() -> Iterator[tuple[str, zmq.Socket]]:
    """A minimal opal-event-style broker: XSUB frontend (producers) + XPUB backend (consumers)
    with a proxy between them, plus one all-topics SUB connected to the XPUB side."""
    ctx = zmq.Context()
    xpub = ctx.socket(zmq.XPUB)
    xpub_port = xpub.bind_to_random_port("tcp://127.0.0.1")
    xsub = ctx.socket(zmq.XSUB)
    xsub_port = xsub.bind_to_random_port("tcp://127.0.0.1")
    threading.Thread(target=_run_proxy, args=(xpub, xsub), daemon=True).start()

    sub = ctx.socket(zmq.SUB)
    sub.connect(f"tcp://127.0.0.1:{xpub_port}")
    sub.setsockopt_string(zmq.SUBSCRIBE, "")
    try:
        yield f"tcp://127.0.0.1:{xsub_port}", sub
    finally:
        sub.close()
        xpub.close()
        xsub.close()
        ctx.term()


def _opal_event_broker_module():
    """Import the real opal-event wire parser from a sibling checkout, if it is available."""
    src = Path(__file__).resolve().parents[3] / "opal-event" / "src"
    if not src.is_dir():
        pytest.skip("opal-event checkout not found next to opal-listen")
    sys.path.insert(0, str(src))
    try:
        return importlib.import_module("opal_event.zeromq_broker")
    except ImportError as exc:
        pytest.skip(f"opal_event not importable: {exc}")
    finally:
        sys.path.remove(str(src))


def test_zmq_event_publisher_delivers_through_an_xpub_xsub_broker() -> None:
    """PUB connects to the broker's XSUB endpoint and a SUB receives the forwarded two-frame
    message through the XPUB endpoint, with frame 2 carrying the canonical event envelope."""
    with zmq_broker() as (publishers_url, sub):
        publisher = ZmqEventPublisher(publishers_url)
        try:
            # Mirror ListenerService.startup(): connect eagerly, then give the subscription time to
            # propagate through the proxy, so the test isn't racing the same "slow joiner" problem
            # that calling connect() early (instead of lazily on first publish) is meant to avoid.
            publisher.connect()
            time.sleep(0.3)
            publisher.publish(
                "opal.listen.intent.match", {"intent_name": "light_turn_on", "matched": True}
            )
            assert sub.poll(3000), "no message received through the XPUB/XSUB broker"
            topic, message = sub.recv_multipart()
        finally:
            publisher.close()

    assert topic == b"opal.listen.intent.match"
    envelope = json.loads(message)
    assert envelope["topic"] == "opal.listen.intent.match"
    assert envelope["source"] == DEFAULT_SOURCE
    assert envelope["id"] and envelope["timestamp"].endswith("Z")
    assert envelope["data"]["intent_name"] == "light_turn_on"
    assert envelope["data"]["matched"] is True
    assert envelope["data"]["metadata"]["schema_version"] == SCHEMA_VERSION


def test_published_envelope_passes_opal_event_parser() -> None:
    """The frames produced by ZmqEventPublisher validate against opal-event's parse_and_normalize
    (the authoritative observer input rules from zeromq_protocol.md)."""
    broker = _opal_event_broker_module()
    with zmq_broker() as (publishers_url, sub):
        publisher = ZmqEventPublisher(publishers_url)
        try:
            publisher.connect()
            time.sleep(0.3)
            publisher.publish(
                "opal.listen.intent.match",
                {"intent_name": "light_turn_on", "matched": True, "slot_values": {"room_name": "kitchen"}},
            )
            assert sub.poll(3000), "no message received through the XPUB/XSUB broker"
            frames = sub.recv_multipart()
        finally:
            publisher.close()

    normalized = broker.parse_and_normalize(frames[0], frames[1], MAX_EVENT_BYTES)
    assert normalized["topic"] == "opal.listen.intent.match"
    assert normalized["source"] == DEFAULT_SOURCE
    assert normalized["data"]["intent_name"] == "light_turn_on"


def test_frame_envelope_topic_mismatch_is_rejected() -> None:
    """opal-event's observer rejects a frame whose JSON topic differs from frame 1."""
    broker = _opal_event_broker_module()
    envelope = {"topic": "a.b", "data": {"x": 1}}
    with pytest.raises(broker.InvalidEventError, match="does not match"):
        broker.parse_and_normalize(b"x.y", json.dumps(envelope).encode(), MAX_EVENT_BYTES)


def test_zmq_event_publisher_swallows_errors() -> None:
    """Event delivery is best-effort: an unreachable/invalid endpoint must not raise."""
    publisher = ZmqEventPublisher("tcp://127.0.0.1:1")  # nothing listens on port 1; connect never raises either
    try:
        publisher.connect()
        publisher.publish("opal.listen.intent.match", {"intent_name": "light_turn_on"})
    finally:
        publisher.close()
