"""Conversion between the SQLite configuration tables and a ConfigBundle (YAML/JSON form)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError
from sqlmodel import Session, delete, select

from opal_listener.models.config_schemas import (
    CONFIG_FILES,
    ConfigBundle,
    IntentSpec,
    ProfileSpec,
    SlotSpec,
    SlotValueSpec,
    WakewordSpec,
)
from opal_listener.models.tables import (
    Intent,
    IntentPhrase,
    Profile,
    Setting,
    Slot,
    SlotAlias,
    SlotValue,
    Wakeword,
    utc_now,
)
from opal_listener.service.nlu.normalizer import normalize_text
from opal_listener.service.settings_registry import SETTINGS, effective_settings, keys_in_group, to_storage


class ConfigFileError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def stored_settings(session: Session) -> dict[str, str]:
    return {s.key: s.value for s in session.exec(select(Setting)).all()}


def export_bundle(session: Session) -> ConfigBundle:
    settings = effective_settings(stored_settings(session))
    return ConfigBundle(
        wakewords=[
            WakewordSpec(name=w.name, enabled=w.enabled, confidence_threshold=w.confidence_threshold)
            for w in session.exec(select(Wakeword).order_by(Wakeword.id)).all()
        ],
        stt={k: settings[k] for k in keys_in_group("stt")},
        intents=[
            IntentSpec(
                name=i.name,
                description=i.description,
                enabled=i.enabled,
                confidence_threshold=i.confidence_threshold,
                phrases=[p.phrase for p in i.phrases],
            )
            for i in session.exec(select(Intent).order_by(Intent.id)).all()
        ],
        slots=[
            SlotSpec(
                name=s.name,
                slot_type=s.slot_type,
                description=s.description,
                values=[SlotValueSpec(value=v.value, aliases=[a.alias for a in v.aliases]) for v in s.values],
            )
            for s in session.exec(select(Slot).order_by(Slot.id)).all()
        ],
        profiles=[
            ProfileSpec(
                name=p.name,
                description=p.description,
                is_active=p.is_active,
                wakeword_names=list(p.wakeword_names),
                confidence_thresholds=dict(p.confidence_thresholds),
                vad_settings=dict(p.vad_settings),
                enabled_intents=list(p.enabled_intents),
                enabled_slots=list(p.enabled_slots),
                model_paths=dict(p.model_paths),
            )
            for p in session.exec(select(Profile).order_by(Profile.id)).all()
        ],
        settings={k: settings[k] for k in keys_in_group("settings")},
    )


def replace_config(session: Session, bundle: ConfigBundle) -> dict[str, int]:
    """Replace all configuration tables with the bundle contents (caller owns the transaction)."""
    for model in (SlotAlias, SlotValue, Slot, IntentPhrase, Intent, Wakeword, Profile):
        session.exec(delete(model))  # type: ignore[call-overload]
    session.flush()
    now = utc_now()
    for w in bundle.wakewords:
        session.add(
            Wakeword(
                name=normalize_text(w.name),
                enabled=w.enabled,
                confidence_threshold=w.confidence_threshold,
                created_at=now,
                updated_at=now,
            )
        )
    for i in bundle.intents:
        intent = Intent(
            name=i.name,
            description=i.description,
            enabled=i.enabled,
            confidence_threshold=i.confidence_threshold,
            created_at=now,
            updated_at=now,
        )
        intent.phrases = [IntentPhrase(phrase=p.strip(), created_at=now) for p in dict.fromkeys(i.phrases)]
        session.add(intent)
    for s in bundle.slots:
        slot = Slot(name=s.name, slot_type=s.slot_type, description=s.description, created_at=now, updated_at=now)
        slot.values = [
            SlotValue(
                value=v.value,
                created_at=now,
                aliases=[SlotAlias(alias=a, created_at=now) for a in dict.fromkeys(v.aliases)],
            )
            for v in s.values
        ]
        session.add(slot)
    for p in bundle.profiles:
        session.add(Profile(**p.model_dump(), created_at=now, updated_at=now))
    existing = {s.key: s for s in session.exec(select(Setting)).all()}
    for key, value in {**bundle.stt, **bundle.settings}.items():
        if key not in SETTINGS:
            continue
        row = existing.get(key) or Setting(key=key, value="")
        row.value = to_storage(value)
        row.updated_at = now
        session.add(row)
    session.flush()
    return {
        "wakewords": len(bundle.wakewords),
        "intents": len(bundle.intents),
        "intent_phrases": sum(len(i.phrases) for i in bundle.intents),
        "slots": len(bundle.slots),
        "slot_values": sum(len(s.values) for s in bundle.slots),
        "profiles": len(bundle.profiles),
        "settings": len(bundle.stt) + len(bundle.settings),
    }


def load_config_dir(config_dir: Path) -> ConfigBundle:
    """Read and schema-validate all YAML files; missing files contribute empty sections."""
    errors: list[str] = []
    data: dict[str, object] = {}
    for filename, (schema, key) in CONFIG_FILES.items():
        path = config_dir / filename
        if not path.exists():
            continue
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            errors.append(f"{filename}: invalid YAML: {exc}")
            continue
        try:
            parsed = schema.model_validate(raw)
        except ValidationError as exc:
            errors.extend(f"{filename}: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
            continue
        data[key] = getattr(parsed, key)
    if errors:
        raise ConfigFileError(errors)
    return ConfigBundle.model_validate(data)


def bundle_to_files(bundle: ConfigBundle) -> dict[str, str]:
    """Render a bundle as the six YAML documents."""
    dumped = bundle.model_dump(mode="json")
    return {
        filename: yaml.safe_dump({"version": bundle.version, key: dumped[key]}, sort_keys=False, allow_unicode=True)
        for filename, (_schema, key) in CONFIG_FILES.items()
    }
