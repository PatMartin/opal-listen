"""Microphone capture (sounddevice/PortAudio) with resampling to 16 kHz mono int16."""

from __future__ import annotations

import contextlib
import logging
import queue
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.signal import firwin, lfilter

from opal_listener.service.audio.pcm import TARGET_RATE

log = logging.getLogger(__name__)

PREFERRED_RATES = (16000, 48000, 32000, 44100)


class AudioCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioFrame:
    pcm: bytes
    captured_at: float  # time.monotonic() when the block arrived


def _sounddevice() -> Any:
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:  # PortAudio missing
        raise AudioCaptureError(f"sounddevice unavailable: {exc}") from exc
    return sd


def list_input_devices() -> list[dict[str, Any]]:
    try:
        sd = _sounddevice()
        devices = sd.query_devices()
        default_in = sd.default.device[0] if sd.default.device else -1
    except Exception as exc:  # noqa: BLE001 - any PortAudio failure means "no devices"
        log.warning("cannot query audio devices: %s", exc)
        return []
    return [
        {
            "index": idx,
            "name": dev["name"],
            "max_input_channels": int(dev["max_input_channels"]),
            "default_sample_rate": float(dev["default_samplerate"]),
            "is_default": idx == default_in,
        }
        for idx, dev in enumerate(devices)
        if dev["max_input_channels"] > 0
    ]


def resolve_device(spec: str) -> int | None:
    """'' -> default device (None), digits -> index, otherwise case-insensitive name substring."""
    spec = spec.strip()
    if not spec:
        return None
    if spec.isdigit():
        return int(spec)
    for dev in list_input_devices():
        if spec.lower() in dev["name"].lower():
            return int(dev["index"])
    raise AudioCaptureError(f"no input device matching '{spec}'")


