"""SQLite online backups, validated restore with automatic rollback."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from opal_listener.models.schemas import BackupInfo
from opal_listener.service.errors import InvalidRequestError, NotFoundError

FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.db$")
REQUIRED_TABLES = frozenset(
    {
        "wakewords",
        "intents",
        "intent_phrases",
        "slots",
        "slot_values",
        "slot_aliases",
        "profiles",
        "settings",
        "recognition_logs",
        "failed_utterances",
        "regression_tests",
        "performance_results",
    }
)


def safe_backup_path(backup_dir: Path, filename: str) -> Path:
    """Reject anything that is not a plain backup filename inside backup_dir (path traversal guard)."""
    if not FILENAME_PATTERN.match(filename) or ".." in filename:
        raise InvalidRequestError(f"invalid backup filename '{filename}'")
    base = backup_dir.resolve()
    path = (base / filename).resolve()
    if path.parent != base:
        raise InvalidRequestError(f"invalid backup filename '{filename}'")
    return path


def list_backups(backup_dir: Path) -> list[BackupInfo]:
    if not backup_dir.exists():
        return []
    items = [
        BackupInfo(
            filename=p.name,
            size_bytes=p.stat().st_size,
            created_at=datetime.fromtimestamp(p.stat().st_mtime, UTC).replace(tzinfo=None),
        )
        for p in backup_dir.glob("*.db")
        if FILENAME_PATTERN.match(p.name)
    ]
    return sorted(items, key=lambda b: (b.created_at, b.filename), reverse=True)


def create_backup(db_path: Path, backup_dir: Path, label: str = "manual") -> str:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    safe_label = re.sub(r"[^a-z0-9_]", "_", label.lower())[:30] or "manual"
    filename = f"opal_listener_{stamp}_{safe_label}.db"
    target = safe_backup_path(backup_dir, filename)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return filename


def verify_backup(path: Path) -> None:
    if not path.exists():
        raise NotFoundError(f"backup '{path.name}' not found")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise InvalidRequestError(f"backup '{path.name}' is not a valid SQLite database: {exc}") from exc
    if not integrity or integrity[0] != "ok":
        raise InvalidRequestError(f"backup '{path.name}' failed integrity check")
    missing = REQUIRED_TABLES - tables
    if missing:
        raise InvalidRequestError(f"backup '{path.name}' is missing tables: {', '.join(sorted(missing))}")


def copy_database(src_path: Path, dst_path: Path) -> None:
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def delete_backup(backup_dir: Path, filename: str) -> None:
    path = safe_backup_path(backup_dir, filename)
    if not path.exists():
        raise NotFoundError(f"backup '{filename}' not found")
    path.unlink()
