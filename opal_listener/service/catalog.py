"""CRUD for configuration entities. Callers run these inside ListenerService.config_change()."""

from __future__ import annotations

from typing import Any, TypeVar

from sqlmodel import Session, SQLModel, select

from opal_listener.models import schemas as s
from opal_listener.models.tables import (
    Intent,
    IntentPhrase,
    Profile,
    Slot,
    SlotAlias,
    SlotValue,
    Wakeword,
    utc_now,
)
from opal_listener.service.errors import ConflictError, InvalidRequestError, NotFoundError
from opal_listener.service.nlu.normalizer import normalize_text

T = TypeVar("T", bound=SQLModel)


def get_or_404(session: Session, model: type[T], obj_id: int, label: str) -> T:
    obj = session.get(model, obj_id)
    if obj is None:
        raise NotFoundError(f"{label} {obj_id} not found")
    return obj


def _ensure_unique(session: Session, model: Any, name: str, label: str, exclude_id: int | None = None) -> None:
    existing = session.exec(select(model).where(model.name == name)).first()
    if existing is not None and existing.id != exclude_id:
        raise ConflictError(f"{label} '{name}' already exists")


def _apply(obj: Any, changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        setattr(obj, key, value)
    if hasattr(obj, "updated_at"):
        obj.updated_at = utc_now()


def _clean(text: str, label: str) -> str:
    text = text.strip()
    if not text:
        raise InvalidRequestError(f"{label} must not be empty")
    return text


# --- wakewords -------------------------------------------------------------------------------


def _wakeword_name(name: str) -> str:
    normalized = normalize_text(name)
    if not normalized:
        raise InvalidRequestError("wakeword name must contain at least one word")
    return normalized


def list_wakewords(session: Session) -> list[Wakeword]:
    return list(session.exec(select(Wakeword).order_by(Wakeword.id)).all())


def create_wakeword(session: Session, data: s.WakewordCreate) -> Wakeword:
    name = _wakeword_name(data.name)
    _ensure_unique(session, Wakeword, name, "wakeword")
    obj = Wakeword(**{**data.model_dump(), "name": name})
    session.add(obj)
    session.flush()
    return obj


def update_wakeword(session: Session, obj_id: int, data: s.WakewordUpdate) -> Wakeword:
    obj = get_or_404(session, Wakeword, obj_id, "wakeword")
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in changes:
        changes["name"] = _wakeword_name(changes["name"])
        _ensure_unique(session, Wakeword, changes["name"], "wakeword", obj_id)
    _apply(obj, changes)
    session.add(obj)
    session.flush()
    return obj


def delete_wakeword(session: Session, obj_id: int) -> None:
    session.delete(get_or_404(session, Wakeword, obj_id, "wakeword"))
    session.flush()


# --- intents ---------------------------------------------------------------------------------


def list_intents(session: Session) -> list[Intent]:
    return list(session.exec(select(Intent).order_by(Intent.id)).all())


def get_intent_by_name(session: Session, name: str) -> Intent:
    obj = session.exec(select(Intent).where(Intent.name == name)).first()
    if obj is None:
        raise NotFoundError(f"intent '{name}' not found")
    return obj


def create_intent(session: Session, data: s.IntentCreate) -> Intent:
    _ensure_unique(session, Intent, data.name, "intent")
    obj = Intent(**data.model_dump(exclude={"phrases"}))
    obj.phrases = [IntentPhrase(phrase=_clean(p, "phrase")) for p in dict.fromkeys(data.phrases)]
    session.add(obj)
    session.flush()
    return obj


def update_intent(session: Session, obj_id: int, data: s.IntentUpdate) -> Intent:
    obj = get_or_404(session, Intent, obj_id, "intent")
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in changes:
        _ensure_unique(session, Intent, changes["name"], "intent", obj_id)
    _apply(obj, changes)
    session.add(obj)
    session.flush()
    return obj


def delete_intent(session: Session, obj_id: int) -> None:
    session.delete(get_or_404(session, Intent, obj_id, "intent"))
    session.flush()


def add_phrase(session: Session, intent_id: int, data: s.IntentPhraseCreate) -> IntentPhrase:
    intent = get_or_404(session, Intent, intent_id, "intent")
    phrase = _clean(data.phrase, "phrase")
    if any(p.phrase == phrase for p in intent.phrases):
        raise ConflictError(f"phrase '{phrase}' already exists for intent '{intent.name}'")
    obj = IntentPhrase(intent_id=intent_id, phrase=phrase)
    session.add(obj)
    intent.updated_at = utc_now()
    session.flush()
    return obj


def delete_phrase(session: Session, intent_id: int, phrase_id: int) -> None:
    obj = get_or_404(session, IntentPhrase, phrase_id, "phrase")
    if obj.intent_id != intent_id:
        raise NotFoundError(f"phrase {phrase_id} not found for intent {intent_id}")
    session.delete(obj)
    session.flush()


# --- slots -----------------------------------------------------------------------------------


def list_slots(session: Session) -> list[Slot]:
    return list(session.exec(select(Slot).order_by(Slot.id)).all())


def create_slot(session: Session, data: s.SlotCreate) -> Slot:
    _ensure_unique(session, Slot, data.name, "slot")
    _check_slot_type(data.slot_type)
    obj = Slot(**data.model_dump())
    session.add(obj)
    session.flush()
    return obj


def _check_slot_type(slot_type: str) -> None:
    if slot_type not in s.SLOT_TYPES:
        raise InvalidRequestError(f"slot_type must be one of {', '.join(s.SLOT_TYPES)}")


def update_slot(session: Session, obj_id: int, data: s.SlotUpdate) -> Slot:
    obj = get_or_404(session, Slot, obj_id, "slot")
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in changes:
        _ensure_unique(session, Slot, changes["name"], "slot", obj_id)
    if "slot_type" in changes:
        _check_slot_type(changes["slot_type"])
    _apply(obj, changes)
    session.add(obj)
    session.flush()
    return obj


def delete_slot(session: Session, obj_id: int) -> None:
    session.delete(get_or_404(session, Slot, obj_id, "slot"))
    session.flush()


def add_slot_value(session: Session, slot_id: int, data: s.SlotValueCreate) -> SlotValue:
    slot = get_or_404(session, Slot, slot_id, "slot")
    value = _clean(data.value, "value")
    if any(v.value == value for v in slot.values):
        raise ConflictError(f"value '{value}' already exists for slot '{slot.name}'")
    obj = SlotValue(slot_id=slot_id, value=value)
    session.add(obj)
    slot.updated_at = utc_now()
    session.flush()
    return obj


def get_slot_value(session: Session, slot_id: int, value_id: int) -> SlotValue:
    obj = get_or_404(session, SlotValue, value_id, "slot value")
    if obj.slot_id != slot_id:
        raise NotFoundError(f"slot value {value_id} not found for slot {slot_id}")
    return obj


def delete_slot_value(session: Session, slot_id: int, value_id: int) -> None:
    session.delete(get_slot_value(session, slot_id, value_id))
    session.flush()


def add_alias(session: Session, slot_id: int, value_id: int, data: s.SlotAliasCreate) -> SlotAlias:
    value = get_slot_value(session, slot_id, value_id)
    alias = _clean(data.alias, "alias")
    if any(a.alias == alias for a in value.aliases):
        raise ConflictError(f"alias '{alias}' already exists for value '{value.value}'")
    obj = SlotAlias(slot_value_id=value_id, alias=alias)
    session.add(obj)
    session.flush()
    return obj


def delete_alias(session: Session, slot_id: int, value_id: int, alias_id: int) -> None:
    get_slot_value(session, slot_id, value_id)
    obj = get_or_404(session, SlotAlias, alias_id, "alias")
    if obj.slot_value_id != value_id:
        raise NotFoundError(f"alias {alias_id} not found for value {value_id}")
    session.delete(obj)
    session.flush()


# --- profiles --------------------------------------------------------------------------------


def list_profiles(session: Session) -> list[Profile]:
    return list(session.exec(select(Profile).order_by(Profile.id)).all())


def _deactivate_others(session: Session, keep_id: int | None) -> None:
    for other in session.exec(select(Profile).where(Profile.is_active == True)).all():  # noqa: E712
        if other.id != keep_id:
            other.is_active = False
            other.updated_at = utc_now()
            session.add(other)


def create_profile(session: Session, data: s.ProfileCreate) -> Profile:
    _ensure_unique(session, Profile, data.name, "profile")
    obj = Profile(**data.model_dump())
    session.add(obj)
    session.flush()
    if obj.is_active:
        _deactivate_others(session, obj.id)
        session.flush()
    return obj


def update_profile(session: Session, obj_id: int, data: s.ProfileUpdate) -> Profile:
    obj = get_or_404(session, Profile, obj_id, "profile")
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    if "name" in changes:
        _ensure_unique(session, Profile, changes["name"], "profile", obj_id)
    _apply(obj, changes)
    session.add(obj)
    if changes.get("is_active"):
        _deactivate_others(session, obj_id)
    session.flush()
    return obj


def activate_profile(session: Session, obj_id: int) -> Profile:
    obj = get_or_404(session, Profile, obj_id, "profile")
    _deactivate_others(session, obj_id)
    obj.is_active = True
    obj.updated_at = utc_now()
    session.add(obj)
    session.flush()
    return obj


def delete_profile(session: Session, obj_id: int) -> None:
    session.delete(get_or_404(session, Profile, obj_id, "profile"))
    session.flush()
