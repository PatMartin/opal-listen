"""Strong ETag computation and If-Match enforcement for mutable portable configuration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from opal_listener.service.errors import PreconditionFailedError


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


def compute_etag(payload: Any) -> str:
    """A strong ETag (quoted, content-derived) for the given JSON-serializable payload."""
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return f'"{digest}"'


def item_etag(model: BaseModel) -> str:
    return compute_etag(model.model_dump(mode="json"))


def collection_etag(models: Sequence[BaseModel] | Sequence[Mapping[str, Any]]) -> str:
    payload = [m.model_dump(mode="json") if isinstance(m, BaseModel) else m for m in models]
    return compute_etag(payload)


def require_matching_etag(if_match: str, current: str, label: str = "resource") -> None:
    """Raise 412 if the supplied strong If-Match does not match the resource's current ETag."""
    if if_match.strip().strip('"') != current.strip('"'):
        raise PreconditionFailedError(f"{label} has changed since it was last fetched (stale If-Match)")
