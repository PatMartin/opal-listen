#!/usr/bin/env python3
"""Deterministic live-capture Event -> Say smoke test.

Runs the real chain end to end with no mocks on the dispatch side:

    scripted mic source (WAV) -> Vosk -> intent match
        -> HttpEventPublisher -> opal-event REST -> rule -> http action -> opal-say

opal-listen runs in-process (TestClient lifespan) with a scripted audio source
replaying a checked-in golden fixture; opal-event and opal-say run as real
subprocesses against their own sibling checkouts.

Requirements (skip with a clear error if absent):
    ../opal-event and ../opal-say, each with a working .venv (override with
    OPAL_EVENT_DIR / OPAL_SAY_DIR). Piper voice en-us-amy-low is the active
    opal-say profile in its checked-in config.

Usage:
    .venv/Scripts/python.exe scripts/event_say_smoke.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

EVENT_REPO = Path(os.environ.get("OPAL_EVENT_DIR", REPO.parent / "opal-event"))
SAY_REPO = Path(os.environ.get("OPAL_SAY_DIR", REPO.parent / "opal-say"))
EVENT_PORT = int(os.environ.get("OPAL_EVENT_PORT", "8090"))
SAY_PORT = int(os.environ.get("OPAL_SAY_PORT", "8001"))
EVENT_BASE = f"http://127.0.0.1:{EVENT_PORT}"
SAY_BASE = f"http://127.0.0.1:{SAY_PORT}"

GOLDEN_WAV = REPO / "opal_listener" / "tests" / "audio" / "fixtures" / "golden_what_time.wav"
VOSK_MODEL = Path(
    os.environ.get(
        "OPAL_LISTENER_VOSK_MODEL_PATH", REPO / "resources" / "vosk" / "models" / "vosk-model-small-en-us-0.15"
    )
)
TOPIC = "opal.listen.intent.match"

_checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _checks.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    return ok


def wait_health(url: str, proc: subprocess.Popen[bytes], timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"service exited early (rc={proc.returncode}); see output above")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"service at {url} did not become healthy in {timeout}s")


def venv_python(repo: Path) -> Path:
    return repo / ".venv" / "Scripts" / "python.exe"


def main() -> int:
    for label, repo in (("opal-event", EVENT_REPO), ("opal-say", SAY_REPO)):
        if not venv_python(repo).exists():
            print(f"SKIP: {label} venv not found at {venv_python(repo)}")
            return 2

    tmp = Path(tempfile.mkdtemp(prefix="opal_event_say_smoke_"))
    print(f"workspace: {tmp}")

    event_cfg = tmp / "opal_event.yaml"
    event_cfg.write_text(
        f"""version: 1
server: {{host: 127.0.0.1, port: {EVENT_PORT}}}
zeromq:
  publishers: tcp://127.0.0.1:15555
  subscribers: tcp://127.0.0.1:15556
security:
  rest_auth: {{enabled: false}}
  http_action_allowlist:
    - {{host: 127.0.0.1, ports: [{SAY_PORT}], schemes: [http]}}
persistence:
  database: {str(tmp / 'opal_event.db').replace(chr(92), '/')}
  event_mode: all
  retention_days: 7
  execution_history_enabled: true
actions:
  - id: say_ack
    type: http
    enabled: true
    method: POST
    url: {SAY_BASE}/api/v1/generate
    headers: {{Content-Type: application/json}}
    body: {{text: "Time request acknowledged"}}
    body_mode: template
    timeout_seconds: 30
rules:
  - id: listen_get_time
    enabled: true
    topic: {TOPIC}
    conditions:
      - {{path: data.intent_name, operator: eq, value: get_time}}
    actions: [say_ack]
