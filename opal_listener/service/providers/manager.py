"""ProviderManager: engine selection, loading and caching, extracted from ListenerService.

Owns the engine factories, the per-name load cache (fingerprint-keyed), the role check that
rejects an engine selected for a role it cannot serve, and the refiner cache. It knows nothing
about capture, dispatch or snapshots beyond the fields it is handed, so it is unit-testable
without a running service.

Cache semantics (unchanged from the inline implementation): an unchanged config fingerprint
reuses the live instance; a changed one creates and loads a fresh instance under the lock, and
the retired instance is simply dropped — never unloaded or mutated — so in-flight pipelines
keep a usable reference. At most one loaded instance per engine name.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from opal_listener.service.providers.base import (
    Engine,
    ListenerProvider,
    ProviderConfig,
    ProviderError,
    SttRefiner,
    TranscriptionEngine,
    WakewordEngine,
)
from opal_listener.service.providers.registry import (
    create_refiner,
    create_transcription_engine,
    create_wakeword_engine,
)


def config_fingerprint(config: ProviderConfig) -> tuple[Any, ...]:
    """Everything load() depends on: sample rate, model paths and engine options."""
    return (
        config.sample_rate,
        tuple(sorted(config.model_paths.items())),
        tuple(sorted((k, repr(v)) for k, v in config.options.items())),
    )


class ProviderManager:
    def __init__(
        self,
        *,
        provider_factory: Callable[[str], ListenerProvider] | None = None,
        wakeword_factory: Callable[[str], WakewordEngine] | None = None,
        transcription_factory: Callable[[str], TranscriptionEngine] | None = None,
    ) -> None:
        # provider_factory is the legacy combined-provider hook: honoured for both roles when
        # role-specific factories aren't given (combined providers serve either role).
        self.wakeword_factory = wakeword_factory or provider_factory or create_wakeword_engine
        self.transcription_factory = transcription_factory or provider_factory or create_transcription_engine
        self._lock = threading.Lock()
        self.engines: dict[str, tuple[tuple[Any, ...], Engine]] = {}
        self.refiners: dict[str, tuple[tuple[Any, ...], SttRefiner]] = {}

    def engine_for(self, name: str, config: ProviderConfig, *, role: str) -> Engine:
        """The loaded engine for `name` under `config`.

        The cache is keyed by engine name and shared across roles: selecting the same engine for
        both roles loads it once, and the role check rejects incompatible reuse."""
        fingerprint = config_fingerprint(config)
        with self._lock:
            cached = self.engines.get(name)
            if cached is not None and cached[0] == fingerprint:
                engine = cached[1]
            else:
                factory = self.wakeword_factory if role == "wakeword" else self.transcription_factory
                engine = factory(name)
                engine.load(config)
                self.engines[name] = (fingerprint, engine)
            expected = WakewordEngine if role == "wakeword" else TranscriptionEngine
            if not isinstance(engine, expected):
                raise ProviderError(f"engine '{name}' cannot serve the {role} role")
            return engine

    def engines_for(self, snapshot: Any) -> tuple[WakewordEngine, TranscriptionEngine]:
        """The loaded wakeword and transcription engines for a snapshot; one shared instance
        when both resolve to the same engine name."""
        config = snapshot.provider_config
        wakeword = self.engine_for(snapshot.wakeword_engine_name, config, role="wakeword")
        transcription = self.engine_for(snapshot.transcription_engine_name, config, role="transcription")
        return wakeword, transcription

    def transient(self, name: str, *, role: str) -> Engine:
        """An unloaded, uncached instance for inspection (check()/status) paths."""
        factory = self.wakeword_factory if role == "wakeword" else self.transcription_factory
        return factory(name)

    def cached(self, name: str) -> Engine | None:
        """The cached engine for `name`, or None — for inspection without side effects."""
        with self._lock:
            cached = self.engines.get(name)
            return cached[1] if cached else None

    def refiner_for(self, snapshot: Any) -> SttRefiner | None:
        """The loaded SttRefiner for a snapshot (or None when refinement is disabled)."""
        name = snapshot.refiner_name
        if name in ("", "none"):
            return None
        fingerprint = config_fingerprint(snapshot.provider_config)
        with self._lock:
            cached = self.refiners.get(name)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
            refiner = create_refiner(name)
            if refiner is None:
                return None
            refiner.load(snapshot.provider_config)
            self.refiners[name] = (fingerprint, refiner)
            return refiner
