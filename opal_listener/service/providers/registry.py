"""Provider registry: built-ins plus third-party engines via entry points.

- `opal_listener.providers` registers combined ListenerProvider classes (both roles).
- `opal_listener.wakeword_engines` / `opal_listener.transcription_engines` register
  role-specific engines (e.g. a wakeword-only openWakeWord plugin).
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TypeVar

from opal_listener.service.providers.base import (
    Engine,
    ListenerProvider,
    ProviderError,
    SttRefiner,
    TranscriptionEngine,
    WakewordEngine,
)

log = logging.getLogger(__name__)

E = TypeVar("E", bound=Engine)


def _builtin_providers() -> dict[str, type[ListenerProvider]]:
    from opal_listener.service.providers.fake_provider import FakeListenerProvider
    from opal_listener.service.providers.vosk_provider import VoskListenerProvider

    return {VoskListenerProvider.name: VoskListenerProvider, FakeListenerProvider.name: FakeListenerProvider}


def _load_entry_points(group: str, expected: type[E], into: dict[str, type[E]]) -> None:
    for ep in entry_points(group=group):
        if ep.name in into:
            continue
        try:
            cls = ep.load()
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not break startup
            log.warning("cannot load provider plugin %s: %s", ep.name, exc)
            continue
        if isinstance(cls, type) and issubclass(cls, expected):
            into[ep.name] = cls


def available_providers() -> dict[str, type[ListenerProvider]]:
    providers: dict[str, type[ListenerProvider]] = dict(_builtin_providers())
    _load_entry_points("opal_listener.providers", ListenerProvider, providers)
    return providers


def available_wakeword_engines() -> dict[str, type[WakewordEngine]]:
    engines: dict[str, type[WakewordEngine]] = dict(_builtin_providers())
    _load_entry_points("opal_listener.providers", WakewordEngine, engines)
    _load_entry_points("opal_listener.wakeword_engines", WakewordEngine, engines)
    return engines


def available_transcription_engines() -> dict[str, type[TranscriptionEngine]]:
    engines: dict[str, type[TranscriptionEngine]] = dict(_builtin_providers())
    _load_entry_points("opal_listener.providers", TranscriptionEngine, engines)
    _load_entry_points("opal_listener.transcription_engines", TranscriptionEngine, engines)
    return engines


def _create_engine(name: str, available: dict[str, type[E]], role: str) -> E:
    if name not in available:
        raise ProviderError(f"unknown {role} '{name}' (available: {', '.join(sorted(available))})")
    return available[name]()


def create_provider(name: str) -> ListenerProvider:
    return _create_engine(name, available_providers(), "listener provider")


def create_wakeword_engine(name: str) -> WakewordEngine:
    return _create_engine(name, available_wakeword_engines(), "wakeword engine")


def create_transcription_engine(name: str) -> TranscriptionEngine:
    return _create_engine(name, available_transcription_engines(), "transcription engine")


def available_refiners() -> list[str]:
    return ["none", "faster_whisper"]


def create_refiner(name: str) -> SttRefiner | None:
    if name in ("", "none"):
        return None
    if name == "faster_whisper":
        from opal_listener.service.providers.whisper_refiner import FasterWhisperRefiner

        return FasterWhisperRefiner()
    raise ProviderError(f"unknown stt refiner '{name}'")
