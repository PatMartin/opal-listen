"""End-to-end: real uvicorn server + real HTTP CLI + SSE stream + live runtime (fake provider, scripted audio)."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from typer.testing import CliRunner

from opal_listener.cli.main import app as cli_app
from opal_listener.data.db import Database
from opal_listener.main import create_app
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.providers.fake_provider import FakeListenerProvider
from opal_listener.tests.conftest import RecordingEventPublisher, make_settings
from opal_listener.tests.test_pipeline_runtime import COMMAND, ScriptedSource


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[tuple[str, ListenerService]]:
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    service = ListenerService(
        Database(settings.database_url),
        settings,
        source_factory=lambda s: ScriptedSource([b"\x00\x00" * 1600] * 20 + COMMAND),
        event_publisher=RecordingEventPublisher(),
    )
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings, service), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}", service
    service.stop()
    server.should_exit = True
    thread.join(timeout=10)


def test_cli_listen_streams_live_result(live_server: tuple[str, ListenerService]) -> None:
    url, service = live_server
    runner = CliRunner()
    assert (
        runner.invoke(cli_app, ["listen", "--api-url", url, "settings", "set", "listener_provider", "fake"]).exit_code
        == 0
    )
    FakeListenerProvider.script.clear()
    FakeListenerProvider.script.append("computer turn off the garage lights")

    result = runner.invoke(
        cli_app, ["listen", "--api-url", url, "--json", "test", "listen", "--count", "1", "--duration", "20"]
    )
    assert result.exit_code == 0, result.output
    assert '"intent_name": "light_turn_off"' in result.output
    assert '"room_name": "garage"' in result.output
    # the CLI started the pipeline, so it stops it again
    assert service.running is False

    summary = runner.invoke(cli_app, ["listen", "--api-url", url, "--json", "perf", "summary"])
    assert '"matched_count": 1' in summary.output
