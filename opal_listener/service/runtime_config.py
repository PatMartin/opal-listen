"""Validation and compilation of a full configuration bundle into an immutable runtime snapshot.

The same code validates YAML files, import bundles and the live database (exported as a bundle), so
"config validate" and runtime behaviour can never disagree.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from opal_listener.models.config_schemas import ConfigBundle, ProfileSpec
from opal_listener.service.audio.vad import VadSettings
from opal_listener.service.nlu.engine import (
    NAME_PATTERN,
    CompileReport,
    EngineOptions,
    IntentDef,
    IntentEngine,
    SlotDef,
)
from opal_listener.service.nlu.normalizer import normalize_text, parse_csv
from opal_listener.service.nlu.templates import TemplateError, slot_names
from opal_listener.service.providers.base import ProviderConfig
from opal_listener.service.settings_registry import (
    SETTINGS,
    Scalar,
    SettingValueError,
    effective_settings,
    parse_setting,
    to_storage,
)


class ProfileThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wakeword: float | None = Field(default=None, ge=0.0, le=1.0)
    intent: float | None = Field(default=None, ge=0.0, le=1.0)
    stt: float | None = Field(default=None, ge=0.0, le=1.0)


class ProfileModelPaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vosk_model: str | None = None
    whisper_model: str | None = None


class ConfigInvalidError(ValueError):
    def __init__(self, report: CompileReport) -> None:
        super().__init__("; ".join(report.errors) or "invalid configuration")
        self.report = report


@dataclass(frozen=True)
class RuntimeSnapshot:
    version: int
    profile_name: str | None
    engine: IntentEngine
    wakewords: tuple[str, ...]
    wakeword_thresholds: dict[str, float]
    stt_threshold: float
    vad: VadSettings
    settings: dict[str, Scalar]
    provider_config: ProviderConfig
    grammar: tuple[str, ...] | None
    filler_words: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    provider_name: str = "vosk"
    refiner_name: str = "none"
    extra: dict[str, Any] = field(default_factory=dict)


def _pydantic_errors(prefix: str, exc: ValidationError) -> list[str]:
    return [f"{prefix}.{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]


def _duplicates(names: list[str]) -> list[str]:
    return sorted(n for n, c in Counter(names).items() if c > 1)


def validate_profile(profile: ProfileSpec, report: CompileReport, known: dict[str, set[str]]) -> None:
    where = f"profile '{profile.name}'"
    if not NAME_PATTERN.match(profile.name):
        report.errors.append(f"{where}: name must be snake_case")
    for model, value, label in (
        (ProfileThresholds, profile.confidence_thresholds, "confidence_thresholds"),
        (VadSettings, profile.vad_settings, "vad_settings"),
        (ProfileModelPaths, profile.model_paths, "model_paths"),
    ):
        try:
            model.model_validate(value)
        except ValidationError as exc:
            report.errors.extend(_pydantic_errors(f"{where} {label}", exc))
    for label, names, pool in (
        ("wakeword_names", profile.wakeword_names, known["wakewords"]),
        ("enabled_intents", profile.enabled_intents, known["intents"]),
        ("enabled_slots", profile.enabled_slots, known["slots"]),
    ):
        unknown = sorted(set(names) - pool)
        if unknown:
            report.warnings.append(f"{where} {label}: unknown names ignored: {', '.join(unknown)}")


def _validate_settings(bundle: ConfigBundle, report: CompileReport) -> dict[str, str]:
    stored: dict[str, str] = {}
    for group, values in (("stt", bundle.stt), ("settings", bundle.settings)):
        for key, raw in values.items():
            definition = SETTINGS.get(key)
            if definition is not None and definition.group != group:
                report.errors.append(f"{group}.{key}: belongs in {definition.group}.yaml")
                continue
            try:
                stored[key] = to_storage(parse_setting(key, raw))
            except SettingValueError as exc:
                report.errors.append(f"{group}.{key}: {exc}")
    return stored


def compile_bundle(bundle: ConfigBundle, version: int = 0) -> tuple[CompileReport, RuntimeSnapshot | None]:
    """Validate everything and build the runtime snapshot for the active profile."""
    report = CompileReport()

    for label, names in (
        ("wakewords", [normalize_text(w.name) for w in bundle.wakewords]),
        ("intents", [i.name for i in bundle.intents]),
        ("slots", [s.name for s in bundle.slots]),
        ("profiles", [p.name for p in bundle.profiles]),
    ):
        for dup in _duplicates(names):
            report.errors.append(f"{label}: duplicate name '{dup}'")
    for wakeword in bundle.wakewords:
        if not normalize_text(wakeword.name):
            report.errors.append(f"wakeword '{wakeword.name}': empty after normalization")
    for slot in bundle.slots:
        for dup in _duplicates([v.value for v in slot.values]):
            report.errors.append(f"slot '{slot.name}': duplicate value '{dup}'")

    stored_settings = _validate_settings(bundle, report)
    settings = effective_settings(stored_settings)

    known = {
        "wakewords": {w.name for w in bundle.wakewords},
        "intents": {i.name for i in bundle.intents},
        "slots": {s.name for s in bundle.slots},
    }
    for profile in bundle.profiles:
        validate_profile(profile, report, known)
    active = [p for p in bundle.profiles if p.is_active]
    if len(active) > 1:
        report.errors.append(f"profiles: more than one active profile ({', '.join(p.name for p in active)})")
    profile = active[0] if len(active) == 1 else None

    slot_defs = [SlotDef(s.name, s.slot_type, [(v.value, tuple(v.aliases)) for v in s.values]) for s in bundle.slots]
    intent_defs = [IntentDef(i.name, tuple(i.phrases), i.confidence_threshold, i.enabled) for i in bundle.intents]
    options = EngineOptions(
        slot_fuzzy_min_score=float(settings["slot_fuzzy_min_score"]),
        token_fuzzy_min_score=float(settings["token_fuzzy_min_score"]),
    )
    # Full compile catches errors in intents/slots even if the active profile disables them.
    _, full_report = IntentEngine.compile(intent_defs, slot_defs, options)
    report.errors.extend(full_report.errors)
    report.warnings.extend(full_report.warnings)
    if report.errors:
        return report, None

    thresholds = ProfileThresholds.model_validate(profile.confidence_thresholds if profile else {})
    vad = VadSettings.model_validate(profile.vad_settings if profile else {})
    model_paths = ProfileModelPaths.model_validate(profile.model_paths if profile else {})

    runtime_intents, runtime_slots = intent_defs, slot_defs
    if profile and profile.enabled_slots:
        enabled_slots = set(profile.enabled_slots)
        runtime_slots = [s for s in slot_defs if s.name in enabled_slots]
        filtered: list[IntentDef] = []
        for intent in runtime_intents:
            phrases = []
            for phrase in intent.phrases:
                try:
                    needed = slot_names(phrase)
                except TemplateError:
                    continue
                if needed <= enabled_slots:
                    phrases.append(phrase)
                else:
                    report.warnings.append(f"profile '{profile.name}': phrase '{phrase}' disabled (uses disabled slot)")
            filtered.append(IntentDef(intent.name, tuple(phrases), intent.confidence_threshold, intent.enabled))
        runtime_intents = filtered
    if profile and profile.enabled_intents:
        enabled_intents = set(profile.enabled_intents)
        runtime_intents = [i for i in runtime_intents if i.name in enabled_intents]

    runtime_options = EngineOptions(
        intent_threshold_floor=thresholds.intent or 0.0,
        slot_fuzzy_min_score=options.slot_fuzzy_min_score,
        token_fuzzy_min_score=options.token_fuzzy_min_score,
    )
    engine, runtime_report = IntentEngine.compile(runtime_intents, runtime_slots, runtime_options)
    report.errors.extend(e for e in runtime_report.errors if e not in report.errors)
    if report.errors:
        return report, None

    wakewords = [w for w in bundle.wakewords if w.enabled]
    if profile and profile.wakeword_names:
        wanted = set(profile.wakeword_names)
        wakewords = [w for w in wakewords if w.name in wanted]
    if not wakewords:
        report.warnings.append("no enabled wakewords: live listening cannot trigger")
    if engine.template_count == 0:
        report.warnings.append("no enabled intent phrases: every utterance will be no_match")

    wake_floor = thresholds.wakeword or 0.0
    wakeword_thresholds = {w.name: max(w.confidence_threshold, wake_floor) for w in wakewords}

    grammar: tuple[str, ...] | None = None
    if settings["stt_mode"] == "grammar":
        if engine.requires_open_vocabulary:
            report.warnings.append("text slots require open dictation; stt_mode 'grammar' falls back to open")
        else:
            wake_phrases = {normalize_text(w.name) for w in wakewords}
            grammar = tuple(sorted(set(engine.grammar_phrases()) | wake_phrases))

    provider_config = ProviderConfig(
        model_paths={
            "vosk_model": model_paths.vosk_model or str(settings["vosk_model_path"]),
            "whisper_model": model_paths.whisper_model or str(settings["whisper_model"]),
        },
        options={
            "wakeword_trigger": settings["wakeword_trigger"],
            "wakeword_distractors": parse_csv(str(settings["wakeword_distractors"])),
            "whisper_compute_type": settings["whisper_compute_type"],
            "grammar": list(grammar or ()),
        },
    )
    snapshot = RuntimeSnapshot(
        version=version,
        profile_name=profile.name if profile else None,
        engine=engine,
        wakewords=tuple(w.name for w in wakewords),
        wakeword_thresholds=wakeword_thresholds,
        stt_threshold=thresholds.stt or 0.0,
        vad=vad,
        settings=settings,
        provider_config=provider_config,
        grammar=grammar,
        filler_words=tuple(parse_csv(str(settings["filler_words"]))),
        warnings=tuple(report.warnings),
        provider_name=str(settings["listener_provider"]),
        refiner_name=str(settings["stt_refiner"]),
    )
    return report, snapshot
