"""opal listen event: publish messages onto the ZeroMQ event bus."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import typer

from opal_listener.cli.client import CliError
from opal_listener.cli.output import done, state
from opal_listener.service.event_envelope import DEFAULT_SOURCE, build_event_envelope, utc_now_iso
from opal_listener.service.zmq_events import MAX_EVENT_BYTES, TOPIC_MAX_LENGTH, TOPIC_PATTERN

event_app = typer.Typer(help="Publish events onto the ZeroMQ event bus.", no_args_is_help=True)

# Give the PUB socket's TCP connection and subscription handshake time to settle before the
# first send; otherwise this short-lived process risks the classic ZeroMQ "slow joiner" problem
# of the very first message being silently dropped.
_CONNECT_SETTLE_SECONDS = 0.2

_TOPIC_RE = re.compile(TOPIC_PATTERN)


def event_envelope_for(topic: str, message: str) -> dict[str, Any]:
    """Build the opal-event two-frame JSON envelope for TOPIC from a raw MESSAGE argument.

    Plain text is wrapped as {"message": ...}; a JSON object becomes the data payload; a JSON
    object already carrying 'topic' and 'data' is treated as a complete envelope (with defaults
    for the optional fields) and its topic must equal TOPIC.
    """
    try:
        parsed = json.loads(message)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and "topic" in parsed and "data" in parsed:
        if parsed["topic"] != topic:
            raise CliError(f"envelope topic {parsed['topic']!r} does not match {topic!r}")
        envelope = parsed
        envelope.setdefault("id", str(uuid.uuid4()))
        envelope.setdefault("source", DEFAULT_SOURCE)
        envelope.setdefault("timestamp", utc_now_iso())
        return envelope
    payload: dict[str, Any]
    if isinstance(parsed, dict):
        payload = parsed
    elif parsed is None:
        payload = {"message": message}
    else:
        payload = {"message": parsed}
    return build_event_envelope(topic, payload)


@event_app.command("publish")
def event_publish(
    ctx: typer.Context,
    topic: str = typer.Argument(..., help="Topic name"),
    message: str = typer.Argument(..., help="Event/message payload (plain text or JSON)"),
) -> None:
    """Publish MESSAGE under TOPIC to the server's --xpub endpoint."""
    try:
        import zmq
    except ImportError as exc:
        raise CliError("pyzmq is required for 'event publish' (pip install pyzmq)") from exc

    if len(topic) > TOPIC_MAX_LENGTH or not _TOPIC_RE.match(topic):
        raise CliError(f"invalid topic {topic!r}: must match {TOPIC_PATTERN} (<= {TOPIC_MAX_LENGTH} chars)")
    envelope = event_envelope_for(topic, message)
    frames = [topic.encode("utf-8"), json.dumps(envelope, default=str).encode("utf-8")]
    if sum(len(frame) for frame in frames) > MAX_EVENT_BYTES:
        raise CliError(f"event exceeds the {MAX_EVENT_BYTES}-byte wire limit")

    xpub_url = state(ctx).xpub_url
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    try:
        try:
            socket.connect(xpub_url)
        except zmq.ZMQError as exc:
            raise CliError(f"cannot connect to {xpub_url} ({exc})") from exc
        time.sleep(_CONNECT_SETTLE_SECONDS)
        socket.send_multipart(frames)
    finally:
        socket.close(linger=0)
        context.term()
    done(ctx, f"published to '{topic}' via {xpub_url}", {"topic": topic, "message": message, "xpub": xpub_url})
