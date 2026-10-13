"""SQLModel table definitions (SQLite). Column names are snake_case and mirror the API contract."""

from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    # sqlmodel >= 0.0.4x rejects naive datetimes on bind
    return datetime.now(UTC)


def _fk(target: str, nullable: bool = False, ondelete: str = "CASCADE") -> Any:
    return Field(
        default=None,
        sa_column=Column(Integer, ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True),
    )


class Wakeword(SQLModel, table=True):
    __tablename__ = "wakewords"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100, unique=True, index=True)
    enabled: bool = True
    confidence_threshold: float = 0.8
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Intent(SQLModel, table=True):
    __tablename__ = "intents"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100, unique=True, index=True)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    confidence_threshold: float = 0.7
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    phrases: list["IntentPhrase"] = Relationship(
        back_populates="intent",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "IntentPhrase.id"},
    )


class IntentPhrase(SQLModel, table=True):
    __tablename__ = "intent_phrases"
    __table_args__ = (UniqueConstraint("intent_id", "phrase", name="uq_intent_phrases_intent_phrase"),)

    id: int | None = Field(default=None, primary_key=True)
    intent_id: int = _fk("intents.id")
    phrase: str = Field(max_length=500)
    created_at: datetime = Field(default_factory=utc_now)

    intent: Optional["Intent"] = Relationship(back_populates="phrases")


class Slot(SQLModel, table=True):
    __tablename__ = "slots"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100, unique=True, index=True)
    slot_type: str = Field(default="list", max_length=50)
    description: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    values: list["SlotValue"] = Relationship(
        back_populates="slot",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "SlotValue.id"},
    )


class SlotValue(SQLModel, table=True):
    __tablename__ = "slot_values"
    __table_args__ = (UniqueConstraint("slot_id", "value", name="uq_slot_values_slot_value"),)

    id: int | None = Field(default=None, primary_key=True)
    slot_id: int = _fk("slots.id")
    value: str = Field(max_length=200)
    created_at: datetime = Field(default_factory=utc_now)

    slot: Optional["Slot"] = Relationship(back_populates="values")
    aliases: list["SlotAlias"] = Relationship(
        back_populates="slot_value",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "SlotAlias.id"},
    )


class SlotAlias(SQLModel, table=True):
    __tablename__ = "slot_aliases"
    __table_args__ = (UniqueConstraint("slot_value_id", "alias", name="uq_slot_aliases_value_alias"),)

    id: int | None = Field(default=None, primary_key=True)
    slot_value_id: int = _fk("slot_values.id")
    alias: str = Field(max_length=200)
    created_at: datetime = Field(default_factory=utc_now)

    slot_value: Optional["SlotValue"] = Relationship(back_populates="aliases")


class Profile(SQLModel, table=True):
    __tablename__ = "profiles"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100, unique=True, index=True)
    description: str | None = None
    is_active: bool = False
    wakeword_names: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    confidence_thresholds: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    vad_settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    enabled_intents: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    enabled_slots: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    model_paths: dict[str, str] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(max_length=100, unique=True, index=True)
    value: str
    updated_at: datetime = Field(default_factory=utc_now)


class RecognitionLog(SQLModel, table=True):
    __tablename__ = "recognition_logs"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str | None = Field(default=None, max_length=64, index=True)
    source: str = Field(default="mic", max_length=20)
    profile_name: str | None = Field(default=None, max_length=100)
    wakeword: str | None = Field(default=None, max_length=100)
    transcript: str | None = None
    normalized_transcript: str | None = None
    intent_name: str | None = Field(default=None, max_length=100, index=True)
    slot_values: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    wakeword_confidence: float | None = None
    stt_confidence: float | None = None
    intent_confidence: float | None = None
    matched: bool = Field(default=False, index=True)
    no_match_reason: str | None = Field(default=None, max_length=50)
    total_latency_ms: int | None = None
    wakeword_latency_ms: int | None = None
    stt_latency_ms: int | None = None
    intent_latency_ms: int | None = None
    audio_level: float | None = None
    created_at: datetime = Field(default_factory=utc_now, index=True)


class FailedUtterance(SQLModel, table=True):
    __tablename__ = "failed_utterances"

    id: int | None = Field(default=None, primary_key=True)
    transcript: str = Field(max_length=1000)
    expected_intent: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)
    recognition_log_id: int | None = _fk("recognition_logs.id", nullable=True, ondelete="SET NULL")
    reviewed: bool = False
    promoted_to_phrase: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class RegressionTest(SQLModel, table=True):
    __tablename__ = "regression_tests"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=200)
    phrase: str = Field(max_length=500)
    expected_intent: str = Field(max_length=100)
    expected_slots: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    last_result: bool | None = None
    last_run_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)


class PerformanceResult(SQLModel, table=True):
    __tablename__ = "performance_results"

    id: int | None = Field(default=None, primary_key=True)
    recognition_log_id: int | None = _fk("recognition_logs.id", nullable=True, ondelete="CASCADE")
    wakeword_latency_ms: int | None = None
    stt_latency_ms: int | None = None
    intent_latency_ms: int | None = None
    total_latency_ms: int | None = None
    wakeword_confidence: float | None = None
    intent_confidence: float | None = None
    matched: bool = False
    audio_level: float | None = None
    created_at: datetime = Field(default_factory=utc_now, index=True)
