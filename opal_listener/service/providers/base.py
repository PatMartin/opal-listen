"""Speech-engine abstractions: pluggable wakeword detection and speech-to-text engines.

`WakewordEngine` and `TranscriptionEngine` are independently selectable roles (the
`wakeword_engine` / `transcription_engine` settings); `ListenerProvider` is the combined
compatibility interface implemented by engines like Vosk that serve both. The
listener_service only depends on these interfaces. Engines receive 16 kHz mono int16 PCM.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar


class ProviderError(RuntimeError):
    pass


class WakewordEventKind(StrEnum):
    CANDIDATE = "candidate"  # early (partial) hit: open a command session, confidence may be unknown
    CONFIRMED = "confirmed"  # final hit with confidence
    REJECTED = "rejected"  # a previous candidate was not confirmed


@dataclass(frozen=True)
class WakewordEvent:
    kind: WakewordEventKind
    wakeword: str | None
    confidence: float | None = None
    end_offset_ms: int | None = None  # end of the wakeword relative to detector start, when known


@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float | None = None
    words: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class TranscriberUpdate:
    partial: str | None = None
    endpoint: bool = False  # engine detected end of utterance
    final_text: str | None = None


@dataclass(frozen=True)
class ProviderConfig:
    model_paths: dict[str, str] = field(default_factory=dict)
    sample_rate: int = 16000
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCheck:
    name: str
    ok: bool
    detail: str
    severity: str = "error"  # severity when not ok: "error" | "warning"


class WakewordDetector(ABC):
    @abstractmethod
    def accept(self, frame: bytes) -> list[WakewordEvent]: ...

    @abstractmethod
    def flush(self) -> list[WakewordEvent]:
        """Finalize pending audio; must resolve any outstanding candidate."""

    @abstractmethod
    def reset(self) -> None: ...


class Transcriber(ABC):
    @abstractmethod
    def accept(self, frame: bytes) -> TranscriberUpdate: ...

    @abstractmethod
    def finalize(self) -> Transcript: ...


class Engine(ABC):
    """Shared contract for a selectable speech engine.

    Engines are long-lived: `load()` models once, then spawn per-session workers
    (WakewordDetector / Transcriber). All engines receive 16 kHz mono int16 PCM frames.
    """

    name: ClassVar[str]

    @abstractmethod
    def load(self, config: ProviderConfig) -> None:
        """Load models. Must be idempotent for an unchanged config."""

    @abstractmethod
    def is_loaded(self) -> bool: ...

    def unload(self) -> None:  # noqa: B027 - optional hook
        pass

    @abstractmethod
    def check(self, config: ProviderConfig) -> list[ProviderCheck]:
        """Cheap readiness checks (e.g. model paths) that do not load models."""

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "loaded": self.is_loaded()}


class WakewordEngine(Engine):
    """Engine that creates per-session wakeword detectors (e.g. Vosk KWS, openWakeWord)."""

    @abstractmethod
    def create_wakeword_detector(self, wakewords: Sequence[str]) -> WakewordDetector: ...


class TranscriptionEngine(Engine):
    """Engine that creates per-session speech-to-text transcribers (e.g. Vosk, faster-whisper)."""

    @abstractmethod
    def create_transcriber(self, grammar: Sequence[str] | None) -> Transcriber:
        """grammar=None means open dictation; otherwise constrain recognition to these phrases."""


class ListenerProvider(WakewordEngine, TranscriptionEngine):
    """Legacy combined provider: a WakewordEngine and TranscriptionEngine in one object.

    Compatibility path for engines (like Vosk) that implement both roles — it makes every
    existing ListenerProvider selectable for either role without adapters.
    """

    name: ClassVar[str]


class SttRefiner(ABC):
    """Optional second-pass transcription (e.g. Whisper) over the captured command audio."""

    name: ClassVar[str]

    @abstractmethod
    def load(self, config: ProviderConfig) -> None: ...

    @abstractmethod
    def refine(self, pcm: bytes, draft: Transcript) -> Transcript: ...
