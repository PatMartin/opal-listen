"""Optional faster-whisper second pass (install with the 'whisper' extra)."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from opal_listener.service.providers.base import ProviderConfig, ProviderError, SttRefiner, Transcript

log = logging.getLogger(__name__)


class FasterWhisperRefiner(SttRefiner):
    name = "faster_whisper"

    def __init__(self) -> None:
        self._model: Any = None
        self._model_name: str | None = None
        self._prompt: str | None = None

    def load(self, config: ProviderConfig) -> None:
        model_name = config.model_paths.get("whisper_model") or config.options.get("whisper_model", "tiny.en")
        if self._model is not None and self._model_name == model_name:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ProviderError("faster-whisper not installed (pip install 'opal-listener[whisper]')") from exc
        compute_type = config.options.get("whisper_compute_type", "int8")
        log.info("loading faster-whisper model %s (%s)", model_name, compute_type)
        self._model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
        self._model_name = model_name
        grammar = config.options.get("grammar") or []
        self._prompt = ", ".join(grammar[:100]) or None

    def refine(self, pcm: bytes, draft: Transcript) -> Transcript:
        if self._model is None:
            raise ProviderError("whisper refiner not loaded")
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(
            audio, language="en", beam_size=1, initial_prompt=self._prompt, vad_filter=False
        )
        segments = list(segments)
        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            return draft
        probs = [float(np.exp(s.avg_logprob)) for s in segments]
        return Transcript(text=text, confidence=round(sum(probs) / len(probs), 4))
