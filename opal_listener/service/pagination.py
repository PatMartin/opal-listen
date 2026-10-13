"""Opaque cursor codec for growing histories (stable descending created_at + id ordering).

Cursors are scoped to the exact filter set: the signature binds scope+filters so that a cursor
produced under one filter combination is rejected (422) if reused under another.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any


def _filters_signature(scope: str, filters: Mapping[str, Any]) -> str:
    canonical = json.dumps({"scope": scope, "filters": filters}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def encode_cursor(scope: str, filters: Mapping[str, Any], last_created_at: datetime, last_id: int) -> str:
    payload = {"sig": _filters_signature(scope, filters), "t": last_created_at.isoformat(), "id": last_id}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(
    cursor: str,
    scope: str,
    filters: Mapping[str, Any],
    error_factory: Callable[[str], Exception],
) -> tuple[datetime, int]:
    """Decode and validate a cursor, raising ``error_factory(message)`` if invalid or mismatched."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw)
        if payload["sig"] != _filters_signature(scope, filters):
            raise ValueError("cursor is scoped to a different filter set")
        return datetime.fromisoformat(payload["t"]), int(payload["id"])
    except Exception as exc:  # noqa: BLE001 - normalize every decode failure to one error type
        raise error_factory("invalid or mismatched cursor") from exc
