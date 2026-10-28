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
from opal_listener.tests.conftest import patch_settings, silence_frame, tone_frame

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


def test_live_mic_match_is_published_to_the_event_bus(client: TestClient, fake_provider: type) -> None:
    from opal_listener.service.listener_service import INTENT_MATCH_TOPIC

    service = client.app.state.listener  # type: ignore[attr-defined]
    publisher = service.event_publisher
    assert patch_settings(client, event_publication_enabled="true").status_code == 200
    fake_provider.script.append("turn on the kitchen light")
    service._source_factory = lambda settings: ScriptedSource(COMMAND)
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["results"] >= 1)
    finally:
        client.post("/api/v1/recognition/stop")
    assert len(publisher.events) == 1
    topic, payload = publisher.events[0]
    assert topic == INTENT_MATCH_TOPIC
    assert payload["matched"] is True and payload["source"] == "mic"
    assert payload["intent_name"] == "light_turn_on" and payload["slot_values"] == {"room_name": "kitchen"}


def test_event_publication_enable_disable(client: TestClient, fake_provider: type) -> None:
    """event_publication_enabled gates external publication live: disabled matches are logged and
    streamed locally but never reach the event publisher; enabling PATCHes take effect without a
    service restart (the publisher connects on the next match), and disabling suppresses again."""
    from opal_listener.service.listener_service import INTENT_MATCH_TOPIC

    service = client.app.state.listener  # type: ignore[attr-defined]
    publisher = service.event_publisher
    assert client.get("/api/v1/settings/").json()["event_publication_enabled"] == "false"

    def run_once(expected_log_total: int) -> None:
        fake_provider.script.append("turn on the kitchen light")
        service._source_factory = lambda settings: ScriptedSource(list(COMMAND))
        client.post("/api/v1/recognition/start")
        try:
            wait_for(lambda: client.get("/api/v1/logs/count").json()["total"] >= expected_log_total)
        finally:
            client.post("/api/v1/recognition/stop")

    run_once(1)
    assert publisher.events == []  # disabled: the match was logged but not published externally
    assert client.get("/api/v1/logs/count").json()["total"] == 1

    # dedup_window_ms=0 keeps this test focused on the publication toggle (dedup is tested separately)
    updated = patch_settings(client, event_publication_enabled="true", dedup_window_ms="0")
    assert updated.status_code == 200 and updated.json()["event_publication_enabled"] == "true"
    run_once(2)
    assert len(publisher.events) == 1
    topic, payload = publisher.events[0]
    assert topic == INTENT_MATCH_TOPIC and payload["intent_name"] == "light_turn_on"

    updated = patch_settings(client, event_publication_enabled="false")
    assert updated.status_code == 200 and updated.json()["event_publication_enabled"] == "false"
    run_once(3)
    assert len(publisher.events) == 1  # no further events after disabling


def test_live_runtime_duplicate_results_are_flagged(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    updated = patch_settings(
        client, cooldown_ms="0", dedup_window_ms="60000", event_publication_enabled="true"
    )
    assert updated.status_code == 200
    assert (updated.json()["cooldown_ms"], updated.json()["dedup_window_ms"]) == ("0", "60000")
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
    # both results are logged and streamed locally, but only the first is published externally
    publisher = service.event_publisher
    assert len(publisher.events) == 1
    assert publisher.events[0][1]["intent_name"] == "get_time"
    assert publisher.events[0][1]["deduplicated"] is False


def test_live_runtime_repeat_after_dedup_window_publishes_again(
    client: TestClient, fake_provider: type
) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    publisher = service.event_publisher
    updated = patch_settings(client, dedup_window_ms="500", event_publication_enabled="true")
    assert updated.status_code == 200

    def run_once(expected_logs: int) -> None:
        fake_provider.script.append("what time is it")
        service._source_factory = lambda settings: ScriptedSource(list(COMMAND))
        client.post("/api/v1/recognition/start")
        try:
            wait_for(lambda: client.get("/api/v1/logs/count").json()["total"] >= expected_logs)
        finally:
            client.post("/api/v1/recognition/stop")

    run_once(1)
    time.sleep(0.7)  # longer than the 500 ms dedup window
    run_once(2)
    assert [e[1]["intent_name"] for e in publisher.events] == ["get_time", "get_time"]


def test_live_runtime_different_slots_publish_distinct_events(
    client: TestClient, fake_provider: type
) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    publisher = service.event_publisher
    updated = patch_settings(
        client, cooldown_ms="0", dedup_window_ms="60000", event_publication_enabled="true"
    )
    assert updated.status_code == 200
    fake_provider.script.extend(["turn on the kitchen light", "turn on the bedroom light"])
    service._source_factory = lambda settings: ScriptedSource(COMMAND + COMMAND)
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: client.get("/api/v1/logs/count").json()["total"] >= 2)
    finally:
        client.post("/api/v1/recognition/stop")
    rooms = [e[1]["slot_values"]["room_name"] for e in publisher.events]
    assert rooms == ["kitchen", "bedroom"]


