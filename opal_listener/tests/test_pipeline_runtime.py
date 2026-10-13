"""Pipeline state machine and live runtime (watchdog/recovery) using the fake provider and scripted audio."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opal_listener.service.audio.capture import AudioCaptureError, AudioFrame, AudioSource
from opal_listener.service.audio.vad import VadSettings
from opal_listener.service.pipeline import RecognitionPipeline
from opal_listener.service.providers.fake_provider import FakeListenerProvider
from opal_listener.tests.conftest import silence_frame, tone_frame

COMMAND = [silence_frame()] * 5 + [tone_frame()] * 10 + [silence_frame()] * 10


def make_pipeline(threshold: float = 0.8, cooldown_ms: int = 0) -> RecognitionPipeline:
    provider = FakeListenerProvider()
    provider.load(None)  # type: ignore[arg-type]
    return RecognitionPipeline(
        provider=provider,
        wakewords=["computer"],
        wakeword_thresholds={"computer": threshold},
        grammar=None,
        vad_settings=VadSettings(silence_end_ms=500, pre_roll_ms=300),
        frame_ms=100,
        cooldown_ms=cooldown_ms,
        debug=True,
    )


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeListenerProvider.script.clear()
    FakeListenerProvider.wakeword_confidence = 0.9
    FakeListenerProvider.fail_load = False


def run_frames(pipeline: RecognitionPipeline, frames: list[bytes]) -> list[Any]:
    events = []
    for frame in frames:
        events.extend(pipeline.feed(frame))
    events.extend(pipeline.flush())
    return events


def test_pipeline_full_session() -> None:
    FakeListenerProvider.script.append("turn on the kitchen light")
    events = run_frames(make_pipeline(), COMMAND)
    kinds = [e.kind for e in events if e.kind != "audio_level"]
    assert kinds[:2] == ["wakeword_candidate", "partial"]
    assert "wakeword_confirmed" in kinds and "speech_start" in kinds
    assert kinds[-2:] == ["speech_end", "session_end"]
    outcome = next(e.outcome for e in events if e.kind == "session_end")
    assert outcome.transcript.text == "turn on the kitchen light"
    assert outcome.wakeword == "computer" and outcome.wakeword_confidence == 0.9
    assert outcome.end_reason == "speech_end"
    assert outcome.audio_level is not None and outcome.audio_level > -30


def test_pipeline_rejects_low_confidence_wakeword() -> None:
    FakeListenerProvider.wakeword_confidence = 0.5
    events = run_frames(make_pipeline(threshold=0.8), COMMAND)
    rejected = [e for e in events if e.kind == "wakeword_rejected"]
    assert rejected and rejected[0].data["reason"] == "below_threshold"
    assert not any(e.kind == "session_end" for e in events)


def test_pipeline_no_speech_after_wakeword() -> None:
    pipeline = make_pipeline()
    frames = [tone_frame()] * 2 + [silence_frame()] * 60
    events = run_frames(pipeline, frames)
    ends = [e for e in events if e.kind == "speech_end"]
    assert ends and ends[0].data["reason"] == "no_speech"


def test_pipeline_cooldown_suppresses_retrigger() -> None:
    pipeline = make_pipeline(cooldown_ms=5000)
    events = run_frames(pipeline, COMMAND + COMMAND)
    assert sum(1 for e in events if e.kind == "wakeword_candidate") == 1
    pipeline = make_pipeline(cooldown_ms=0)
    events = run_frames(pipeline, COMMAND + COMMAND)
    assert sum(1 for e in events if e.kind == "wakeword_candidate") == 2


def test_pipeline_max_length() -> None:
    pipeline = make_pipeline()
    pipeline.vad_settings = VadSettings(max_utterance_ms=1000, speech_start_ms=0)
    events = run_frames(pipeline, [tone_frame()] * 40)
    assert [e.data["reason"] for e in events if e.kind == "speech_end"][0] == "max_length"


# --- live runtime ------------------------------------------------------------------------------


class ScriptedSource(AudioSource):
    """Plays scripted frames, then silence (or nothing, to trigger the watchdog)."""

    frame_ms = 100

    def __init__(self, frames: list[bytes], then_silence: bool = True, fail_start: bool = False) -> None:
        self.frames = list(frames)
        self.then_silence = then_silence
        self.fail_start = fail_start
        self.started = False

    @property
    def description(self) -> str:
        return "scripted"

    def start(self) -> None:
        if self.fail_start:
            raise AudioCaptureError("device unplugged")
        self.started = True

    def stop(self) -> None:
        self.started = False

    def read(self, timeout: float) -> AudioFrame | None:
        time.sleep(0.002)
        if self.frames:
            return AudioFrame(self.frames.pop(0), time.monotonic())
        if self.then_silence:
            return AudioFrame(silence_frame(), time.monotonic())
        time.sleep(timeout)
        return None


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_live_runtime_produces_logged_result(client: TestClient, fake_provider: type[FakeListenerProvider]) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    fake_provider.script.append("turn on the kitchen light")
    service._source_factory = lambda settings: ScriptedSource(COMMAND)
    assert client.post("/api/v1/recognition/start").json()["status"] == "started"
    try:
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["results"] >= 1)
    finally:
        client.post("/api/v1/recognition/stop")
    log = client.get("/api/v1/logs/").json()["items"][0]
    assert (log["source"], log["intent_name"], log["slot_values"]) == ("mic", "light_turn_on", {"room_name": "kitchen"})
    assert log["wakeword"] == "computer" and log["wakeword_confidence"] == 0.9
    status = client.get("/api/v1/recognition/status").json()
    assert status["wakeword_detections"] == 1 and status["sessions"] == 1


def test_live_runtime_duplicate_results_are_flagged(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    client.put("/api/v1/settings/cooldown_ms", json={"value": "0"})
    client.put("/api/v1/settings/dedup_window_ms", json={"value": "60000"})
    fake_provider.script.extend(["what time is it", "what time is it"])
    published: list[dict[str, Any]] = []
    original = service.bus.publish

    def capture(event: str, data: dict[str, Any], debug_only: bool = False) -> None:
        if event == "result":
            published.append(data)
        original(event, data, debug_only)

    service.bus.publish = capture
    service._source_factory = lambda settings: ScriptedSource(COMMAND + COMMAND)
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: len(published) >= 2)
    finally:
        client.post("/api/v1/recognition/stop")
    assert [r["deduplicated"] for r in published[:2]] == [False, True]


def test_watchdog_restarts_silent_audio_source(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    client.put("/api/v1/settings/audio_watchdog_timeout_ms", json={"value": "500"})
    sources: list[ScriptedSource] = []

    def factory(settings: dict[str, Any]) -> ScriptedSource:
        source = ScriptedSource([silence_frame()] * 3, then_silence=len(sources) >= 1)
        sources.append(source)
        return source

    service._source_factory = factory
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["audio_restarts"] >= 1)
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["state"] == "listening")
    finally:
        client.post("/api/v1/recognition/stop")
    assert len(sources) >= 2
    assert sources[0].started is False  # the stalled source was closed before restarting


def test_runtime_recovers_when_device_returns(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    attempts: list[int] = []

    def factory(settings: dict[str, Any]) -> ScriptedSource:
        attempts.append(1)
        return ScriptedSource([], fail_start=len(attempts) == 1)

    service._source_factory = factory
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: len(attempts) >= 2 and client.get("/api/v1/recognition/status").json()["state"] == "listening")
    finally:
        client.post("/api/v1/recognition/stop")
    status = client.get("/api/v1/recognition/status").json()
    assert status["audio_restarts"] == 1


def test_config_change_applies_to_running_pipeline(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    service._source_factory = lambda settings: ScriptedSource([])
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["state"] == "listening")
        version = service.snapshot.version
        client.post("/api/v1/wakewords/", json={"name": "jarvis"})
        assert service.snapshot.version == version + 1
        assert "jarvis" in service.snapshot.wakewords
    finally:
        client.post("/api/v1/recognition/stop")


def test_event_bus_fan_out_is_thread_safe() -> None:
    import asyncio

    from opal_listener.service.events import EventBus

    async def main() -> list[dict[str, Any]]:
        bus = EventBus(max_queue=5)
        normal = bus.subscribe()
        debug = bus.subscribe(debug=True)
        threads = [threading.Thread(target=bus.publish, args=("result", {"i": i})) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        bus.publish("audio_level", {"dbfs": -40}, debug_only=True)
        await asyncio.sleep(0.05)
        assert normal.qsize() == 5  # bounded: oldest dropped
        items = [debug.get_nowait() for _ in range(debug.qsize())]
        bus.unsubscribe(normal)
        bus.unsubscribe(debug)
        assert bus.subscriber_count == 0
        return items

    items = asyncio.run(main())
    assert items[-1]["event"] == "audio_level"
