"""Pydantic API contracts. Names and fields mirror opal_listen.yaml; extra models/fields are additive."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NO_MATCH = "no_match"


class _Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- wakewords -------------------------------------------------------------------------------


class WakewordBase(BaseModel):
    name: str = Field(max_length=100)
    enabled: bool = True
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)


class WakewordCreate(WakewordBase):
    pass


class WakewordRead(WakewordBase, _Read):
    id: int
    created_at: datetime
    updated_at: datetime


class WakewordUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


# --- intents ---------------------------------------------------------------------------------


class IntentBase(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class IntentCreate(IntentBase):
    phrases: list[str] = Field(default_factory=list, description="Optional initial training phrases")


class IntentRead(IntentBase, _Read):
    id: int
    created_at: datetime
    updated_at: datetime


class IntentPhraseCreate(BaseModel):
    phrase: str = Field(max_length=500)


class IntentPhraseRead(_Read):
    phrase: str = Field(max_length=500)
    id: int
    intent_id: int
    created_at: datetime


class IntentReadWithPhrases(IntentRead):
    phrases: list[IntentPhraseRead] = []


class IntentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


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
    id: int
    created_at: datetime
    updated_at: datetime


class SlotAliasCreate(BaseModel):
    alias: str = Field(max_length=200)


class SlotAliasRead(_Read):
    alias: str = Field(max_length=200)
    id: int
    slot_value_id: int
    created_at: datetime


class SlotValueCreate(BaseModel):
    value: str = Field(max_length=200)


class SlotValueRead(_Read):
    value: str = Field(max_length=200)
    id: int
    slot_id: int
    created_at: datetime


class SlotValueReadWithAliases(SlotValueRead):
    aliases: list[SlotAliasRead] = []


class SlotReadWithValues(SlotRead):
    values: list[SlotValueReadWithAliases] = []


class SlotUpdate(BaseModel):
    name: str | None = None
    slot_type: str | None = None
    description: str | None = None


# --- profiles --------------------------------------------------------------------------------


class ProfileCreate(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = None
    is_active: bool = False
    wakeword_names: list[str] = []
    confidence_thresholds: dict[str, Any] = {}
    vad_settings: dict[str, Any] = {}
    enabled_intents: list[str] = []
    enabled_slots: list[str] = []
    model_paths: dict[str, str] = {}


class ProfileRead(_Read):
    id: int
    name: str
    description: str | None
    is_active: bool
    wakeword_names: list[str]
    confidence_thresholds: dict[str, Any]
    vad_settings: dict[str, Any]
    enabled_intents: list[str]
    enabled_slots: list[str]
    model_paths: dict[str, str]
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None
    wakeword_names: list[str] | None = None
    confidence_thresholds: dict[str, Any] | None = None
    vad_settings: dict[str, Any] | None = None
    enabled_intents: list[str] | None = None
    enabled_slots: list[str] | None = None
    model_paths: dict[str, str] | None = None


# --- settings --------------------------------------------------------------------------------


class SettingRead(_Read):
    id: int
    key: str
    value: str
    updated_at: datetime


class SettingUpdate(BaseModel):
    value: str


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
    recognition_log_id: int | None = None
    created_at: datetime


class PipelineStatus(BaseModel):
    running: bool
    state: str
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


# --- logs / failures / regression ------------------------------------------------------------


class RecognitionLogRead(_Read):
    id: int
    session_id: str | None
    source: str = "mic"
    profile_name: str | None = None
    wakeword: str | None
    transcript: str | None
    normalized_transcript: str | None
    intent_name: str | None
    slot_values: dict[str, Any]
    wakeword_confidence: float | None
    stt_confidence: float | None = None
    intent_confidence: float | None
    matched: bool
    no_match_reason: str | None = None
    total_latency_ms: int | None
    wakeword_latency_ms: int | None
    stt_latency_ms: int | None
    intent_latency_ms: int | None
    audio_level: float | None
    created_at: datetime


class FailedUtteranceBase(BaseModel):
    transcript: str = Field(max_length=1000)
    expected_intent: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)


class FailedUtteranceCreate(FailedUtteranceBase):
    recognition_log_id: int | None = None


class FailedUtteranceRead(FailedUtteranceBase, _Read):
    id: int
    recognition_log_id: int | None
    reviewed: bool
    promoted_to_phrase: bool
    created_at: datetime


class FailedUtteranceUpdate(BaseModel):
    expected_intent: str | None = None
    notes: str | None = None
    reviewed: bool | None = None
    promoted_to_phrase: bool | None = None


class RegressionTestBase(BaseModel):
    name: str = Field(max_length=200)
    phrase: str = Field(max_length=500)
    expected_intent: str = Field(max_length=100)
    enabled: bool = True


class RegressionTestCreate(RegressionTestBase):
    expected_slots: dict[str, Any] = {}


class RegressionTestRead(RegressionTestBase, _Read):
    id: int
    expected_slots: dict[str, Any]
    last_result: bool | None
    last_run_at: datetime | None
    created_at: datetime


class RegressionTestUpdate(BaseModel):
    name: str | None = None
    phrase: str | None = None
    expected_intent: str | None = None
    expected_slots: dict[str, Any] | None = None
    enabled: bool | None = None


class RegressionCaseResult(BaseModel):
    test_id: int
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
    id: int
    recognition_log_id: int | None
    wakeword_latency_ms: int | None
    stt_latency_ms: int | None
    intent_latency_ms: int | None
    total_latency_ms: int | None
    wakeword_confidence: float | None
    intent_confidence: float | None
    matched: bool
    audio_level: float | None = None
    created_at: datetime


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


# --- health / backup / config ----------------------------------------------------------------

CheckStatus = Literal["ok", "warning", "error"]


class HealthCheckResult(BaseModel):
    name: str
    status: CheckStatus
    detail: str


class HealthReport(BaseModel):
    status: CheckStatus
    checks: list[HealthCheckResult]
    checked_at: datetime


class AudioDevice(BaseModel):
    index: int
    name: str
    max_input_channels: int
    default_sample_rate: float
    is_default: bool


class BackupInfo(BaseModel):
    filename: str
    size_bytes: int
    created_at: datetime


class ConfigValidationReport(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
    sources: dict[str, bool] = {}


class ConfigImportResult(BaseModel):
    status: Literal["imported"]
    backup_filename: str
    counts: dict[str, int]
    warnings: list[str]
