"""Unit test for ZmqEventPublisher (the server-side ZeroMQ event bus publisher)."""

from __future__ import annotations

import contextlib
import threading
import time

import zmq

from opal_listener.service.zmq_events import ZmqEventPublisher


def test_zmq_event_publisher_delivers_through_an_xpub_xsub_broker() -> None:
    """Mirrors the CLI's event-publish test: a bare bound SUB racing an immediate PUB send is the
    classic ZeroMQ "slow joiner" problem, so this simulates the real broker topology instead
    (PUB connects to XSUB, SUB connects to XPUB, with a proxy relaying between them)."""

    def _run_proxy(frontend: zmq.Socket, backend: zmq.Socket) -> None:
        with contextlib.suppress(zmq.ZMQError):  # raised when torn down at the end of the test
            zmq.proxy(frontend, backend)

    ctx = zmq.Context()
    xpub = ctx.socket(zmq.XPUB)
    xpub_port = xpub.bind_to_random_port("tcp://127.0.0.1")
    xsub = ctx.socket(zmq.XSUB)
    xsub_port = xsub.bind_to_random_port("tcp://127.0.0.1")
    threading.Thread(target=_run_proxy, args=(xpub, xsub), daemon=True).start()

    sub = ctx.socket(zmq.SUB)
    sub.connect(f"tcp://127.0.0.1:{xpub_port}")
    sub.setsockopt_string(zmq.SUBSCRIBE, "")

    publisher = ZmqEventPublisher(f"tcp://127.0.0.1:{xsub_port}")
    try:
        # Mirror ListenerService.startup(): connect eagerly, then give the subscription time to
        # propagate through the proxy, so the test isn't racing the same "slow joiner" problem
        # that calling connect() early (instead of lazily on first publish) is meant to avoid.
        publisher.connect()
        time.sleep(0.3)
        publisher.publish("opal.listen.intent.match", {"intent_name": "light_turn_on", "matched": True})
        assert sub.poll(3000), "no message received through the XPUB/XSUB broker"
        topic, message = sub.recv_multipart()
        assert topic == b"opal.listen.intent.match"
        assert b'"intent_name": "light_turn_on"' in message
    finally:
        publisher.close()
        sub.close()


def test_zmq_event_publisher_swallows_errors() -> None:
    """Event delivery is best-effort: an unreachable/invalid endpoint must not raise."""
    publisher = ZmqEventPublisher("tcp://127.0.0.1:1")  # nothing listens on port 1; connect never raises either
    try:
        publisher.connect()
        publisher.publish("opal.listen.intent.match", {"intent_name": "light_turn_on"})
    finally:
        publisher.close()
