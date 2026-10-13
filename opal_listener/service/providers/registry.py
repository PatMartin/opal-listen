"""Provider registry: built-ins plus third-party providers via the 'opal_listener.providers' entry point."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from opal_listener.service.providers.base import ListenerProvider, ProviderError, SttRefiner

log = logging.getLogger(__name__)


def _builtin_providers() -> dict[str, type[ListenerProvider]]:
    from opal_listener.service.providers.fake_provider import FakeListenerProvider
    from opal_listener.service.providers.vosk_provider import VoskListenerProvider

    return {VoskListenerProvider.name: VoskListenerProvider, FakeListenerProvider.name: FakeListenerProvider}


def available_providers() -> dict[str, type[ListenerProvider]]:
    providers = _builtin_providers()
    for ep in entry_points(group="opal_listener.providers"):
        if ep.name in providers:
            continue
        try:
            cls = ep.load()
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not break startup
            log.warning("cannot load provider plugin %s: %s", ep.name, exc)
            continue
        if isinstance(cls, type) and issubclass(cls, ListenerProvider):
            providers[ep.name] = cls
    return providers


def create_provider(name: str) -> ListenerProvider:
    providers = available_providers()
    if name not in providers:
        raise ProviderError(f"unknown listener provider '{name}' (available: {', '.join(sorted(providers))})")
    return providers[name]()


def available_refiners() -> list[str]:
    return ["none", "faster_whisper"]


def create_refiner(name: str) -> SttRefiner | None:
    if name in ("", "none"):
        return None
    if name == "faster_whisper":
        from opal_listener.service.providers.whisper_refiner import FasterWhisperRefiner

        return FasterWhisperRefiner()
    raise ProviderError(f"unknown stt refiner '{name}'")
