"""Shared fixtures: isolated SQLite per test, FastAPI TestClient, CLI runner wired to the TestClient."""

from __future__ import annotations

import math
import struct
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from opal_listener.app_settings import PACKAGE_DIR, AppSettings
from opal_listener.cli import client as cli_client
from opal_listener.cli.main import app as cli_app
from opal_listener.data.db import Database
from opal_listener.main import create_app
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.providers.fake_provider import FakeListenerProvider


class RecordingEventPublisher:
    """Test double for EventPublisher: records (topic, payload) pairs instead of using ZeroMQ."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.events.append((topic, payload))

    def close(self) -> None:
        self.closed = True


VOSK_MODEL = Path(AppSettings().vosk_model_path)
HAS_VOSK_MODEL = (VOSK_MODEL / "am").is_dir()
TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "audio" / "fixtures"

requires_vosk = pytest.mark.skipif(not HAS_VOSK_MODEL, reason=f"Vosk model not found at {VOSK_MODEL}")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("vosk") and not HAS_VOSK_MODEL:
            item.add_marker(pytest.mark.skip(reason=f"Vosk model not found at {VOSK_MODEL}"))


def make_settings(tmp_path: Path, **overrides: Any) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        config_dir=overrides.pop("config_dir", PACKAGE_DIR / "config"),
        vosk_model_path=str(VOSK_MODEL),
        **overrides,
    )


@pytest.fixture
def app_settings(tmp_path: Path) -> AppSettings:
    return make_settings(tmp_path)


@pytest.fixture
def service(app_settings: AppSettings) -> Iterator[ListenerService]:
    app_settings.ensure_dirs()
    svc = ListenerService(Database(app_settings.database_url), app_settings, event_publisher=RecordingEventPublisher())
    yield svc
    svc.shutdown()
    svc.db.dispose()


@pytest.fixture
def client(service: ListenerService, app_settings: AppSettings) -> Iterator[TestClient]:
    with TestClient(create_app(app_settings, service)) as test_client:
        yield test_client


def patch_settings(client: TestClient, **changes: Any) -> Any:
    """PATCH /api/v1/settings/ with the current strong ETag, per the OPAL v1.2 If-Match contract."""
    etag = client.get("/api/v1/settings/").headers["etag"]
    return client.patch("/api/v1/settings/", json=changes, headers={"If-Match": etag})


def etag_of(client: TestClient, path: str) -> str:
    return client.get(path).headers["etag"]


@pytest.fixture
def fake_provider(client: TestClient) -> Iterator[type[FakeListenerProvider]]:
    """Switch the running service to the deterministic fake provider."""
    FakeListenerProvider.script.clear()
    FakeListenerProvider.fail_load = False
    FakeListenerProvider.wakeword_confidence = 0.9
    response = patch_settings(client, listener_provider="fake")
    assert response.status_code == 200, response.text
    yield FakeListenerProvider
    FakeListenerProvider.script.clear()
    FakeListenerProvider.fail_load = False


class CliHarness:
    def __init__(self, test_client: TestClient) -> None:
        self.runner = CliRunner()
        self.client = test_client

    def __call__(self, *args: str, input: str | None = None) -> Any:
        return self.runner.invoke(cli_app, ["listen", *args], input=input, catch_exceptions=False)

    def json(self, *args: str) -> Any:
        import json

        result = self(*("--json", *args))
        assert result.exit_code == 0, result.output
        return json.loads(result.output)


@pytest.fixture
def cli(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> CliHarness:
    monkeypatch.setattr(cli_client, "client_factory", lambda base_url, timeout: client)
    return CliHarness(client)


# --- synthetic audio -----------------------------------------------------------------------------


def tone_frame(ms: int = 100, amplitude: int = 8000, freq: float = 440.0, rate: int = 16000) -> bytes:
    n = rate * ms // 1000
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))


def silence_frame(ms: int = 100, rate: int = 16000) -> bytes:
    return b"\x00\x00" * (rate * ms // 1000)


@pytest.fixture
def frames() -> dict[str, Callable[..., bytes]]:
    return {"tone": tone_frame, "silence": silence_frame}
