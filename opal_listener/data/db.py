"""SQLite engine, sessions and Alembic migration runner."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

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
        with Session(self.engine, expire_on_commit=False) as session:
            yield session

    def ping(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
