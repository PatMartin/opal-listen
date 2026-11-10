"""Recognition logs, performance results and failed utterances persistence/queries."""

from __future__ import annotations

import statistics
from typing import Any, TypeVar

from sqlalchemy import func
from sqlmodel import Session, SQLModel, col, delete, select

from opal_listener.models import schemas as s
from opal_listener.models.tables import FailedUtterance, PerformanceResult, RecognitionLog
from opal_listener.service.catalog import get_or_404

T = TypeVar("T", bound=SQLModel)


def save_result(session: Session, result: s.RecognitionResult, capture_failure: bool) -> int:
    log = RecognitionLog(
        session_id=result.session_id,
        source=result.source,
        profile_name=result.profile_name,
        wakeword=result.wakeword,
        transcript=result.transcript,
        normalized_transcript=result.normalized_transcript,
        intent_name=result.intent_name,
        slot_values=result.slot_values,
        wakeword_confidence=result.wakeword_confidence,
        stt_confidence=result.stt_confidence,
        intent_confidence=result.intent_confidence,
        matched=result.matched,
        no_match_reason=result.no_match_reason,
        total_latency_ms=result.total_latency_ms,
        wakeword_latency_ms=result.wakeword_latency_ms,
        stt_latency_ms=result.stt_latency_ms,
        intent_latency_ms=result.intent_latency_ms,
        audio_level=result.audio_level,
        created_at=result.created_at,
    )
    session.add(log)
    session.flush()
    assert log.id is not None
    session.add(
        PerformanceResult(
            recognition_log_id=log.id,
            wakeword_latency_ms=result.wakeword_latency_ms,
            stt_latency_ms=result.stt_latency_ms,
            intent_latency_ms=result.intent_latency_ms,
            total_latency_ms=result.total_latency_ms,
            wakeword_confidence=result.wakeword_confidence,
            intent_confidence=result.intent_confidence,
            matched=result.matched,
            audio_level=result.audio_level,
            created_at=result.created_at,
        )
    )
    if capture_failure and not result.matched and result.transcript:
        session.add(
            FailedUtterance(
                transcript=result.transcript[:1000],
                recognition_log_id=log.id,
                notes=f"auto-captured: {result.no_match_reason or 'no_match'}",
            )
        )
    return log.id


def prune_logs(session: Session, keep: int) -> None:
    cutoff = session.exec(
        select(RecognitionLog.id).order_by(col(RecognitionLog.id).desc()).offset(keep).limit(1)
    ).first()
    if cutoff is not None:
        session.exec(delete(RecognitionLog).where(col(RecognitionLog.id) <= cutoff))  # type: ignore[call-overload]


def list_logs(
    session: Session,
    limit: int,
    intent_name: str | None,
    matched: bool | None,
) -> list[RecognitionLog]:
    """Stable descending (created_at, id) bounded list; ``limit`` caps items."""
    query = select(RecognitionLog)
    if intent_name is not None:
        query = query.where(RecognitionLog.intent_name == intent_name)
    if matched is not None:
        query = query.where(RecognitionLog.matched == matched)
    query = query.order_by(col(RecognitionLog.created_at).desc(), col(RecognitionLog.id).desc()).limit(limit)
    return list(session.exec(query).all())


def count_logs(session: Session) -> dict[str, int]:
    total = session.exec(select(func.count()).select_from(RecognitionLog)).one()
    matched = session.exec(
        select(func.count()).select_from(RecognitionLog).where(RecognitionLog.matched == True)  # noqa: E712
    ).one()
    return {"total": total, "matched": matched, "no_match": total - matched}


# --- failed utterances -----------------------------------------------------------------------


def list_failed(
    session: Session, reviewed: bool | None, promoted: bool | None, limit: int
) -> list[FailedUtterance]:
    query = select(FailedUtterance)
    if reviewed is not None:
        query = query.where(FailedUtterance.reviewed == reviewed)
    if promoted is not None:
        query = query.where(FailedUtterance.promoted_to_phrase == promoted)
    query = query.order_by(col(FailedUtterance.id).desc()).limit(limit)
    return list(session.exec(query).all())


def create_failed(session: Session, data: s.FailedUtteranceCreate) -> FailedUtterance:
    if data.recognition_log_id is not None:
        get_or_404(session, RecognitionLog, data.recognition_log_id, "recognition log")
    obj = FailedUtterance(**data.model_dump())
    session.add(obj)
    session.flush()
    return obj


def update_failed(session: Session, obj_id: int, data: s.FailedUtteranceUpdate) -> FailedUtterance:
    obj = get_or_404(session, FailedUtterance, obj_id, "failed utterance")
    for key, value in data.model_dump(exclude_unset=True).items():
        if key in ("reviewed", "promoted_to_phrase") and value is None:
            continue
        setattr(obj, key, value)
    session.add(obj)
    session.flush()
    return obj


# --- performance -----------------------------------------------------------------------------


def last_performance(session: Session) -> PerformanceResult | None:
    return session.exec(select(PerformanceResult).order_by(col(PerformanceResult.id).desc()).limit(1)).first()


def performance_history(session: Session, limit: int) -> list[PerformanceResult]:
    query = select(PerformanceResult).order_by(col(PerformanceResult.id).desc()).limit(limit)
    return list(session.exec(query).all())


def _stats(values: list[Any]) -> s.LatencyStats:
    data = sorted(float(v) for v in values if v is not None)
    if not data:
        return s.LatencyStats(avg=None, p50=None, p95=None, max=None)
    p95 = data[min(len(data) - 1, max(0, round(0.95 * len(data)) - 1))]
    return s.LatencyStats(
        avg=round(statistics.fmean(data), 2), p50=round(statistics.median(data), 2), p95=p95, max=data[-1]
    )


def _mean(values: list[Any]) -> float | None:
    data = [float(v) for v in values if v is not None]
    return round(statistics.fmean(data), 4) if data else None


def performance_summary(session: Session, limit: int) -> s.PerformanceSummary:
    rows = performance_history(session, limit)
    matched = sum(1 for r in rows if r.matched)
    return s.PerformanceSummary(
        count=len(rows),
        matched_count=matched,
        no_match_count=len(rows) - matched,
        match_rate=round(matched / len(rows), 4) if rows else None,
        avg_wakeword_confidence=_mean([r.wakeword_confidence for r in rows]),
        avg_intent_confidence=_mean([r.intent_confidence for r in rows]),
        wakeword_latency_ms=_stats([r.wakeword_latency_ms for r in rows]),
        stt_latency_ms=_stats([r.stt_latency_ms for r in rows]),
        intent_latency_ms=_stats([r.intent_latency_ms for r in rows]),
        total_latency_ms=_stats([r.total_latency_ms for r in rows]),
    )
