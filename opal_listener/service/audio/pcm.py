"""Audio level helpers, WAV loading and resampling. Internal format: 16 kHz mono int16 PCM."""

from __future__ import annotations

import io
import math
import wave
from fractions import Fraction

import numpy as np
from scipy.signal import resample_poly

TARGET_RATE = 16000
SILENCE_DBFS = -96.0


class AudioFormatError(ValueError):
    pass


def rms_dbfs(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16)
    if samples.size == 0:
        return SILENCE_DBFS
    rms = math.sqrt(float(np.mean(samples.astype(np.float64) ** 2)))
    if rms <= 0:
        return SILENCE_DBFS
    return max(SILENCE_DBFS, 20.0 * math.log10(rms / 32768.0))


def resample_int16(samples: np.ndarray, src_rate: int, dst_rate: int = TARGET_RATE) -> np.ndarray:
    if src_rate == dst_rate:
        return samples.astype(np.int16, copy=False)
    ratio = Fraction(dst_rate, src_rate).limit_denominator(1000)
    out = resample_poly(samples.astype(np.float32), ratio.numerator, ratio.denominator)
    return np.clip(np.round(out), -32768, 32767).astype(np.int16)


def to_mono(samples: np.ndarray, channels: int) -> np.ndarray:
    if channels == 1:
        return samples
    return samples.reshape(-1, channels).mean(axis=1).astype(np.int16)


def load_wav_bytes(data: bytes) -> bytes:
    """Decode a PCM WAV file to 16 kHz mono int16 bytes."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioFormatError("not a RIFF/WAVE file")
    try:
        with wave.open(io.BytesIO(data)) as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            raw = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioFormatError(f"invalid WAV file: {exc}") from exc
    if width != 2:
        raise AudioFormatError("only 16-bit PCM WAV is supported")
    if channels not in (1, 2):
        raise AudioFormatError("only mono or stereo WAV is supported")
    if not 8000 <= rate <= 96000:
        raise AudioFormatError(f"unsupported sample rate {rate}")
    samples = to_mono(np.frombuffer(raw, dtype="<i2"), channels)
    return resample_int16(samples, rate).tobytes()


def write_wav_bytes(pcm: bytes, rate: int = TARGET_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def iter_frames(pcm: bytes, frame_ms: int, rate: int = TARGET_RATE) -> list[bytes]:
    size = int(rate * frame_ms / 1000) * 2
    return [pcm[i : i + size] for i in range(0, len(pcm), size)]
