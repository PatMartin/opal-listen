"""Transcript retention: TTL scrub, unreviewed-failure expiry, bounded reaction journal."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlmodel import select

from opal_listener.models.tables import FailedUtterance, PerformanceResult, RecognitionLog, utc_now
from opal_listener.service import recognition_store
from opal_listener.service.reactions import ReactionAction, run_action

from .conftest import patch_settings


def _log_row(session: Any, days_old: float, transcript: str | None = "turn it on") -> RecognitionLog:
    row = RecognitionLog(
        session_id="s",
        source="mic",
        transcript=transcript,
        normalized_transcript=transcript,
        intent_name="light_turn_on",
        slot_values={},
        matched=True,
        created_at=utc_now() - timedelta(days=days_old),
    )
    session.add(row)
    session.flush()
    return row


def _failed(session: Any, days_old: float, reviewed: bool = False) -> FailedUtterance:
    row = FailedUtterance(
        transcript="miss",
        reviewed=reviewed,

        created_at=utc_now() - timedelta(days=days_old),
    )
    session.add(row)
    session.flush()
    return row


def test_transcript_ttl_scrubs_old_logs_and_keeps_rows(client: Any, service: Any) -> None:
    with service.db.session() as session:
        old = _log_row(session, days_old=40)
        perf = PerformanceResult(recognition_log_id=old.id, matched=True, created_at=old.created_at)
        session.add(perf)
        new = _log_row(session, days_old=1)
        session.commit()
        assert recognition_store.prune_transcripts(session, 30) == 1
        session.commit()
        session.refresh(old)
        session.refresh(new)
        assert old.transcript is None and old.normalized_transcript is None
        assert old.intent_name == "light_turn_on"  # structured fields survive the scrub
        assert new.transcript == "turn it on"
        kept_perf = session.exec(select(PerformanceResult).where(PerformanceResult.recognition_log_id == old.id)).all()
        assert len(kept_perf) == 1  # aggregate tuning metrics preserved
        assert recognition_store.prune_transcripts(session, 30) == 0  # repeated cycle is a no-op


def test_transcript_ttl_zero_keeps_everything(client: Any, service: Any) -> None:
    with service.db.session() as session:
        _log_row(session, days_old=4000)
        session.commit()
        assert recognition_store.prune_transcripts(session, 0) == 0
        assert session.exec(select(RecognitionLog)).one().transcript == "turn it on"


def test_unreviewed_failures_expire_but_curated_ones_survive(client: Any, service: Any) -> None:
    with service.db.session() as session:
        stale = _failed(session, days_old=100)
        fresh = _failed(session, days_old=1)
        reviewed = _failed(session, days_old=100, reviewed=True)
        session.commit()
        assert recognition_store.prune_failed_utterances(session, 90) == 1
        session.commit()
        remaining = {f.id for f in session.exec(select(FailedUtterance)).all()}
        assert stale.id not in remaining
        assert {fresh.id, reviewed.id} <= remaining  # curated examples preserved
        assert recognition_store.prune_failed_utterances(session, 90) == 0  # idempotent second cycle


def test_retention_settings_apply_at_prune_cycle(service: Any, client: Any) -> None:
    """The policy in effect at prune time governs existing rows: lowering the TTL scrubs
    transcripts that were written while retention was unlimited."""
    with service.db.session() as session:
        old = _log_row(session, days_old=40)
        session.commit()
        old_id = old.id
    assert patch_settings(client, transcript_retention_days="0").status_code == 200
    service._results_since_prune = 99  # drive the periodic cycle with the next result
    service.test_text("computer, what time is it")
    with service.db.session() as session:
        assert session.get(RecognitionLog, old_id).transcript == "turn it on"  # TTL 0: untouched
    assert patch_settings(client, transcript_retention_days="30").status_code == 200
    service._results_since_prune = 99
    service.test_text("computer, what time is it")
    with service.db.session() as session:
        row = session.get(RecognitionLog, old_id)
        assert row is not None and row.transcript is None  # retroactive scrub at the next cycle


def test_reaction_journal_is_bounded(tmp_path: Path) -> None:
    log_path = tmp_path / "reaction_log.jsonl"
    action = ReactionAction(argv=[sys.executable, "-c", "pass"])
    payload = {"intent_name": "x", "transcript": "some spoken words " * 10}
    for _ in range(60):
        run_action(action, payload, "r", log_path, origin="test", max_bytes=2048)
    assert log_path.stat().st_size <= 2048 + 1024  # stays near the cap, never grows without bound
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert 1 < len(lines) < 60  # oldest entries dropped
    assert all(json.loads(line)["origin"] == "test" for line in lines)  # newest entries intact
