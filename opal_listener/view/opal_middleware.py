"""ASGI middleware implementing the OPAL SUITE STANDARD v1.2 correlation-header contract.

Reads/validates X-Opal-Session-Id, X-Opal-Transaction-Id, X-Opal-Request-Id and X-Opal-Causation-Id
on every request, generating transaction/request ids when absent, rejecting malformed values with a
422 OpalError response, and stamping the effective ids onto every response (success, error, and
streaming alike).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from opal_listener.service.opal_context import (
    CAUSATION_HEADER,
    HEADER_NAMES,
    REQUEST_HEADER,
    SESSION_HEADER,
    TRANSACTION_HEADER,
    OpalContext,
    is_valid_opaque_id,
    new_opaque_id,
)

Handler = Callable[[Request], Awaitable[Response]]


class OpalContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        values: dict[str, str | None] = {}
        invalid: list[str] = []
        for wire_name, canonical in HEADER_NAMES:
            value = request.headers.get(wire_name)
            if value is not None and not is_valid_opaque_id(value):
                invalid.append(canonical)
            values[canonical] = value

        if invalid:
            request_id, transaction_id = new_opaque_id(), new_opaque_id()
            response = JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "invalid_header",
                        "message": f"invalid value for header(s): {', '.join(invalid)}",
                        "request_id": request_id,
                        "transaction_id": transaction_id,
                    }
                },
            )
            response.headers[REQUEST_HEADER] = request_id
            response.headers[TRANSACTION_HEADER] = transaction_id
            return response

        context = OpalContext(
            session_id=values[SESSION_HEADER],
            transaction_id=values[TRANSACTION_HEADER] or new_opaque_id(),
            request_id=values[REQUEST_HEADER] or new_opaque_id(),
            causation_id=values[CAUSATION_HEADER],
        )
        request.state.opal_context = context
        response = await call_next(request)
        for key, value in context.response_headers().items():
            response.headers[key] = value
        return response
