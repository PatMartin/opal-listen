"""Audio fixture tests: every .wav in the manifest goes through the full pipeline via the REST API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
MANIFEST = yaml.safe_load((HERE / "manifest.yaml").read_text())["fixtures"]

pytestmark = pytest.mark.vosk


def by_set(name: str) -> list[dict[str, Any]]:
    return [f for f in MANIFEST if f["set"] == name]


def post_audio(client: TestClient, path: Path, **params: Any) -> dict[str, Any]:
    with path.open("rb") as fh:
        response = client.post(
            "/api/v1/recognition/test_audio", files={"file": (path.name, fh, "audio/wav")}, params=params
        )
    assert response.status_code == 200, response.text
    return response.json()


def assert_expected(result: dict[str, Any], expect: dict[str, Any]) -> None:
    if "wakeword" in expect:
        assert result["wakeword"] == expect["wakeword"], result
    if "intent_name" in expect:
        assert result["intent_name"] == expect["intent_name"], result
        assert result["matched"] == (expect["intent_name"] != "no_match"), result
    if "slot_values" in expect:
        assert result["slot_values"] == expect["slot_values"], result


@pytest.mark.parametrize("fixture", by_set("golden"), ids=lambda f: f["file"])
def test_golden_audio(client: TestClient, fixture: dict[str, Any]) -> None:
    result = post_audio(client, HERE / "fixtures" / fixture["file"], require_wakeword=True)
    assert_expected(result, fixture["expect"])
    assert result["wakeword_confidence"] is not None and result["wakeword_confidence"] >= 0.8
    for key in ("wakeword_latency_ms", "stt_latency_ms", "intent_latency_ms", "total_latency_ms"):
        assert isinstance(result[key], int) and result[key] >= 0, key


@pytest.mark.parametrize("fixture", by_set("negative"), ids=lambda f: f["file"])
def test_negative_audio(client: TestClient, fixture: dict[str, Any]) -> None:
    result = post_audio(client, HERE / "fixtures" / fixture["file"], require_wakeword=True)
    assert_expected(result, fixture["expect"])
    assert result["no_match_reason"]


@pytest.mark.parametrize("fixture", by_set("no_wakeword"), ids=lambda f: f["file"])
def test_wakeword_false_positive(client: TestClient, fixture: dict[str, Any]) -> None:
    """Speech without the wakeword must not trigger it."""
    result = post_audio(client, HERE / "fixtures" / fixture["file"], require_wakeword=True)
    assert result["wakeword"] is None, result
    assert result["matched"] is False
    assert result["no_match_reason"] == "no_wakeword"


@pytest.mark.parametrize("fixture", by_set("golden") + by_set("negative"), ids=lambda f: f["file"])
def test_wakeword_false_negative(client: TestClient, fixture: dict[str, Any]) -> None:
    """Every fixture that starts with the wakeword must be detected."""
    result = post_audio(client, HERE / "fixtures" / fixture["file"], require_wakeword=True, save_log=False)
    assert result["wakeword"] == "computer", result


def test_command_without_wakeword_when_not_required(client: TestClient) -> None:
    fixture = next(f for f in MANIFEST if f["file"] == "nowake_command_only.wav")
    result = post_audio(client, HERE / "fixtures" / fixture["file"], require_wakeword=False)
    assert_expected(result, fixture["expect"])


def test_silence_is_graceful_no_match(client: TestClient, tmp_path: Path) -> None:
    from opal_listener.service.audio.pcm import write_wav_bytes

    wav = tmp_path / "silence.wav"
    wav.write_bytes(write_wav_bytes(b"\x00\x00" * 16000 * 2))
    result = post_audio(client, wav)
    assert result["matched"] is False
    assert result["intent_name"] == "no_match"


def test_audio_results_are_logged_with_performance(client: TestClient) -> None:
    post_audio(client, HERE / "fixtures" / "golden_kitchen_light_on.wav")
    last = client.get("/api/v1/performance/last").json()
    assert last["matched"] is True
    log = client.get(f"/api/v1/logs/{last['recognition_log_id']}").json()
    assert log["source"] == "audio"
    assert log["intent_name"] == "light_turn_on"


def test_negative_audio_is_captured_as_failure(client: TestClient) -> None:
    post_audio(client, HERE / "fixtures" / "negative_weather.wav")
    failed = client.get("/api/v1/failed_utterances/").json()
    assert len(failed) == 1
    assert failed[0]["notes"].startswith("auto-captured")
    assert failed[0]["recognition_log_id"] is not None
