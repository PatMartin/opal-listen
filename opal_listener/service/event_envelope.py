"""Shared OPAL event-envelope construction.

The same envelope schema is produced by the HTTP publisher (POST /api/v1/events/) and the
ZeroMQ publisher (frame 2 of the two-frame wire message): a UUID id, the topic, the source, an
RFC3339 UTC timestamp, and a `data` object carrying the OPAL v1.3 EventContext correlation
metadata alongside the recognition-result payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from opal_listener.service.opal_context import new_opaque_id

SCHEMA_VERSION = "2.0"
DEFAULT_SOURCE = "opal-listen"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_event_envelope(topic: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload dict in the canonical event envelope shared by all event transports."""
    session_id = payload.get("session_id")
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "transaction_id": new_opaque_id(),
        "request_id": new_opaque_id(),
        "causation_id": None,
        "depth": 0,
    }
    return {
        "id": str(uuid.uuid4()),
        "topic": topic,
        "source": DEFAULT_SOURCE,
        "timestamp": utc_now_iso(),
        "data": {"metadata": metadata, **payload},
    }