class StreamResampler:
    """Stateful resampler to 16 kHz.

    Integer ratios use a streaming FIR decimator (filter state carried across blocks). Non-integer
    ratios (e.g. 44.1 kHz) stream through a buffered linear interpolator with a fractional output
    phase accumulator, so consecutive blocks stitch seamlessly and long captures cannot drift.
    Latency: identity and fractional paths emit ~one input block of delay; the FIR path adds a
    fixed group delay of (len(taps)-1)/(2*src_rate) s (~2 ms at 32 kHz).
    """

    def __init__(self, src_rate: int, dst_rate: int = TARGET_RATE) -> None:
        self.src_rate = src_rate
        self.dst_rate = dst_rate
        self._factor = src_rate // dst_rate if src_rate % dst_rate == 0 else 0
        if self._factor > 1:
            self._taps = firwin(63, 0.9 * (dst_rate / 2), fs=src_rate)
            self._zi = np.zeros(len(self._taps) - 1)
            self._phase = 0
        else:
            self._step = src_rate / dst_rate
            self._buf = np.empty(0, dtype=np.float64)
            self._pos = 0.0  # absolute position (in input samples) of the next output sample

    def process(self, samples: np.ndarray) -> np.ndarray:
        if self.src_rate == self.dst_rate:
            return samples.astype(np.int16, copy=False)
        if self._factor > 1:
            filtered, self._zi = lfilter(self._taps, 1.0, samples.astype(np.float64), zi=self._zi)
            out = filtered[self._phase :: self._factor]
            self._phase = (self._phase - len(samples)) % self._factor
            return np.clip(np.round(out), -32768, 32767).astype(np.int16)
        self._buf = np.concatenate([self._buf, samples.astype(np.float64)])
        return self._drain(final=False)

    def _drain(self, final: bool) -> np.ndarray:
        # emit output samples at positions _pos, _pos+_step, ... while the buffer covers them
        last_idx = len(self._buf) - 1 if final else len(self._buf) - 2
        n_out = 0 if last_idx < self._pos else int((last_idx - self._pos) // self._step) + 1
        if n_out <= 0:
            if final:
                self._buf = np.empty(0, dtype=np.float64)
            return np.empty(0, dtype=np.int16)
        idx = self._pos + np.arange(n_out) * self._step
        i0 = idx.astype(np.int64)
        frac = idx - i0
        i1 = np.minimum(i0 + 1, len(self._buf) - 1)
        out = self._buf[i0] * (1.0 - frac) + self._buf[i1] * frac
        consume = int(i0[-1])  # input before the last emitted sample is fully consumed
        self._buf = self._buf[consume:]
        self._pos = idx[-1] + self._step - consume
        if final:
            self._buf = np.empty(0, dtype=np.float64)
        return np.clip(np.round(out), -32768, 32767).astype(np.int16)

    def flush(self) -> np.ndarray:
        """End of stream: emit all remaining buffered samples (non-integer ratios only)."""
        if self.src_rate == self.dst_rate or self._factor > 1:
            return np.empty(0, dtype=np.int16)
        return self._drain(final=True)


class AudioSource(ABC):
    frame_ms: int

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def read(self, timeout: float) -> AudioFrame | None: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    def capture_rate(self) -> int | None:
        return None


class MicrophoneSource(AudioSource):
    def __init__(self, device: str = "", frame_ms: int = 100, capture_rate: int = 0, max_queue: int = 50) -> None:
        self.device_spec = device
        self.frame_ms = frame_ms
        self.requested_rate = capture_rate
        self._queue: queue.Queue[tuple[np.ndarray, float]] = queue.Queue(maxsize=max_queue)
        self._stream: Any = None
        self._rate: int | None = None
        self._resampler: StreamResampler | None = None
        self._device_name = "default"
        self.overflows = 0
        self.dropped_blocks = 0

    @property
    def description(self) -> str:
        return self._device_name

    @property
    def capture_rate(self) -> int | None:
        return self._rate

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def queue_capacity(self) -> int:
        return self._queue.maxsize

    def _pick_rate(self, sd: Any, device: int | None) -> int:
        candidates = [self.requested_rate] if self.requested_rate else list(PREFERRED_RATES)
        info = sd.query_devices(device, kind="input")
        candidates.append(int(info["default_samplerate"]))
        for rate in candidates:
            try:
                sd.check_input_settings(device=device, samplerate=rate, channels=1, dtype="int16")
                return rate
            except Exception:  # noqa: BLE001, S112
                continue
        raise AudioCaptureError("no supported sample rate for input device")

    def start(self) -> None:
        sd = _sounddevice()
        try:
            device = resolve_device(self.device_spec)
            info = sd.query_devices(device, kind="input")
            self._device_name = str(info["name"])
            self._rate = self._pick_rate(sd, device)
            self._resampler = StreamResampler(self._rate)
            self._stream = sd.InputStream(
                samplerate=self._rate,
                channels=1,
                dtype="int16",
                blocksize=int(self._rate * self.frame_ms / 1000),
                device=device,
                callback=self._callback,
            )
            self._stream.start()
        except AudioCaptureError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AudioCaptureError(f"cannot open microphone: {exc}") from exc
        log.info("microphone '%s' opened at %s Hz", self._device_name, self._rate)

    def _callback(self, indata: np.ndarray, _frames: int, _time: Any, status: Any) -> None:
        if status and getattr(status, "input_overflow", False):
            self.overflows += 1
        item = (indata[:, 0].copy(), time.monotonic())
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            self.dropped_blocks += 1
            with contextlib.suppress(queue.Empty):
                self._queue.get_nowait()
            self._queue.put_nowait(item)

    def read(self, timeout: float) -> AudioFrame | None:
        if self._stream is not None and not self._stream.active:
            raise AudioCaptureError("microphone stream stopped")
        try:
            samples, captured_at = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        assert self._resampler is not None
        return AudioFrame(self._resampler.process(samples).tobytes(), captured_at)

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("error closing microphone: %s", exc)
        while not self._queue.empty():
            self._queue.get_nowait()
