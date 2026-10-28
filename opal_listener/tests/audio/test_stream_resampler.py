"""Streaming resampler correctness: continuity, timing, frequency preservation, no drift."""

from __future__ import annotations

import numpy as np
import pytest

from opal_listener.service.audio.capture import StreamResampler

TARGET = 16000


def _tone(rate: int, seconds: float, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * freq * t) * 20000).astype(np.int16)


def _run(resampler: StreamResampler, pcm: np.ndarray, block: int) -> np.ndarray:
    out = [resampler.process(pcm[i : i + block]) for i in range(0, len(pcm), block)]
    out.append(resampler.flush())
    return np.concatenate(out)


@pytest.mark.parametrize("rate", [16000, 32000, 44100, 48000])
def test_continuous_tone_duration_and_frequency(rate: int) -> None:
    pcm = _tone(rate, 2.0)
    out = _run(StreamResampler(rate), pcm, 480)
    expected = int(2.0 * TARGET)
    assert abs(len(out) - expected) <= 8  # duration preserved within half a 5 ms block
    # frequency preserved: dominant FFT bin stays at 440 Hz
    spectrum = np.abs(np.fft.rfft(out.astype(np.float64) * np.hanning(len(out))))
    peak_hz = np.fft.rfftfreq(len(out), 1 / TARGET)[np.argmax(spectrum)]
    assert abs(peak_hz - 440.0) < 20.0


@pytest.mark.parametrize("rate", [44100, 48000, 32000])
def test_block_size_independence(rate: int) -> None:
    pcm = _tone(rate, 1.0)
    outputs = {b: _run(StreamResampler(rate), pcm, b) for b in (160, 480, 1024, 4096)}
    base = outputs[4096]
    for out in outputs.values():
        assert abs(len(out) - len(base)) <= 2
        n = min(len(out), len(base)) - 40  # skip edge where block boundaries differ
        assert np.max(np.abs(out[20 : 20 + n].astype(int) - base[20 : 20 + n].astype(int))) <= 2


def test_cross_block_continuity_no_step() -> None:
    pcm = _tone(44100, 1.0, freq=220.0)
    out = _run(StreamResampler(44100), pcm, 480)
    # seamless output: a 220 Hz tone at amplitude 20000 diffs < ~1730/sample at 16 kHz; a block
    # boundary step would be a large isolated jump
    diff = np.abs(np.diff(out.astype(np.float64)))
    assert diff.max() < 2000


def test_fractional_ratio_no_long_term_drift() -> None:
    pcm = _tone(44100, 10.0)
    out = _run(StreamResampler(44100), pcm, 441)
    expected = len(pcm) * TARGET / 44100
    assert abs(len(out) - expected) <= 2  # no cumulative drift over 10 s


def test_integer_rates_exact() -> None:
    for rate, factor in ((32000, 2), (48000, 3)):
        pcm = _tone(rate, 1.0)
        out = _run(StreamResampler(rate), pcm, rate // 10)
        assert abs(len(out) - TARGET) <= factor + 1


def test_partial_frames_and_flush() -> None:
    rate = 44100
    pcm = _tone(rate, 0.3)
    r = StreamResampler(rate)
    outs = [r.process(pcm[:100]), r.process(pcm[100:337]), r.process(pcm[337:])]
    tail = r.flush()
    total = sum(len(o) for o in outs) + len(tail)
    assert abs(total - len(pcm) * TARGET / rate) <= 2


def test_repeated_start_stop_identical() -> None:
    pcm = _tone(44100, 0.5)
    runs = [_run(StreamResampler(44100), pcm, 480) for _ in range(3)]
    assert all(np.array_equal(runs[0], r) for r in runs)


def test_interface_unchanged_passthrough() -> None:
    pcm = _tone(TARGET, 0.1)
    r = StreamResampler(TARGET)
    out = r.process(pcm)
    assert out.dtype == np.int16 and len(out) == len(pcm)
