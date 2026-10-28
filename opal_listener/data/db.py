"""SQLite engine, sessions and Alembic migration runner."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from opal_listener.service.errors import ServiceUnavailableError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _configure_sqlite(dbapi_connection: sqlite3.Connection, _record: object) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False})
    event.listen(engine, "connect", _configure_sqlite)
    return engine


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = make_engine(url)
        self._sess_cv = threading.Condition()
        self._active_sessions = 0
        self._in_maintenance = False
        self._tls = threading.local()

    @property
    def path(self) -> Path:
        return Path(self.url.removeprefix("sqlite:///"))

    def migrate(self) -> None:
        with self.engine.begin() as connection:
            cfg = alembic_config(self.url)
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sess_cv:
            if self._in_maintenance and not getattr(self._tls, "maintenance", False):
                raise ServiceUnavailableError("database restore in progress")
            self._active_sessions += 1
        try:
            with Session(self.engine, expire_on_commit=False) as session:
                yield session
        finally:
            with self._sess_cv:
                self._active_sessions -= 1
                self._sess_cv.notify_all()

    @contextmanager
    def maintenance(self, timeout: float = 10.0) -> Iterator[None]:
        """Exclusive maintenance window (e.g. backup restore): waits for active sessions to
        finish, then rejects new sessions from other threads until released."""
        with self._sess_cv:
            self._in_maintenance = True
            drained = self._sess_cv.wait_for(lambda: self._active_sessions == 0, timeout=timeout)
            if not drained:
                self._in_maintenance = False
                raise ServiceUnavailableError("timed out waiting for database activity to quiesce")
            self._tls.maintenance = True
        try:
            yield
        finally:
            self._tls.maintenance = False
            with self._sess_cv:
                self._in_maintenance = False

    def ping(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
