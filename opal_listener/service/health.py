"""Startup/doctor health checks."""

from __future__ import annotations

import shutil

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from opal_listener.data.db import alembic_config
from opal_listener.models import schemas as s
from opal_listener.models.tables import utc_now
from opal_listener.service.audio.capture import AudioCaptureError, list_input_devices, resolve_device
from opal_listener.service.config_store import export_bundle
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.nlu.normalizer import normalize_text
from opal_listener.service.providers.base import ProviderError
from opal_listener.service.runtime_config import compile_bundle

MIN_FREE_BYTES = 100 * 1024 * 1024


def _check(name: str, status: s.CheckStatus, detail: str) -> s.HealthCheckResult:
    return s.HealthCheckResult(name=name, status=status, detail=detail)


def run_health_checks(service: ListenerService) -> s.HealthReport:
    checks: list[s.HealthCheckResult] = []

    try:
        service.db.ping()
        checks.append(_check("sqlite", "ok", str(service.db.path)))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check("sqlite", "error", f"database unavailable: {exc}"))
        return s.HealthReport(status="error", checks=checks, checked_at=utc_now())

    with service.db.engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    head = ScriptDirectory.from_config(alembic_config(service.db.url)).get_current_head()
    checks.append(_check("migrations", "ok" if current == head else "error", f"revision {current} (head {head})"))

    with service.db.session() as session:
        report, snapshot = compile_bundle(export_bundle(session))
    slot_errors = [e for e in report.errors if e.startswith("slot")]
    intent_errors = [e for e in report.errors if e not in slot_errors]
    checks.append(
        _check("slots", "error", "; ".join(slot_errors)) if slot_errors else _check("slots", "ok", "slots compile")
    )
    if intent_errors:
        checks.append(_check("intents", "error", "; ".join(intent_errors)))
    elif snapshot is not None:
        checks.append(
            _check(
                "intents",
                "ok" if snapshot.engine.template_count else "warning",
                f"{len(snapshot.engine.intent_names)} intents, {snapshot.engine.template_count} phrase variants",
            )
        )
    if report.warnings:
        checks.append(_check("config_warnings", "warning", "; ".join(report.warnings)))

    provider, snap = service.provider_status()
    if provider is None or snap is None:
        checks.append(_check("provider", "error", "listener provider unavailable"))
    else:
        provider_checks = provider.check(snap.provider_config)
        for c in provider_checks:
            status: s.CheckStatus = "ok" if c.ok else ("error" if c.severity == "error" else "warning")
            checks.append(_check(c.name, status, c.detail))
        if all(c.ok for c in provider_checks):
            try:
                service.provider_for(snap)
                checks.append(_check("provider", "ok", f"{snap.provider_name} loaded"))
            except ProviderError as exc:
                checks.append(_check("provider", "error", str(exc)))
        missing_words = getattr(provider, "missing_words", None)
        if provider.is_loaded() and callable(missing_words):
            wake_tokens = [t for w in snap.wakewords for t in normalize_text(w).split()]
            grammar_words = [w for phrase in snap.grammar or () for w in phrase.split()]
            distractors = list(snap.provider_config.options.get("wakeword_distractors", []))
            missing = missing_words([*wake_tokens, *grammar_words, *distractors])
            checks.append(
                _check("vocabulary", "warning", f"words not in model vocabulary: {', '.join(missing)}")
                if missing
                else _check("vocabulary", "ok", "all wakeword/phrase words are in the model vocabulary")
            )

    devices = list_input_devices()
    auto_start = bool(snap.settings.get("auto_start_listener")) if snap else False
    missing_status: s.CheckStatus = "error" if auto_start else "warning"
    if not devices:
        checks.append(_check("microphone", missing_status, "no audio input devices found"))
    else:
        try:
            spec = str(snap.settings["audio_device"]) if snap else ""
            index = resolve_device(spec)
            name = next(
                (d["name"] for d in devices if d["index"] == index or (index is None and d["is_default"])),
                devices[0]["name"],
            )
            checks.append(_check("microphone", "ok", f"{len(devices)} input device(s); using '{name}'"))
        except AudioCaptureError as exc:
            checks.append(_check("microphone", missing_status, str(exc)))

    runtime = service.status()
    if runtime.running and runtime.last_error:
        checks.append(_check("listener", "warning", f"{runtime.state}: {runtime.last_error}"))
    else:
        checks.append(_check("listener", "ok", runtime.state))

    free = shutil.disk_usage(service.app_settings.data_dir).free
    checks.append(_check("disk", "ok" if free >= MIN_FREE_BYTES else "warning", f"{free // (1024 * 1024)} MiB free"))

    worst: s.CheckStatus = "ok"
    if any(c.status == "error" for c in checks):
        worst = "error"
    elif any(c.status == "warning" for c in checks):
        worst = "warning"
    return s.HealthReport(status=worst, checks=checks, checked_at=utc_now())
