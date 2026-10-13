"""Deterministic provider for tests and model-less development.

Wakeword: two consecutive loud frames (> -35 dBFS) produce a candidate, confirmed a few frames later.
Transcription: returns scripted transcripts in order (see FakeListenerProvider.script).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from typing import ClassVar

from opal_listener.service.audio.pcm import rms_dbfs
from opal_listener.service.providers.base import (
    ListenerProvider,
    ProviderCheck,
    ProviderConfig,
    ProviderError,
    Transcriber,
    TranscriberUpdate,
    Transcript,
    WakewordDetector,
    WakewordEvent,
    WakewordEventKind,
)

LOUD_DBFS = -35.0


class FakeWakewordDetector(WakewordDetector):
    def __init__(self, wakewords: Sequence[str], confidence: float) -> None:
        self._wakeword = wakewords[0] if wakewords else None
        self._confidence = confidence
        self.reset()

    def reset(self) -> None:
        self._loud = 0
        self._since_candidate: int | None = None

    def accept(self, frame: bytes) -> list[WakewordEvent]:
        if self._wakeword is None:
            return []
        if self._since_candidate is not None:
            self._since_candidate += 1
            if self._since_candidate >= 3:
                self._since_candidate = None
                return [WakewordEvent(WakewordEventKind.CONFIRMED, self._wakeword, self._confidence)]
            return []
        self._loud = self._loud + 1 if rms_dbfs(frame) > LOUD_DBFS else 0
        if self._loud >= 2:
            self._loud = 0
            self._since_candidate = 0
            return [WakewordEvent(WakewordEventKind.CANDIDATE, self._wakeword)]
        return []

    def flush(self) -> list[WakewordEvent]:
        if self._since_candidate is not None:
            self._since_candidate = None
            return [WakewordEvent(WakewordEventKind.CONFIRMED, self._wakeword, self._confidence)]
        return []


class FakeTranscriber(Transcriber):
    def __init__(self, text: str) -> None:
        self._text = text

    def accept(self, frame: bytes) -> TranscriberUpdate:
        return TranscriberUpdate(partial=self._text.split(" ")[0] if self._text else None)

    def finalize(self) -> Transcript:
        return Transcript(text=self._text, confidence=0.95 if self._text else None)


class FakeListenerProvider(ListenerProvider):
    name = "fake"
    script: ClassVar[deque[str]] = deque()
    wakeword_confidence: ClassVar[float] = 0.9
    fail_load: ClassVar[bool] = False

    def __init__(self) -> None:
        self._loaded = False

    def check(self, config: ProviderConfig) -> list[ProviderCheck]:
        return [ProviderCheck("fake_model", True, "fake provider needs no model")]

    def load(self, config: ProviderConfig) -> None:
        if self.fail_load:
            raise ProviderError("fake provider configured to fail")
        self._loaded = True

    def is_loaded(self) -> bool:
        return self._loaded

    def create_wakeword_detector(self, wakewords: Sequence[str]) -> WakewordDetector:
        return FakeWakewordDetector(list(wakewords), self.wakeword_confidence)

    def create_transcriber(self, grammar: Sequence[str] | None) -> Transcriber:
        text = self.script.popleft() if self.script else ""
        return FakeTranscriber(text)