def test_watchdog_restarts_silent_audio_source(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    updated = patch_settings(client, audio_watchdog_timeout_ms="500")
    assert updated.status_code == 200 and updated.json()["audio_watchdog_timeout_ms"] == "500"
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


class _BlockingSource(AudioSource):
    """read() blocks until released — simulates a worker stuck in a blocking call at shutdown."""

    def __init__(self) -> None:
        self.release = threading.Event()

    @property
    def description(self) -> str:
        return "blocking"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def read(self, timeout: float) -> AudioFrame | None:
        self.release.wait(30)
        return None


def test_concurrent_start_launches_one_worker(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    service._source_factory = lambda settings: ScriptedSource([silence_frame()])
    results: list[dict[str, str]] = []
    threads = [threading.Thread(target=lambda: results.append(service.start())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    try:
        assert len(results) == 8
        assert [r["status"] for r in results].count("started") == 1
        assert [r["status"] for r in results].count("already_running") == 7
        workers = [t for t in threading.enumerate() if t.name == "listener-runtime" and t.is_alive()]
        assert len(workers) == 1  # at most one active worker
        assert service.running
    finally:
        service.stop()


def test_concurrent_stop_requests(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    service._source_factory = lambda settings: ScriptedSource([silence_frame()])
    assert service.start()["status"] == "started"
    wait_for(lambda: service.running)
    results: list[dict[str, str]] = []
    threads = [threading.Thread(target=lambda: results.append(service.stop())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert len(results) == 8
    assert all(r["status"] in ("stopped", "not_running") for r in results)
    assert not service.running
    assert service._thread is None  # type: ignore[attr-defined]


def test_rapid_start_stop_start(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    service._source_factory = lambda settings: ScriptedSource([silence_frame()])
    try:
        for _ in range(5):
            assert service.start()["status"] in ("started", "stopping")
            assert service.stop()["status"] == "stopped"
        assert not service.running
        assert service.start()["status"] == "started"
        workers = [t for t in threading.enumerate() if t.name == "listener-runtime" and t.is_alive()]
        assert len(workers) == 1
    finally:
        service.stop()


def test_stop_reports_stopping_while_worker_blocked(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    source = _BlockingSource()
    service._source_factory = lambda settings: source
    assert service.start()["status"] == "started"
    wait_for(lambda: service.running)
    try:
        assert service.stop()["status"] == "stopping"  # join timed out on the blocked worker
        assert service.running  # not reported stopped while the worker is alive
        assert service._thread is not None  # type: ignore[attr-defined]
        status = client.get("/api/v1/recognition/status").json()
        assert status["running"] is True and status["state"] == "stopping"
        source.release.set()  # unblock the worker so it can observe _stop and exit
        wait_for(lambda: not service.running, timeout=10)
        assert service.stop()["status"] == "stopped"
        assert service._thread is None  # type: ignore[attr-defined]
    finally:
        service.stop()


def test_repeated_shutdown_is_idempotent(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    service._source_factory = lambda settings: ScriptedSource([silence_frame()])
    assert service.start()["status"] == "started"
    wait_for(lambda: service.running)
    service.shutdown()
    service.shutdown()  # second call must not raise or re-close
    assert not service.running
    assert service.event_publisher.closed  # type: ignore[attr-defined]


class _SlowSource(ScriptedSource):
    """Scripted source with slower frame delivery so a command session stays in progress."""

    def read(self, timeout: float) -> AudioFrame | None:
        time.sleep(0.02)
        if self.frames:
            return AudioFrame(self.frames.pop(0), time.monotonic())
        return AudioFrame(silence_frame(), time.monotonic())


@pytest.mark.parametrize(
    ("key", "value"),
    [("audio_device", "usb-mic"), ("audio_capture_rate", "48000"), ("audio_block_ms", "200")],
)
def test_capture_setting_change_restarts_source(
    client: TestClient, fake_provider: type, key: str, value: str
) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    sources: list[ScriptedSource] = []
    seen: list[dict[str, Any]] = []

    def factory(settings: dict[str, Any]) -> ScriptedSource:
        seen.append(dict(settings))
        source = ScriptedSource([silence_frame()])
        source.frame_ms = int(settings["audio_block_ms"])
        sources.append(source)
        return source

    service._source_factory = factory
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["state"] == "listening")
        assert len(sources) == 1
        patch_settings(client, **{key: value})
        wait_for(lambda: len(sources) >= 2)
        expected = value if key == "audio_device" else int(value)
        assert seen[-1][key] == expected  # new stream opened with the updated capture settings
        assert sources[0].started is False  # old stream closed — no overlapping captures
        assert sources[1].started  # new stream is the live one
        if key == "audio_block_ms":
            assert sources[1].frame_ms == 200  # frame duration follows the new setting
    finally:
        client.post("/api/v1/recognition/stop")


def test_pipeline_only_change_does_not_restart_source(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    sources: list[ScriptedSource] = []

    def factory(settings: dict[str, Any]) -> ScriptedSource:
        source = ScriptedSource([silence_frame()])
        sources.append(source)
        return source

    service._source_factory = factory
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["state"] == "listening")
        patch_settings(client, cooldown_ms="1000")
        time.sleep(0.3)  # give the runtime several iterations to (not) react
        assert len(sources) == 1  # pipeline rebuilt in place — capture stream untouched
        assert sources[0].started
    finally:
        client.post("/api/v1/recognition/stop")


def test_capture_restart_waits_for_session_end(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    sources: list[ScriptedSource] = []

    def factory(settings: dict[str, Any]) -> ScriptedSource:
        source = _SlowSource(list(COMMAND))
        source.frame_ms = int(settings["audio_block_ms"])
        sources.append(source)
        return source

    service._source_factory = factory
    fake_provider.script.append("turn on the kitchen light")
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["state"] == "capturing")
        patch_settings(client, audio_block_ms="200")  # lands mid-session
        wait_for(lambda: client.get("/api/v1/logs/count").json()["total"] >= 1)
        wait_for(lambda: len(sources) >= 2)
        # the session played out on the old source before the restart: only post-session trailing
        # silence frames remain unread (a mid-session swap would leave ~20 of the 25 frames)
        assert len(sources[0].frames) <= 10
        assert sources[1].frame_ms == 200
    finally:
        client.post("/api/v1/recognition/stop")


def test_failed_capture_restart_uses_last_known_good(client: TestClient, fake_provider: type) -> None:
    service = client.app.state.listener  # type: ignore[attr-defined]
    sources: list[ScriptedSource] = []

    def factory(settings: dict[str, Any]) -> ScriptedSource:
        source = ScriptedSource(list(COMMAND), fail_start=settings["audio_device"] == "bad-device")
        sources.append(source)
        return source

    service._source_factory = factory
    client.post("/api/v1/recognition/start")
    try:
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["state"] == "listening")
        patch_settings(client, audio_device="bad-device")
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["audio_restarts"] >= 1)
        status = client.get("/api/v1/recognition/status").json()
        assert "restart failed" in status["last_error"]
        # last-known-good stream was restored and is still the live capture
        assert sources[0].started is True
        assert sources[1].started is False  # the failed replacement never came up
        # a later change retries the reopen and recovers
        patch_settings(client, audio_device="usb-ok")
        wait_for(lambda: len(sources) >= 3)
        wait_for(lambda: client.get("/api/v1/recognition/status").json()["state"] == "listening")
        assert sources[0].started is False  # LKG finally retired once the new stream works
        assert sources[2].started is True
    finally:
        client.post("/api/v1/recognition/stop")
