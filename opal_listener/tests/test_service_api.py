"""`/api/v1/service/*` routes: managed server lifecycle over REST (v1.7.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opal_listener.service import lifecycle
from opal_listener.service.lifecycle import ServiceProcessManager


class _FakeProc:
    """Popen stand-in: poll() returns the given exit code (None = still running)."""

    def __init__(self, pid: int = 4242, exit_code: int | None = None) -> None:
        self.pid = pid
        self._exit_code = exit_code

    def poll(self) -> int | None:
        return self._exit_code


@pytest.fixture(autouse=True)
def isolated_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the managed-server log dir into tmp_path and disarm self-termination."""
    monkeypatch.setattr(lifecycle, "LOG_DIR", tmp_path)
    monkeypatch.setattr(lifecycle, "EARLY_DEATH_WINDOW", 0.05)
    monkeypatch.setattr(ServiceProcessManager, "terminate_self", lambda self, delay=0.0: None)
    return tmp_path


def test_service_start_launches_managed_server(
    client: TestClient, isolated_manager: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: dict[str, Any] = {}
    monkeypatch.setattr(
        ServiceProcessManager, "launch", lambda self, append=False, wait_for_pids=(): launched.update(append=append) or _FakeProc()
    )
    resp = client.post("/api/v1/service/start", json={"append": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "running" and body["pid"] == 4242 and body["previous_pid"] is None
    assert launched == {"append": True}
    assert client.post("/api/v1/service/start").status_code == 200


def test_service_start_early_death_is_operation_failed(
    client: TestClient, isolated_manager: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ServiceProcessManager, "launch", lambda self, append=False, wait_for_pids=(): _FakeProc(exit_code=3)
    )
    resp = client.post("/api/v1/service/start")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "operation_failed"


def test_service_stop_reports_stopped_when_nothing_tracked(client: TestClient) -> None:
    body = client.post("/api/v1/service/stop").json()
    assert body["state"] == "stopped" and body["pid"] is None and body["previous_pid"] is None


def test_service_stop_terminates_tracked_pid(
    client: TestClient, isolated_manager: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (isolated_manager / lifecycle.PID_NAME).write_text("999999")
    monkeypatch.setattr(lifecycle, "pid_alive", lambda pid: pid == 999999)
    stopped: list[int] = []
    monkeypatch.setattr(lifecycle, "stop_pid", lambda pid: stopped.append(pid))
    body = client.post("/api/v1/service/stop").json()
    assert body["state"] == "stopped" and body["previous_pid"] == 999999
    assert stopped == [999999]


def test_service_log_tail(client: TestClient, isolated_manager: Path) -> None:
    (isolated_manager / lifecycle.LOG_NAME).write_text("boot\nready\n")
    body = client.get("/api/v1/service/log", params={"lines": 1}).json()
    assert body["lines"] == ["ready"] and body["log_file"].endswith(lifecycle.LOG_NAME)
    assert client.get("/api/v1/service/log").json()["lines"] == ["boot", "ready"]
    assert client.get("/api/v1/service/log", params={"lines": 0}).status_code == 422
    assert client.get("/api/v1/service/log", params={"lines": 1001}).status_code == 422


def test_service_log_missing_file_is_404(client: TestClient) -> None:
    resp = client.get("/api/v1/service/log")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
