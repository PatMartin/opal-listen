"""Failed-utterance promotion (the review/tuning loop)."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from opal_listener.models.tables import FailedUtterance, Intent, IntentPhrase, utc_now
from opal_listener.service.catalog import get_or_404
from opal_listener.service.errors import InvalidRequestError
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.nlu.normalizer import normalize_text, split_wakeword
from opal_listener.service.runtime_config import RuntimeSnapshot


def delexicalize(text: str, slot_names: set[str], snapshot: RuntimeSnapshot) -> tuple[str, dict[str, Any]]:
    """Replace known slot values/aliases with {slot} placeholders (longest surface first)."""
    tokens = text.split()
    values: dict[str, Any] = {}
    for name in sorted(slot_names):
        surfaces = snapshot.engine.slot_surfaces(name)
        done = False
        for surface, canonical in surfaces:
            k = len(surface)
            for i in range(len(tokens) - k + 1):
                if tuple(tokens[i : i + k]) == surface:
                    tokens[i : i + k] = ["{" + name + "}"]
                    values[name] = canonical
                    done = True
                    break
            if done:
                break
    return " ".join(tokens), values


def promote(
    session: Session, service: ListenerService, fu_id: int, intent: Intent
) -> tuple[FailedUtterance, IntentPhrase]:
    fu = get_or_404(session, FailedUtterance, fu_id, "failed utterance")
    intent_name = intent.name
    snapshot = service.snapshot
    _, command = split_wakeword(normalize_text(fu.transcript), snapshot.wakewords)
    command = normalize_text(command, snapshot.filler_words)
    if not command:
        raise InvalidRequestError("failed utterance has no usable transcript")
    phrase_text, _ = delexicalize(command, snapshot.engine.slot_names_for_intent(intent_name), snapshot)
    phrase = next((p for p in intent.phrases if p.phrase == phrase_text), None)
    if phrase is None:
        phrase = IntentPhrase(intent_id=intent.id, phrase=phrase_text)  # type: ignore[arg-type]
        session.add(phrase)
    intent.updated_at = utc_now()
    fu.promoted_to_phrase = True
    fu.reviewed = True
    fu.expected_intent = intent_name
    session.add(fu)
    session.flush()
    return fu, phrase
