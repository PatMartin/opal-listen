"""OPAL configuration snapshots: build/apply/validate OpalConfigSnapshot envelopes + managed store.

The snapshot carries persistent desired configuration only: settings overrides plus the
declarative resource collections declared by x-opal-snapshot (profiles, personas, wakewords,
intents, slots). Resource identifiers are preserved verbatim across load.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sqlmodel import Session, delete, select

from opal_listener.models import schemas as s
from opal_listener.models.tables import (
    Intent,
    IntentPhrase,
    Persona,
    Profile,
    Setting,
    Slot,
    SlotAlias,
    SlotValue,
    Wakeword,
    utc_now,
)
from opal_listener.service.concurrency import compute_etag
from opal_listener.service.config_store import stored_settings
from opal_listener.service.errors import InvalidRequestError, NotFoundError
from opal_listener.service.nlu.normalizer import normalize_text
from opal_listener.service.settings_registry import (
    SETTINGS,
    SettingValueError,
    parse_setting,
    to_storage,
)

SNAPSHOT_FORMAT_VERSION = "1.0.0"
SNAPSHOT_MODULE = "opal-listen"
RESOURCE_SECTIONS = ("profiles", "personas", "wakewords", "intents", "slots")


def managed_snapshot_path(data_dir: Path) -> Path:
    return data_dir / "managed_config_snapshot.json"


def _issue(code: str, path: str, message: str) -> s.OpalValidationIssue:
    return s.OpalValidationIssue(code=code, path=path, message=message)


def _setting_overrides(session: Session) -> dict[str, Any]:
    stored = stored_settings(session)
    overrides: dict[str, Any] = {}
    for key, definition in SETTINGS.items():
        raw = stored.get(key)
        if raw is None:
            continue
        try:
            value = parse_setting(key, raw)
        except SettingValueError:
            continue
        if value != definition.default_value():
            overrides[key] = value
    return overrides


def build_snapshot(session: Session) -> s.OpalConfigSnapshot:
    intents = session.exec(select(Intent).order_by(Intent.id)).all()
    return s.OpalConfigSnapshot(
        format_version=SNAPSHOT_FORMAT_VERSION,
        module=SNAPSHOT_MODULE,
        created_at=utc_now(),
        settings=_setting_overrides(session),
        resources={
            "profiles": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "description": p.description,
                    "is_active": p.is_active,
                    "shared": dict(p.shared),
                    "modules": {
                        **p.modules,
                        "opal-listen": {
                            "wakeword_names": list(p.wakeword_names),
                            "confidence_thresholds": dict(p.confidence_thresholds),
                            "vad_settings": dict(p.vad_settings),
                            "enabled_intents": list(p.enabled_intents),
                            "enabled_slots": list(p.enabled_slots),
                            "model_paths": dict(p.model_paths),
                        },
                    },
                }
                for p in session.exec(select(Profile).order_by(Profile.id)).all()
            ],
            "personas": [
                {
                    "id": p.persona_id,
                    "persona_id": p.persona_id,
                    "name": p.name,
                    "description": p.description,
                    "voice_id": p.voice_id,
                    "enabled": p.enabled,
                }
                for p in session.exec(select(Persona).order_by(Persona.persona_id)).all()
            ],
            "wakewords": [
                {"id": str(w.id), "name": w.name, "enabled": w.enabled, "confidence_threshold": w.confidence_threshold}
                for w in session.exec(select(Wakeword).order_by(Wakeword.id)).all()
            ],
            "intents": [
                {
                    "id": str(i.id),
                    "name": i.name,
                    "description": i.description,
                    "enabled": i.enabled,
                    "confidence_threshold": i.confidence_threshold,
                    "phrases": [{"id": str(ph.id), "phrase": ph.phrase} for ph in i.phrases],
                }
                for i in intents
            ],
            "slots": [
                {
                    "id": str(sl.id),
                    "name": sl.name,
                    "slot_type": sl.slot_type,
                    "description": sl.description,
                    "values": [
                        {
                            "id": str(v.id),
                            "value": v.value,
                            "aliases": [{"id": str(a.id), "alias": a.alias} for a in v.aliases],
                        }
                        for v in sl.values
                    ],
                }
                for sl in session.exec(select(Slot).order_by(Slot.id)).all()
            ],
        },
    )


def config_revision(session: Session) -> str:
    """Strong live-configuration revision over the snapshot scope (If-Match for config load)."""
    payload = build_snapshot(session).model_dump(mode="json")
    payload.pop("created_at", None)
    return compute_etag(payload)


def _item_id(item: Any) -> int | None:
    if isinstance(item, dict):
        raw = item.get("id")
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
    return None


def apply_snapshot(session: Session, snapshot: s.OpalConfigSnapshot) -> dict[str, int]:
    """Replace all declarative configuration with the snapshot (caller owns the transaction)."""
    # The regression_tests table is intentionally untouched: v0.5.0 retired the section and
    # the table is inert archival data, never recreated or cleared by loads.
    resources = snapshot.resources
    for model in (
        SlotAlias,
        SlotValue,
        Slot,
        IntentPhrase,
        Intent,
        Wakeword,
        Profile,
        Persona,
    ):
        session.exec(delete(model))  # type: ignore[call-overload]
    session.flush()
    now = utc_now()
    for w in resources["wakewords"]:
        session.add(
            Wakeword(
                id=_item_id(w),
                name=normalize_text(w["name"]),
                enabled=w.get("enabled", True),
                confidence_threshold=w.get("confidence_threshold", 0.8),
                created_at=now,
                updated_at=now,
            )
        )
    for i in resources["intents"]:
        intent = Intent(
            id=_item_id(i),
            name=i["name"],
            description=i.get("description"),
            enabled=i.get("enabled", True),
            confidence_threshold=i.get("confidence_threshold", 0.7),
            created_at=now,
            updated_at=now,
        )
        intent.phrases = [
            IntentPhrase(id=_item_id(p), phrase=p["phrase"].strip(), created_at=now)
            for p in {ph["phrase"].strip(): ph for ph in i.get("phrases", [])}.values()
        ]
        session.add(intent)
    for sl in resources["slots"]:
        slot = Slot(
            id=_item_id(sl),
            name=sl["name"],
            slot_type=sl.get("slot_type", "list"),
            description=sl.get("description"),
            created_at=now,
            updated_at=now,
        )
        slot.values = [
            SlotValue(
                id=_item_id(v),
                value=v["value"],
                created_at=now,
                aliases=[
                    SlotAlias(id=_item_id(a), alias=a["alias"], created_at=now)
                    for a in {al["alias"]: al for al in v.get("aliases", [])}.values()
                ],
            )
            for v in sl.get("values", [])
        ]
        session.add(slot)
    for p in resources["profiles"]:
        # v3 envelope (shared + modules.opal-listen) with flat-field fallback for pre-v3 snapshots.
        comp = dict(p.get("modules", {}).get("opal-listen") or {})
        foreign = {k: v for k, v in (p.get("modules") or {}).items() if k != "opal-listen"}
        session.add(
            Profile(
                id=_item_id(p),
                name=p["name"],
                description=p.get("description"),
                is_active=p.get("is_active", False),
                wakeword_names=list(comp.get("wakeword_names", p.get("wakeword_names", []))),
                confidence_thresholds=dict(comp.get("confidence_thresholds", p.get("confidence_thresholds", {}))),
                vad_settings=dict(comp.get("vad_settings", p.get("vad_settings", {}))),
                enabled_intents=list(comp.get("enabled_intents", p.get("enabled_intents", []))),
                enabled_slots=list(comp.get("enabled_slots", p.get("enabled_slots", []))),
                model_paths=dict(comp.get("model_paths", p.get("model_paths", {}))),
                shared=dict(p.get("shared", {})),
                modules=foreign,
                created_at=now,
                updated_at=now,
            )
        )
    for p in resources["personas"]:
        session.add(
            Persona(
                persona_id=p["persona_id"],
                name=p["name"],
                description=p.get("description"),
                voice_id=p.get("voice_id"),
                enabled=p.get("enabled", True),
                created_at=now,
                updated_at=now,
            )
        )
    existing = {r.key: r for r in session.exec(select(Setting)).all()}
    for key, definition in SETTINGS.items():
        row = existing.get(key) or Setting(key=key, value="")
        row.value = (
            to_storage(snapshot.settings[key]) if key in snapshot.settings else to_storage(definition.default_value())
        )
        row.updated_at = now
        session.add(row)
    session.flush()
    return {section: len(resources[section]) for section in RESOURCE_SECTIONS} | {"settings": len(snapshot.settings)}


def validate_snapshot(raw: dict[str, Any]) -> tuple[list[s.OpalValidationIssue], list[s.OpalValidationIssue]]:
    """Structural issues for a candidate envelope; returns (errors, warnings)."""
    errors: list[s.OpalValidationIssue] = []
    warnings: list[s.OpalValidationIssue] = []
    allowed = {"format_version", "module", "created_at", "settings", "resources"}
    for key in raw:
        if key not in allowed:
            errors.append(_issue("unknown_field", key, f"unexpected snapshot field '{key}'"))
    version = raw.get("format_version")
    if version != SNAPSHOT_FORMAT_VERSION:
        errors.append(_issue("unsupported_version", "format_version", f"unsupported format_version {version!r}"))
    module = raw.get("module")
    if module != SNAPSHOT_MODULE:
        errors.append(_issue("wrong_module", "module", f"snapshot is for module {module!r}"))
    if not raw.get("created_at"):
        errors.append(_issue("missing_field", "created_at", "created_at is required"))
    settings = raw.get("settings")
    if not isinstance(settings, dict):
        errors.append(_issue("invalid_field", "settings", "settings must be an object"))
    else:
        for key, value in settings.items():
            if key not in SETTINGS:
                errors.append(_issue("unknown_setting", f"settings.{key}", f"unknown setting '{key}'"))
                continue
            try:
                parse_setting(key, value)
            except SettingValueError as exc:
                errors.append(_issue("invalid_setting", f"settings.{key}", str(exc)))
    resources = raw.get("resources")
    if not isinstance(resources, dict):
        errors.append(_issue("invalid_field", "resources", "resources must be an object"))
    else:
        for section, items in resources.items():
            if section not in RESOURCE_SECTIONS:
                errors.append(_issue("unknown_section", f"resources.{section}", f"unknown section '{section}'"))
            elif not isinstance(items, list):
                errors.append(_issue("invalid_field", f"resources.{section}", f"'{section}' must be an array"))
    return errors, warnings


def save_managed(data_dir: Path, snapshot: s.OpalConfigSnapshot) -> Path:
    """Atomically create or replace the single managed snapshot (previous kept on write failure)."""
    path = managed_snapshot_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(snapshot.model_dump_json(), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_managed(data_dir: Path) -> s.OpalConfigSnapshot:
    path = managed_snapshot_path(data_dir)
    if not path.exists():
        raise NotFoundError("no managed configuration snapshot exists")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidRequestError(f"managed snapshot is unreadable: {exc}") from exc
    errors, _ = validate_snapshot(raw)
    if errors:
        raise InvalidRequestError("managed snapshot is invalid: " + "; ".join(f"{i.path}: {i.message}" for i in errors))
    return s.OpalConfigSnapshot.model_validate(raw)
