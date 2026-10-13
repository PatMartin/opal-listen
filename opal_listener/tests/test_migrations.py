"""Database migration tests: upgrade/downgrade and model/migration drift."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import opal_listener.models.tables  # noqa: F401
from opal_listener.data.db import Database, alembic_config

TABLES = {
    "wakewords", "intents", "intent_phrases", "slots", "slot_values", "slot_aliases", "profiles", "settings",
    "recognition_logs", "failed_utterances", "regression_tests", "performance_results",
}  # fmt: skip


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'migrate.db'}"


def test_upgrade_creates_all_tables(tmp_path: Path) -> None:
    db = Database(_url(tmp_path))
    db.migrate()
    tables = set(inspect(db.engine).get_table_names())
    assert tables >= TABLES
    assert "alembic_version" in tables
    db.dispose()


def test_models_match_migrations(tmp_path: Path) -> None:
    """Fails if a model changed without a new Alembic revision."""
    db = Database(_url(tmp_path))
    db.migrate()
    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)
    assert diff == [], f"models and migrations differ; run 'alembic revision --autogenerate': {diff}"
    db.dispose()


def test_downgrade_and_reupgrade(tmp_path: Path) -> None:
    url = _url(tmp_path)
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(url)
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(cfg, "head")
    assert set(inspect(engine).get_table_names()) >= TABLES
    engine.dispose()


def test_migrate_is_idempotent_and_single_head(tmp_path: Path) -> None:
    db = Database(_url(tmp_path))
    db.migrate()
    db.migrate()
    heads = ScriptDirectory.from_config(alembic_config(db.url)).get_heads()
    assert len(heads) == 1
    with db.engine.connect() as conn:
        assert MigrationContext.configure(conn).get_current_revision() == heads[0]
    db.dispose()


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    import pytest
    from sqlalchemy.exc import IntegrityError

    from opal_listener.models.tables import IntentPhrase

    db = Database(_url(tmp_path))
    db.migrate()
    with db.session() as session:
        session.add(IntentPhrase(intent_id=999, phrase="orphan"))
        with pytest.raises(IntegrityError):
            session.commit()
    db.dispose()
