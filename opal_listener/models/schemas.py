"""Pydantic API contracts. Names and fields mirror openapi/opal_listen_v2.0.0.yaml; extra models/fields are additive."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Path as ApiPath
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, RootModel, model_validator
from pydantic.json_schema import SkipJsonSchema

NO_MATCH = "no_match"

# Opaque public identifier: stored as an integer PK, exposed as a string on the wire.
PublicId = Annotated[str, BeforeValidator(str), Field(min_length=1)]
# Nonempty public identifier for path parameters (contract *Id params are minLength: 1 strings).
IdPath = Annotated[str, ApiPath(min_length=1)]


class _Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def _require_one_field(data: Any) -> Any:
    """Reject an empty update object (contract minProperties: 1)."""
    if isinstance(data, dict) and not data:
        raise ValueError("at least one field must be provided")
    return data


# --- wakewords -------------------------------------------------------------------------------


class WakewordBase(BaseModel):
    name: str = Field(max_length=100)
    enabled: bool = True
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)


class WakewordCreate(WakewordBase):
    pass


class WakewordRead(WakewordBase, _Read):
    id: PublicId
    created_at: datetime
    updated_at: datetime


class WakewordUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"minProperties": 1})

    name: str | None = None
    enabled: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    _at_least_one = model_validator(mode="before")(_require_one_field)


# --- intents ---------------------------------------------------------------------------------


class IntentBase(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class IntentCreate(IntentBase):
    phrases: list[str] = Field(default_factory=list, description="Optional initial training phrases")


class IntentRead(IntentBase, _Read):
    id: PublicId
    created_at: datetime
    updated_at: datetime


class IntentPhraseCreate(BaseModel):
    phrase: str = Field(max_length=500)


class IntentPhraseRead(_Read):
    phrase: str = Field(max_length=500)
    id: PublicId
    intent_id: PublicId
    created_at: datetime


class IntentReadWithPhrases(IntentRead):
    phrases: list[IntentPhraseRead] = []


class IntentUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"minProperties": 1})

    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    _at_least_one = model_validator(mode="before")(_require_one_field)


# --- slots -----------------------------------------------------------------------------------

SlotType = Literal["list", "number", "text"]
SLOT_TYPES: tuple[str, ...] = ("list", "number", "text")


class SlotBase(BaseModel):
    name: str = Field(max_length=100)
    slot_type: str = Field(default="list", max_length=50)
    description: str | None = Field(default=None, max_length=500)


class SlotCreate(SlotBase):
    pass


class SlotRead(SlotBase, _Read):
    id: PublicId
    created_at: datetime
    updated_at: datetime


class SlotAliasCreate(BaseModel):
    alias: str = Field(max_length=200)


class SlotAliasRead(_Read):
    alias: str = Field(max_length=200)
    id: PublicId
    slot_value_id: PublicId
    created_at: datetime


class SlotValueCreate(BaseModel):
    value: str = Field(max_length=200)


class SlotValueRead(_Read):
    value: str = Field(max_length=200)
    id: PublicId
    slot_id: PublicId
    created_at: datetime


class SlotValueReadWithAliases(SlotValueRead):
    aliases: list[SlotAliasRead] = []


class SlotReadWithValues(SlotRead):
    values: list[SlotValueReadWithAliases] = []


class SlotUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"minProperties": 1})

    name: str | None = None
    slot_type: str | None = None
    description: str | None = None

    _at_least_one = model_validator(mode="before")(_require_one_field)


# --- profiles --------------------------------------------------------------------------------


class OpalProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    is_active: bool = False
    wakeword_names: list[str] = []
    confidence_thresholds: dict[str, Any] = {}
    vad_settings: dict[str, Any] = {}
    enabled_intents: list[str] = []
    enabled_slots: list[str] = []
    model_paths: dict[str, str] = {}


class OpalProfile(_Read):
    """Kernel OpalProfile; only id/is_active/name are normative — the rest are service fields."""

    id: PublicId
    name: str
    is_active: bool
    description: str | None = None
    wakeword_names: list[str] = []
    confidence_thresholds: dict[str, Any] = {}
    vad_settings: dict[str, Any] = {}
    enabled_intents: list[str] = []
    enabled_slots: list[str] = []
    model_paths: dict[str, str] = {}
    created_at: SkipJsonSchema[datetime | None] = None
    updated_at: SkipJsonSchema[datetime | None] = None


class OpalProfilePatch(BaseModel):
    model_config = ConfigDict(json_schema_extra={"minProperties": 1})

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    is_active: bool | None = None
    wakeword_names: list[str] | None = None
    confidence_thresholds: dict[str, Any] | None = None
    vad_settings: dict[str, Any] | None = None
    enabled_intents: list[str] | None = None
    enabled_slots: list[str] | None = None
    model_paths: dict[str, str] | None = None

    _at_least_one = model_validator(mode="before")(_require_one_field)


# --- settings --------------------------------------------------------------------------------


SettingSource = Literal["default", "persisted", "dotenv", "environment"]


class OpalValidationIssue(BaseModel):
    """Kernel OpalValidationIssue: one structured validation issue or warning."""

    model_config = ConfigDict(extra="allow")

    code: str
    path: str
    message: str


class SettingRead(_Read):
    """Kernel SettingRead: effective/configured/default values plus provenance metadata."""

    id: SkipJsonSchema[int | None] = None
    key: str
    value: Any
    configured_value: Any
    updated_at: SkipJsonSchema[datetime | None] = None
    default: Any
    has_override: bool
    is_default: bool
    source: SettingSource
    restart_required: bool
    pending_restart: bool
    warnings: list[OpalValidationIssue] | None = None


class SettingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any


# --- recognition -----------------------------------------------------------------------------


class TextTestRequest(BaseModel):
    text: str = Field(max_length=1000)


class IntentAlternative(BaseModel):
    intent_name: str
    confidence: float
    phrase: str


class RecognitionResult(BaseModel):
    """Outcome of one pass through the pipeline (live, audio file or text)."""

    session_id: str
    source: Literal["mic", "audio", "text"]
    profile_name: str | None = None
    wakeword: str | None = None
    transcript: str | None = None
    normalized_transcript: str | None = None
    intent_name: str | None = None
    slot_values: dict[str, Any] = {}
    wakeword_confidence: float | None = None
    stt_confidence: float | None = None
    intent_confidence: float | None = None
    matched: bool = False
    no_match_reason: str | None = None
    matched_phrase: str | None = None
    alternatives: list[IntentAlternative] = []
    wakeword_latency_ms: int | None = None
    stt_latency_ms: int | None = None
    intent_latency_ms: int | None = None
    total_latency_ms: int | None = None
    audio_level: float | None = None
    deduplicated: bool = False
    refinement_error: str | None = None  # set when refinement failed/timed out and the draft was used
    # dispatch targets actually invoked for this result (subset of {"local", "event"}); always
    # empty for diagnostics, deduplicated repeats, and superseded-generation results
    dispatched_to: list[str] = []
    recognition_log_id: PublicId | None = None
    created_at: datetime


class PipelineStatus(BaseModel):
    running: bool
    state: Literal["stopped", "starting", "running", "stopping", "error"]
    provider: str
    profile_name: str | None
    audio_device: str | None
    capture_sample_rate: int | None
    started_at: datetime | None
    uptime_s: float | None
    frames_processed: int
    wakeword_detections: int
    sessions: int
    results: int
    audio_restarts: int
    last_audio_level: float | None
    last_result_at: datetime | None
    last_error: str | None
    # lightweight reliability counters, reset per run: mic_overflows, dropped_audio_blocks,
    # audio_queue_depth/utilization, refine_queue_depth, refinement_completed/failed,
    # recoveries, and last-result stt/refine/intent/publish latencies (ms)
    metrics: dict[str, float] = {}


# --- failures / regression -------------------------------------------------------------------


class FailedUtteranceBase(BaseModel):
    transcript: str = Field(max_length=1000)
    expected_intent: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)


class FailedUtteranceCreate(FailedUtteranceBase):
    recognition_log_id: PublicId | None = None


class FailedUtteranceRead(FailedUtteranceBase, _Read):
    id: PublicId
    recognition_log_id: PublicId | None
    reviewed: bool
    promoted_to_phrase: bool
    created_at: datetime


class FailedUtteranceUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"minProperties": 1})

    expected_intent: str | None = None
    notes: str | None = None
    reviewed: bool | None = None
    promoted_to_phrase: bool | None = None

    _at_least_one = model_validator(mode="before")(_require_one_field)


class FailedUtterancePromoteRequest(BaseModel):
    """Contract FailedUtterancePromoteRequest: target intent id plus optional regression creation."""

    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(
        min_length=1, description="Identifier of the intent this utterance becomes a training phrase for."
    )
    create_regression: bool = True


class RegressionTestBase(BaseModel):
    name: str = Field(max_length=200)
    phrase: str = Field(max_length=500)
    expected_intent: str = Field(max_length=100)
    enabled: bool = True


class RegressionTestCreate(RegressionTestBase):
    expected_slots: dict[str, Any] = {}


class RegressionTestRead(RegressionTestBase, _Read):
    id: PublicId
    expected_slots: dict[str, Any]
    last_result: bool | None
    last_run_at: datetime | None
    created_at: datetime


class RegressionTestUpdate(BaseModel):
    model_config = ConfigDict(json_schema_extra={"minProperties": 1})

    name: str | None = None
    phrase: str | None = None
    expected_intent: str | None = None
    expected_slots: dict[str, Any] | None = None
    enabled: bool | None = None

    _at_least_one = model_validator(mode="before")(_require_one_field)


class RegressionCaseResult(BaseModel):
    test_id: PublicId
    name: str
    phrase: str
    expected_intent: str
    actual_intent: str
    expected_slots: dict[str, Any]
    actual_slots: dict[str, Any]
    intent_confidence: float | None
    passed: bool
    failure_reason: str | None = None


class RegressionRunResult(BaseModel):
    total: int
    passed: int
    failed: int
    pass_rate: float
    results: list[RegressionCaseResult]


class PromoteResult(BaseModel):
    failed_utterance: FailedUtteranceRead
    phrase: IntentPhraseRead
    regression_test: RegressionTestRead | None = None


# --- performance -----------------------------------------------------------------------------


class PerformanceResultRead(_Read):
    id: PublicId
    recognition_log_id: PublicId | None
    wakeword_latency_ms: int | None
    stt_latency_ms: int | None
    intent_latency_ms: int | None
    total_latency_ms: int | None
    wakeword_confidence: float | None
    intent_confidence: float | None
    matched: bool
    audio_level: float | None = None
    created_at: datetime


class PerformanceHistory(RootModel[list[PerformanceResultRead]]):
    """Bounded list of performance results, newest first; `limit` caps the returned items."""


class LatencyStats(BaseModel):
    avg: float | None
    p50: float | None
    p95: float | None
    max: float | None


class PerformanceSummary(BaseModel):
    count: int
    matched_count: int
    no_match_count: int
    match_rate: float | None
    avg_wakeword_confidence: float | None
    avg_intent_confidence: float | None
    wakeword_latency_ms: LatencyStats
    stt_latency_ms: LatencyStats
    intent_latency_ms: LatencyStats
    total_latency_ms: LatencyStats


# --- configuration snapshots (kernel OpalConfig*) -----------------------------------------------

_SNAPSHOT_FORMAT_VERSION = "1.0.0"
_SNAPSHOT_MODULE = "opal-listen"


class OpalConfigSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: Literal["managed", "response"] = "managed"


class OpalConfigSnapshot(BaseModel):
    """Kernel OpalConfigSnapshot: closed envelope — `settings` + `resources` are the only slots."""

    model_config = ConfigDict(extra="forbid")

    format_version: str
    module: str = Field(min_length=1)
    created_at: datetime
    settings: dict[str, Any]
    resources: dict[str, Any]


class OpalConfigSaveResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    snapshot: OpalConfigSnapshot
    managed: bool
    # service extras (contract is additionalProperties: true)
    revision: str | None = None
    destination: Literal["managed", "response"] | None = None
    destination_ref: str | None = None


def _reject_explicit_null(data: Any) -> Any:
    if isinstance(data, dict) and "snapshot" in data and data["snapshot"] is None:
        raise ValueError("explicit null snapshot is invalid; omit the field or send {}")
    return data


class OpalConfigLoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: OpalConfigSnapshot | None = None

    _no_null_snapshot = model_validator(mode="before")(_reject_explicit_null)


class OpalConfigLoadResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    applied: Literal[True]
    pending_restart_fields: list[str] = []
    warnings: list[OpalValidationIssue] | None = None
    # service extras: resulting live-configuration revision + per-section item counts applied
    revision: str | None = None
    applied_counts: dict[str, int] | None = None


class OpalConfigValidateRequest(BaseModel):
    """Snapshot is typed loosely so malformed envelopes report structured issues, not 422."""

    model_config = ConfigDict(extra="forbid")

    snapshot: dict[str, Any] | None = None

    _no_null_snapshot = model_validator(mode="before")(_reject_explicit_null)


class OpalConfigValidation(BaseModel):
    model_config = ConfigDict(extra="allow")

    valid: bool
    errors: list[OpalValidationIssue]
    warnings: list[OpalValidationIssue]


# --- health / config -------------------------------------------------------------------------

CheckStatus = Literal["ok", "warning", "error"]


class HealthCheckResult(BaseModel):
    name: str
    status: CheckStatus
    detail: str


class OpalHealthReport(BaseModel):
    """Kernel OpalHealthReport; status is the only normative member.

    checked_at stays in the emitted payload but is hidden from the schema: the kernel types
    it non-nullable and our optional timestamp would otherwise fail the format check.
    """

    status: CheckStatus
    checks: list[HealthCheckResult] = []
    checked_at: SkipJsonSchema[datetime | None] = None


class AudioDevice(BaseModel):
    index: int
    name: str
    max_input_channels: int
    default_sample_rate: float
    is_default: bool


class OpalValidationReport(BaseModel):
    """Kernel OpalValidationReport; valid is the only normative member."""

    valid: bool
    errors: list[str | dict[str, Any]] = []
    warnings: list[str | dict[str, Any]] = []
    sources: dict[str, bool] = {}


# --- service lifecycle -----------------------------------------------------------------------


class OpalServiceStartOptions(BaseModel):
    """Kernel OpalServiceStartOptions: open body; only `append` is portable."""

    model_config = ConfigDict(extra="allow")

    append: bool = False


class OpalServiceStatus(BaseModel):
    """Kernel OpalServiceStatus for service start/stop."""

    model_config = ConfigDict(extra="allow")

    state: Literal["running", "stopped"]
    pid: int | None = None
    previous_pid: int | None = None
    log_file: str | None = None


class OpalServiceLogTail(BaseModel):
    """Kernel OpalServiceLogTail for service log."""

    model_config = ConfigDict(extra="allow")

    log_file: str | None = None
    lines: list[str]


# --- personas --------------------------------------------------------------------------------

PERSONA_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class PersonaBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    voice_id: str | None = Field(default=None, min_length=1)
    enabled: bool = True


class OpalPersonaCreate(PersonaBase):
    """Kernel OpalPersonaCreate (contract `PersonaCreate` is a $ref alias of it)."""

    persona_id: str = Field(min_length=1, max_length=200, pattern=PERSONA_ID_PATTERN)


class OpalPersonaUpdate(BaseModel):
    """Kernel OpalPersonaUpdate: partial persona update; minProperties: 1 per the contract."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    voice_id: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _at_least_one_field(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data:
            raise ValueError("at least one field must be provided")
        return data


class OpalPersonaRead(_Read):
    """Kernel OpalPersonaRead; `origin` marks provenance (owner-sync is not implemented yet)."""

    persona_id: str
    name: str
    description: str | None = None
    voice_id: str | None = None
    enabled: bool
    origin: Literal["local", "owner"] = "local"
    created_at: datetime
    updated_at: datetime
