"""Regression tests and failed-utterance promotion (the review/tuning loop)."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, col, select

from opal_listener.models import schemas as s
from opal_listener.models.tables import FailedUtterance, IntentPhrase, RegressionTest, utc_now
from opal_listener.service.catalog import get_intent_by_name, get_or_404
from opal_listener.service.errors import InvalidRequestError
from opal_listener.service.listener_service import ListenerService
from opal_listener.service.nlu.normalizer import normalize_text, split_wakeword
from opal_listener.service.runtime_config import RuntimeSnapshot


def _slots_equal(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return all(str(actual.get(k)) == str(v) for k, v in expected.items())


def run_regression(session: Session, service: ListenerService, enabled_only: bool) -> s.RegressionRunResult:
    query = select(RegressionTest).order_by(col(RegressionTest.id))
    if enabled_only:
        query = query.where(RegressionTest.enabled == True)  # noqa: E712
    tests = session.exec(query).all()
    results: list[s.RegressionCaseResult] = []
    now = utc_now()
    for test in tests:
        outcome = service.interpret_text(test.phrase)
        actual_intent = outcome.intent_name or s.NO_MATCH
        failure = None
        if actual_intent != test.expected_intent:
            failure = f"expected intent '{test.expected_intent}', got '{actual_intent}'"
        elif not _slots_equal(test.expected_slots, outcome.slot_values):
            failure = f"expected slots {test.expected_slots}, got {outcome.slot_values}"
        test.last_result = failure is None
        test.last_run_at = now
        session.add(test)
        results.append(
            s.RegressionCaseResult(
                test_id=test.id or 0,
                name=test.name,
                phrase=test.phrase,
                expected_intent=test.expected_intent,
                actual_intent=actual_intent,
                expected_slots=test.expected_slots,
                actual_slots=outcome.slot_values,
                intent_confidence=outcome.intent_confidence,
                passed=failure is None,
                failure_reason=failure,
            )
        )
    session.commit()
    passed = sum(1 for r in results if r.passed)
    return s.RegressionRunResult(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        pass_rate=round(passed / len(results), 4) if results else 1.0,
        results=results,
    )


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
    session: Session, service: ListenerService, fu_id: int, intent_name: str, create_regression: bool
) -> tuple[FailedUtterance, IntentPhrase, RegressionTest | None]:
    fu = get_or_404(session, FailedUtterance, fu_id, "failed utterance")
    intent = get_intent_by_name(session, intent_name)
    snapshot = service.snapshot
    _, command = split_wakeword(normalize_text(fu.transcript), snapshot.wakewords)
    command = normalize_text(command, snapshot.filler_words)
    if not command:
        raise InvalidRequestError("failed utterance has no usable transcript")
    phrase_text, slot_values = delexicalize(command, snapshot.engine.slot_names_for_intent(intent_name), snapshot)
    phrase = next((p for p in intent.phrases if p.phrase == phrase_text), None)
    if phrase is None:
        phrase = IntentPhrase(intent_id=intent.id, phrase=phrase_text)  # type: ignore[arg-type]
        session.add(phrase)
    intent.updated_at = utc_now()
    fu.promoted_to_phrase = True
    fu.reviewed = True
    fu.expected_intent = intent_name
    session.add(fu)
    regression = None
    if create_regression:
        regression = RegressionTest(
            name=f"promoted_failure_{fu.id}",
            phrase=command,
            expected_intent=intent_name,
            expected_slots=slot_values,
        )
        session.add(regression)
    session.flush()
    return fu, phrase, regression