""",
        encoding="utf-8",
    )

    say_proc = subprocess.Popen(
        [str(venv_python(SAY_REPO)), "-m", "opal_say", "--port", str(SAY_PORT), "--no-env-file"],
        cwd=SAY_REPO,
    )
    event_proc = subprocess.Popen(
        [
            str(venv_python(EVENT_REPO)),
            "-m",
            "opal_event",
            "--config",
            str(event_cfg),
            "--no-env-file",
            "--log-level",
            "warning",
        ],
        cwd=EVENT_REPO,
    )

    matched_intent = None
    try:
        print("waiting for opal-say ...")
        wait_health(f"{SAY_BASE}/api/v1/health/", say_proc)
        print("waiting for opal-event ...")
        wait_health(f"{EVENT_BASE}/api/v1/health/", event_proc)

        # opal-say leg: verify it produces a nonempty WAV (also usable as capture input).
        gen = httpx.post(
            f"{SAY_BASE}/api/v1/generate",
            json={"text": "computer. what time is it"},
            timeout=60,
        )
        said_wav = tmp / "said_utterance.wav"
        wav_ok = gen.status_code == 200 and gen.content[:4] == b"RIFF" and len(gen.content) > 1000
        if wav_ok:
            said_wav.write_bytes(gen.content)
        check("opal-say /generate returns nonempty WAV", wav_ok, f"{len(gen.content)} bytes")

        from fastapi.testclient import TestClient

        from opal_listener.app_settings import PACKAGE_DIR, AppSettings
        from opal_listener.data.db import Database
        from opal_listener.main import create_app
        from opal_listener.service.audio.capture import AudioFrame, AudioSource
        from opal_listener.service.audio.pcm import iter_frames, load_wav_bytes
        from opal_listener.service.listener_service import ListenerService

        class FeedSource(AudioSource):
            def __init__(self, feed: deque[bytes]) -> None:
                self.feed = feed

            @property
            def description(self) -> str:
                return "smoke-feed"

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def read(self, timeout: float) -> AudioFrame | None:
                time.sleep(0.002)
                pcm = self.feed.popleft() if self.feed else b"\x00" * 3200
                return AudioFrame(pcm, time.monotonic())

        settings = AppSettings(
            data_dir=tmp / "listen_data",
            config_dir=PACKAGE_DIR / "config",
            vosk_model_path=str(VOSK_MODEL),
            opal_event_url=EVENT_BASE,
            event_transport="http",
        )
        settings.ensure_dirs()
        service = ListenerService(Database(settings.database_url), settings)

        feed: deque[bytes] = deque()
        service._source_factory = lambda snapshot_settings: FeedSource(feed)

        with TestClient(create_app(settings, service)) as client:
            etag = client.get("/api/v1/setting/").headers["etag"]
            patch = client.patch(
                "/api/v1/setting/",
                json={"intent_dispatch": "event", "vosk_model_path": str(VOSK_MODEL)},
                headers={"If-Match": etag},
            )
            check("intent_dispatch=event accepted", patch.status_code == 200, patch.text[:120])

            client.post("/api/v1/recognition/start")
            try:
                # Prefer the say-synthesized utterance (full say->listen->event->say loop);
                # fall back to the checked-in golden fixture if TTS doesn't decode.
                from opal_listener.service import recognition_store

                candidates = [w for w in (said_wav if wav_ok else None, GOLDEN_WAV) if w]
                deadline = time.monotonic() + 40
                for wav_path in candidates:
                    if matched_intent:
                        break
                    frames = iter_frames(load_wav_bytes(wav_path.read_bytes()), 100)
                    feed.extend(frames)
                    feed.extend([b"\x00" * 3200] * 30)  # trailing silence -> speech_end
                    print(f"fed {wav_path.name} ({len(frames)} frames)")
                    while time.monotonic() < deadline and not matched_intent:
                        with service.db.session() as session:
                            rows = recognition_store.list_logs(session, 5, None, True)
                        for row in rows:
                            if row.source == "mic":
                                matched_intent = row.intent_name
                                break
                        time.sleep(0.25)

                check(
                    "live capture matched intent",
                    matched_intent == "get_time",
                    f"intent_name={matched_intent!r}",
                )
            finally:
                client.post("/api/v1/recognition/stop")

        # opal-event leg: the published event and the action execution receipt.
        event_id = None
        exec_ok = False
        receipts: list[dict[str, Any]] = []
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not exec_ok:
            events = httpx.get(f"{EVENT_BASE}/api/v1/event/", params={"topic": TOPIC}, timeout=5).json()
            if events:
                event_id = events[0]["id"]
                receipts = httpx.get(
                    f"{EVENT_BASE}/api/v1/execution/", params={"event_id": event_id}, timeout=5
                ).json()
                if receipts:
                    rec = receipts[0]
                    exec_ok = rec["status"] == "succeeded" and rec.get("http_status") == 200
            time.sleep(0.5)
        check("event stored in opal-event", event_id is not None, f"id={event_id}")
        check(
            "execution receipt: say_ack succeeded",
            exec_ok,
            f"status={receipts[0]['status'] if receipts else None} "
            f"http_status={receipts[0].get('http_status') if receipts else None}",
        )
    finally:
        for proc in (event_proc, say_proc):
            proc.terminate()
        for proc in (event_proc, say_proc):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    failed = [name for name, ok, _ in _checks if not ok]
    print(f"\n{'SMOKE PASS' if not failed else 'SMOKE FAIL'} ({len(_checks) - len(failed)}/{len(_checks)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
