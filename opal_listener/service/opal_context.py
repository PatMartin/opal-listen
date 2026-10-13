"""OPAL SUITE STANDARD v1.2 correlation context: opaque ids, header parsing/validation, propagation."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request

# 1..128 printable ASCII characters, no whitespace/control characters.
OPAQUE_ID_PATTERN = re.compile(r"^[!-~]{1,128}$")

SESSION_HEADER = "X-Opal-Session-Id"
TRANSACTION_HEADER = "X-Opal-Transaction-Id"
REQUEST_HEADER = "X-Opal-Request-Id"
CAUSATION_HEADER = "X-Opal-Causation-Id"

# (lowercase wire name, canonical response header name)
HEADER_NAMES: tuple[tuple[str, str], ...] = (
    (SESSION_HEADER.lower(), SESSION_HEADER),
    (TRANSACTION_HEADER.lower(), TRANSACTION_HEADER),
    (REQUEST_HEADER.lower(), REQUEST_HEADER),
    (CAUSATION_HEADER.lower(), CAUSATION_HEADER),
)


def new_opaque_id() -> str:
    return uuid.uuid4().hex


def is_valid_opaque_id(value: str) -> bool:
    return bool(OPAQUE_ID_PATTERN.match(value))


@dataclass
class OpalContext:
    """The effective OPAL correlation context for one inbound HTTP exchange."""

    session_id: str | None
    transaction_id: str
    request_id: str
    causation_id: str | None

    def response_headers(self) -> dict[str, str]:
        headers = {TRANSACTION_HEADER: self.transaction_id, REQUEST_HEADER: self.request_id}
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        if self.causation_id:
            headers[CAUSATION_HEADER] = self.causation_id
        return headers


def fallback_context() -> OpalContext:
    """Used only if request.state.opal_context was never populated (e.g. middleware not installed)."""
    return OpalContext(session_id=None, transaction_id=new_opaque_id(), request_id=new_opaque_id(), causation_id=None)


def get_opal_context(request: Request) -> OpalContext:
    return getattr(request.state, "opal_context", None) or fallback_context()
