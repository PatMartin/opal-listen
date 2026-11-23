"""ProviderManager + _DedupWindow: extracted internals, tested without a ListenerService."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest

from opal_listener.service.listener_service import _DedupWindow
from opal_listener.service.providers.base import ProviderConfig, ProviderError, WakewordEngine
from opal_listener.service.providers.fake_provider import FakeListenerProvider
from opal_listener.service.providers.manager import ProviderManager, config_fingerprint


@dataclass
class _Snap:
    provider_config: ProviderConfig = field(default_factory=ProviderConfig)
    wakeword_engine_name: str = "fake"
    transcription_engine_name: str = "fake"
    refiner_name: str = "none"


def test_engine_for_reuses_and_reloads_on_fingerprint_change():
    mgr = ProviderManager()
    config = ProviderConfig()
    first = mgr.engine_for("fake", config, role="wakeword")
    assert mgr.engine_for("fake", config, role="wakeword") is first
    changed = ProviderConfig(sample_rate=48000)
    second = mgr.engine_for("fake", changed, role="wakeword")
    assert second is not first and len(mgr.engines) == 1
    assert first.is_loaded()  # retired instance stays usable for in-flight pipelines


def test_shared_and_split_role_instances():
    mgr = ProviderManager()
    wake, trans = mgr.engines_for(_Snap())
    assert wake is trans  # same name: one loaded instance serves both roles


def test_wrong_role_rejected():
    class WakeOnly(WakewordEngine):
        name = "wake_only"

        def load(self, config):
            pass

        def is_loaded(self):
            return True

        def check(self, config):
            return []

        def create_wakeword_detector(self, wakewords):
            raise NotImplementedError

    mgr = ProviderManager(
        wakeword_factory=lambda name: {"wake_only": WakeOnly}[name](),
        transcription_factory=lambda name: {"wake_only": WakeOnly}[name](),
    )
    with pytest.raises(ProviderError, match="cannot serve the transcription role"):
        mgr.engine_for("wake_only", ProviderConfig(), role="transcription")


def test_transient_and_cached_inspection():
    mgr = ProviderManager()
    assert mgr.cached("fake") is None
    transient = mgr.transient("fake", role="wakeword")
    assert isinstance(transient, FakeListenerProvider) and not transient.is_loaded()
    assert mgr.cached("fake") is None  # inspection never loads or caches
    loaded = mgr.engine_for("fake", ProviderConfig(), role="wakeword")
    assert mgr.cached("fake") is loaded and loaded is not transient


def test_refiner_cache_and_none():
    mgr = ProviderManager()
    assert mgr.refiner_for(_Snap(refiner_name="none")) is None
    with pytest.raises(ProviderError, match="unknown stt refiner"):
        mgr.refiner_for(_Snap(refiner_name="bogus"))


def test_config_fingerprint_covers_options_and_models():
    base = config_fingerprint(ProviderConfig())
    assert config_fingerprint(ProviderConfig()) == base
    assert config_fingerprint(ProviderConfig(sample_rate=8000)) != base
    assert config_fingerprint(ProviderConfig(model_paths={"m": "/x"})) != base
    assert config_fingerprint(ProviderConfig(options={"k": 1})) != base


def test_dedup_window_expiry_and_reset():
    dedup = _DedupWindow()
    now = time.monotonic()
    assert dedup.seen_recently(("a",), 1.0, now) is False
    assert dedup.seen_recently(("a",), 1.0, now + 0.5) is True  # inside the window
    assert dedup.seen_recently(("a",), 1.0, now + 2.0) is False  # expired entry evicted
    dedup.seen_recently(("b",), 60.0, now)
    dedup.reset()
    assert dedup.seen_recently(("b",), 60.0, now) is False
