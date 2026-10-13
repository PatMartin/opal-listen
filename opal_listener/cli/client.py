"""HTTP client used by REST-backed CLI commands (the CLI always goes through the REST API for those;
`event publish` is the one command that bypasses this entirely and talks to ZeroMQ instead)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import typer

DEFAULT_API_URL = "http://localhost:8000"
API_URL_ENV = "OPAL_LISTEN_API_URL"
DEFAULT_XPUB_URL = "tcp://127.0.0.1:5559"
DEFAULT_XSUB_URL = "tcp://127.0.0.1:5560"
XPUB_URL_ENV = "OPAL_EVENT_XPUB_URL"
XSUB_URL_ENV = "OPAL_EVENT_XSUB_URL"

# typer >= 0.27 vendors its own click; use whichever ClickException typer actually catches.
_ClickException: type[Exception] = next(c for c in typer.BadParameter.__mro__ if c.__name__ == "ClickException")


class CliError(_ClickException):  # type: ignore[misc,valid-type]
    """Printed as 'Error: ...' by click; exit code 1."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _default_factory(base_url: str, timeout: float) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=timeout)


# Tests replace this with a factory returning a FastAPI TestClient.
client_factory: Callable[[str, float], httpx.Client] = _default_factory


def format_detail(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error = payload["error"]
        return f"{error.get('code')}: {error.get('message')}" if error.get("code") else str(error.get("message"))
    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                loc = ".".join(str(p) for p in item.get("loc", []) if p != "body")
                parts.append(f"{loc}: {item.get('msg')}" if loc else str(item.get("msg")))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(detail)


@dataclass
class ApiClient:
    base_url: str = DEFAULT_API_URL
    timeout: float = 30.0

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._http: httpx.Client | None = None

    @property
    def http(self) -> httpx.Client:
        if self._http is None:
            self._http = client_factory(self.base_url, self.timeout)
        return self._http

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise CliError(
                f"cannot reach the opal listener API at {self.base_url} ({exc}).\n"
                f"Start it with 'python -m opal_listener' or set {API_URL_ENV}."
            ) from exc
        if response.status_code >= 400:
            try:
                message = format_detail(response.json())
            except ValueError:
                message = response.text
            raise CliError(f"HTTP {response.status_code}: {message}")
        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        return response.json() if "json" in content_type else response.content

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self.request("PATCH", path, **kw)

    def put(self, path: str, **kw: Any) -> Any:
        return self.request("PUT", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)

    def etag(self, path: str) -> str:
        """Fetch the current strong ETag of a resource (for a subsequent If-Match mutation)."""
        try:
            response = self.http.request("GET", path)
        except httpx.TransportError as exc:
            raise CliError(
                f"cannot reach the opal listener API at {self.base_url} ({exc}).\n"
                f"Start it with 'python -m opal_listener' or set {API_URL_ENV}."
            ) from exc
        if response.status_code >= 400:
            try:
                message = format_detail(response.json())
            except ValueError:
                message = response.text
            raise CliError(f"HTTP {response.status_code}: {message}")
        if "etag" not in response.headers:
            raise CliError(f"server response for {path} did not include an ETag")
        return response.headers["etag"]

    def patch_with_etag(self, path: str, *, etag_path: str | None = None, **kw: Any) -> Any:
        """PATCH after fetching the current ETag, for the mandatory strong If-Match contract."""
        headers = {**kw.pop("headers", {}), "If-Match": self.etag(etag_path or path)}
        return self.patch(path, headers=headers, **kw)

    def delete_with_etag(self, path: str, *, etag_path: str | None = None, **kw: Any) -> Any:
        """DELETE after fetching the current ETag, for the mandatory strong If-Match contract."""
        headers = {**kw.pop("headers", {}), "If-Match": self.etag(etag_path or path)}
        return self.delete(path, headers=headers, **kw)

    def stream_events(
        self, path: str, params: dict[str, Any] | None = None, idle_timeout: float | None = None
    ) -> Iterator[tuple[str, Any]]:
        """Yield (event, data) pairs from a Server-Sent Events endpoint.

        The stream ends quietly if no data arrives for idle_timeout seconds (None = wait forever).
        """
        try:
            timeout = httpx.Timeout(None, read=idle_timeout)
            with self.http.stream("GET", path, params=params, timeout=timeout) as response:
                if response.status_code >= 400:
                    raise CliError(f"HTTP {response.status_code} opening event stream")
                event, data_lines = "message", []
                for line in response.iter_lines():
                    if line == "":
                        if data_lines:
                            raw = "\n".join(data_lines)
                            try:
                                yield event, json.loads(raw)
                            except ValueError:
                                yield event, raw
                        event, data_lines = "message", []
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
        except httpx.ReadTimeout:
            return
        except httpx.TransportError as exc:
            raise CliError(f"event stream failed: {exc}") from exc

    def resolve(self, collection: str, ref: str, key: str = "name") -> dict[str, Any]:
        """Find an item in a collection by id or by name."""
        items = self.get(collection)
        for item in items:
            if (ref.isdigit() and item["id"] == int(ref)) or str(item.get(key)) == ref:
                return item
        raise CliError(f"'{ref}' not found in {collection}")


def api_url_from_env() -> str:
    return os.environ.get(API_URL_ENV, DEFAULT_API_URL)


def xpub_url_from_env() -> str:
    return os.environ.get(XPUB_URL_ENV, DEFAULT_XPUB_URL)


def xsub_url_from_env() -> str:
    return os.environ.get(XSUB_URL_ENV, DEFAULT_XSUB_URL)
