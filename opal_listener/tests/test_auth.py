"""Runtime tests for the OPAL BearerAuth contract (OpalAuthMiddleware + spec documentation)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from opal_listener.app_settings import AppSettings
from opal_listener.data.db import Database
from opal_listener.main import create_app
from opal_listener.service.listener_service import ListenerService
from opal_listener.tests.conftest import make_settings

TOKEN = "test-token-123"


@pytest.fixture
def authed_client(tmp_path) -> Iterator[TestClient]:
    settings = make_settings(tmp_path, api_token=TOKEN)
    settings.ensure_dirs()
    service = ListenerService(Database(settings.database_url), settings)
    with TestClient(create_app(settings, service)) as test_client:
        yield test_client
    service.shutdown()
    service.db.dispose()


def test_auth_disabled_without_token(client: TestClient) -> None:
    assert client.get("/api/v1/info/ping").status_code == 200
    assert client.get("/api/v1/setting/").status_code == 200


def test_auth_enforced_on_every_route(authed_client: TestClient) -> None:
    for path in ("/api/v1/info/", "/api/v1/info/ping", "/api/v1/health/", "/api/v1/setting/", "/api/v1/wakeword/"):
        response = authed_client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthorized", path
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.headers["X-Opal-Transaction-Id"], path
        assert response.headers["X-Opal-Request-Id"], path


def test_auth_accepts_valid_bearer(authed_client: TestClient) -> None:
    good = authed_client.get("/api/v1/setting/", headers={"Authorization": f"Bearer {TOKEN}"})
    assert good.status_code == 200
    wrong = authed_client.get("/api/v1/info/ping", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    wrong_scheme = authed_client.get("/api/v1/info/ping", headers={"Authorization": f"Basic {TOKEN}"})
    assert wrong_scheme.status_code == 401


def test_docs_and_ui_surfaces_stay_open(authed_client: TestClient) -> None:
    assert authed_client.get("/docs").status_code == 200
    assert authed_client.get("/redoc").status_code == 200
    assert authed_client.get("/openapi.json").status_code == 200
    assert authed_client.get("/ui/opal-listen.html").status_code == 200


def test_query_token_only_honored_on_sse_stream(authed_client: TestClient) -> None:
    rejected = authed_client.get(f"/api/v1/setting/?token={TOKEN}")
    assert rejected.status_code == 401
    bad = authed_client.get("/api/v1/recognition/stream?token=nope")
    assert bad.status_code == 401
    with authed_client.stream("GET", f"/api/v1/recognition/stream?token={TOKEN}") as response:
        assert response.status_code == 200


def test_unhandled_error_returns_opal_error_envelope(
    authed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise RuntimeError("boom")

    service: ListenerService = authed_client.app.state.listener
    monkeypatch.setattr(service.db, "session", boom)
    raw = TestClient(authed_client.app, raise_server_exceptions=False)
    response = raw.get("/api/v1/setting/", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["request_id"] and error["transaction_id"]


def test_openapi_declares_bearer_security_and_auth_responses(tmp_path) -> None:
    schema = create_app(AppSettings(data_dir=tmp_path / "d")).openapi()
    assert schema["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    assert schema["security"] == [{"BearerAuth": []}]
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            assert "401" in operation["responses"], f"{method.upper()} {path} lacks 401"
            assert "403" in operation["responses"], f"{method.upper()} {path} lacks 403"
            assert (
                operation["responses"]["401"]["content"]["application/json"]["schema"]["$ref"]
                == "#/components/schemas/OpalError"
            )


def test_cli_sends_bearer_token_from_env(authed_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from opal_listener.cli import client as cli_client

    monkeypatch.setattr(cli_client, "client_factory", lambda base_url, timeout: authed_client)
    monkeypatch.delenv(cli_client.API_TOKEN_ENV, raising=False)
    api = cli_client.ApiClient()
    with pytest.raises(cli_client.CliError, match="401"):
        api.get("/api/v1/info/ping")
    monkeypatch.setenv(cli_client.API_TOKEN_ENV, TOKEN)
    assert api.get("/api/v1/info/ping") == {"status": "ok"}
