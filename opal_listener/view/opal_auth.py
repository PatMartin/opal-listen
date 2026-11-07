"""Optional Bearer-token authentication implementing the OPAL BearerAuth contract.

When ``AppSettings.api_token`` is unset the middleware is a pass-through (the shared kernel
permits disabled authentication on loopback deployments). When set, every request outside the
documentation/static surfaces must present ``Authorization: Bearer <token>``; failures get a 401
OpalError envelope. The SSE stream additionally accepts ``?token=`` because EventSource cannot
set request headers. Correlation headers are transport context and are never credentials.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from opal_listener.service.opal_context import get_opal_context

Handler = Callable[[Request], Awaitable[Response]]

# Documentation/discovery surfaces outside the API contract; left open so the bundled console
# and the OpenAPI docs it calls remain reachable. API requests themselves still require auth.
_EXEMPT_PREFIXES = ("/docs", "/redoc", "/ui")
_EXEMPT_PATHS = {"/openapi.json", "/favicon.ico"}

# EventSource cannot send headers, so the SSE stream accepts the token as a query parameter.
_SSE_PATH = "/api/v1/recognition/stream"


class OpalAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, token: str | None) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        if not self._token or self._exempt(request.url.path) or self._authenticated(request):
            return await call_next(request)
        ctx = get_opal_context(request)
        error: dict[str, Any] = {
            "code": "unauthorized",
            "message": "missing or invalid bearer token",
            "request_id": ctx.request_id,
            "transaction_id": ctx.transaction_id,
        }
        if ctx.session_id:
            error["session_id"] = ctx.session_id
        if ctx.causation_id:
            error["causation_id"] = ctx.causation_id
        response = JSONResponse(status_code=401, content={"error": error})
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @staticmethod
    def _exempt(path: str) -> bool:
        return path in _EXEMPT_PATHS or any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES)

    def _authenticated(self, request: Request) -> bool:
        token = self._token or ""
        scheme, _, credential = (request.headers.get("authorization") or "").partition(" ")
        if scheme.lower() == "bearer" and credential and secrets.compare_digest(credential, token):
            return True
        if request.url.path == _SSE_PATH:
            candidate = request.query_params.get("token") or ""
            return bool(candidate) and secrets.compare_digest(candidate, token)
        return False
