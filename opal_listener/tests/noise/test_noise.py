"""Noise robustness: golden audio fixtures mixed with seeded background noise at fixed SNRs."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from opal_listener.service.audio.pcm import write_wav_bytes

AUDIO_DIR = Path(__file__).resolve().parents[1] / "audio"
MANIFEST = yaml.safe_load((AUDIO_DIR / "manifest.yaml").read_text())["fixtures"]
GOLDEN = [f for f in MANIFEST if f["set"] == "golden"]

pytestmark = [pytest.mark.vosk, pytest.mark.slow]

# Minimum fraction of golden fixtures that must still match exactly at each noise condition.
# Baselines measured with vosk-model-small-en-us-0.15 on espeak voices; raise them as recognition improves.
REQUIRED_PASS_RATE = {("white", 20): 0.7, ("pink", 20): 0.7, ("white", 10): 0.4, ("pink", 10): 0.4}


def read_pcm(path: Path) -> np.ndarray:
    with wave.open(str(path)) as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).astype(np.float64)


def noise(kind: str, n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    if kind == "white":
        return white
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n)
    freqs[0] = freqs[1]
    return np.fft.irfft(spectrum / np.sqrt(freqs), n)


def mix(speech: np.ndarray, kind: str, snr_db: float, seed: int) -> bytes:
    active = speech[np.abs(speech) > 0.02 * np.abs(speech).max()]
    speech_power = float(np.mean(active**2))
    n = noise(kind, len(speech), seed)
    n *= np.sqrt(speech_power / (10 ** (snr_db / 10)) / float(np.mean(n**2)))
    mixed = np.clip(speech + n, -32768, 32767).astype(np.int16)
    return write_wav_bytes(mixed.tobytes())


def recognize(client: TestClient, wav: bytes) -> dict[str, Any]:
    response = client.post(
        "/api/v1/recognition/test_audio",
        files={"file": ("noisy.wav", io.BytesIO(wav), "audio/wav")},
        params={"require_wakeword": True, "save_log": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(("kind", "snr_db"), list(REQUIRED_PASS_RATE), ids=lambda v: str(v))
def test_golden_fixtures_under_noise(client: TestClient, kind: str, snr_db: int) -> None:
    failures: list[str] = []
    for seed, fixture in enumerate(GOLDEN):
        result = recognize(client, mix(read_pcm(AUDIO_DIR / "fixtures" / fixture["file"]), kind, snr_db, seed))
        expect = fixture["expect"]
        if result["intent_name"] != expect["intent_name"] or result["slot_values"] != expect["slot_values"]:
            failures.append(f"{fixture['file']}: {result['transcript']!r} -> {result['intent_name']}")
    pass_rate = 1 - len(failures) / len(GOLDEN)
    assert pass_rate >= REQUIRED_PASS_RATE[(kind, snr_db)], "\n".join(failures)


def test_pure_noise_never_triggers_wakeword(client: TestClient) -> None:
    for seed, kind in enumerate(("white", "pink")):
        loud = np.clip(noise(kind, 16000 * 4, seed) * 3000, -32768, 32767).astype(np.int16)
        result = recognize(client, write_wav_bytes(loud.tobytes()))
        assert result["wakeword"] is None, result
        assert result["no_match_reason"] == "no_wakeword"
