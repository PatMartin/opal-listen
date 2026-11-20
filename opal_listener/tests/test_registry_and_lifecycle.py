"""Provider-registry and service-process lifecycle utility tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import opal_listener.service.providers.registry as registry
from opal_listener.service.lifecycle import (
    ServiceProcessManager,
    pid_alive,
    stop_pid,
    wait_for_pids,
    write_pid_file,
)
from opal_listener.service.providers.base import ProviderError
from opal_listener.service.providers.fake_provider import FakeListenerProvider


def test_create_provider_known_and_unknown():
    provider = registry.create_provider("fake")
    assert isinstance(provider, FakeListenerProvider)
    with pytest.raises(ProviderError, match="unknown listener provider 'bogus'"):
        registry.create_provider("bogus")


def test_create_refiner_none_and_unknown():
    assert registry.create_refiner("") is None
    assert registry.create_refiner("none") is None
    with pytest.raises(ProviderError, match="unknown stt refiner"):
        registry.create_refiner("bogus")


def test_available_providers_builtins_and_broken_plugin(monkeypatch):
    class GoodEp:
        name = "extra_fake"

        def load(self):
            return FakeListenerProvider

    class BrokenEp:
        name = "broken"

        def load(self):
            raise ImportError("no module")

    class NotAProviderEp:
        name = "not_a_provider"

        def load(self):
            return object  # not a ListenerProvider subclass

    monkeypatch.setattr(registry, "entry_points", lambda group: [GoodEp(), BrokenEp(), NotAProviderEp()])
    providers = registry.available_providers()
    assert providers["extra_fake"] is FakeListenerProvider
    assert "broken" not in providers and "not_a_provider" not in providers
    # built-ins are always present
    assert {"fake", "vosk"} <= set(providers)


@pytest.mark.skipif(os.name == "nt", reason="posix pid semantics")
def test_pid_alive_and_stop_pid():
    assert pid_alive(os.getpid()) is True
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert pid_alive(proc.pid) is True
    finally:
        stop_pid(proc.pid)
    proc.wait(timeout=10)
    assert pid_alive(proc.pid) is False


def _detached_sleep(seconds: float) -> int:
    """Spawn a sleep that is NOT our child (start_new_session via a helper), so its
    pid behaves like the detached managed-server pids wait_for_pids is built for —
    unreaped children of our own process would zombie and look alive to kill(pid, 0)."""
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import subprocess,sys;"
            f"p=subprocess.Popen([sys.executable,'-c','import time;time.sleep({seconds})'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
            "start_new_session=True);print(p.pid)",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return int(out.stdout.strip())


@pytest.mark.skipif(os.name == "nt", reason="posix pid semantics")
def test_wait_for_pids_returns_and_times_out():
    wait_for_pids([])  # empty set returns immediately
    pid = _detached_sleep(0.3)
    wait_for_pids([pid], timeout=10)  # returns once the process exits
    assert pid_alive(pid) is False

    slow = _detached_sleep(30)
    try:
        with pytest.raises(TimeoutError, match="timed out waiting"):
            wait_for_pids([slow], timeout=0.5)
    finally:
        stop_pid(slow)
        wait_for_pids([slow], timeout=10)


def test_pid_file_and_tracked_pid(tmp_path: Path, app_settings):
    write_pid_file(tmp_path / "sub" / "opal_listen.pid")
    assert (tmp_path / "sub" / "opal_listen.pid").read_text().strip() == str(os.getpid())

    mgr = ServiceProcessManager(app_settings, log_dir=tmp_path / "logs")
    assert mgr.tracked_pid() is None  # no pidfile yet
    mgr.pid_file.parent.mkdir(parents=True, exist_ok=True)
    mgr.pid_file.write_text("not-a-pid\n")
    assert mgr.tracked_pid() is None and not mgr.pid_file.exists()  # corrupt -> cleared
    mgr.pid_file.write_text("99999999\n")  # implausible pid
    assert mgr.tracked_pid() is None and not mgr.pid_file.exists()  # stale -> cleared
    mgr.pid_file.write_text(f"{os.getpid()}\n")
    assert mgr.tracked_pid() == os.getpid()
