"""Energy-based voice activity detection with an adaptive noise floor."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class VadSettings(BaseModel):
    """Profile-tunable VAD parameters (profiles.vad_settings)."""

    model_config = ConfigDict(extra="forbid")

    energy_threshold_dbfs: float = Field(default=-45.0, ge=-90.0, le=0.0)
    noise_margin_db: float = Field(default=10.0, ge=0.0, le=40.0)
    speech_start_ms: int = Field(default=150, ge=0, le=2000)
    silence_end_ms: int = Field(default=700, ge=100, le=5000)
    no_speech_timeout_ms: int = Field(default=4000, ge=500, le=30000)
    max_utterance_ms: int = Field(default=8000, ge=1000, le=60000)
    pre_roll_ms: int = Field(default=1000, ge=0, le=3000)
    use_stt_endpoint: bool = True


class VadEvent(StrEnum):
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"
    NO_SPEECH = "no_speech"
    MAX_LENGTH = "max_length"


class EnergyVad:
    def __init__(self, settings: VadSettings, frame_ms: int, noise_floor_dbfs: float = -60.0) -> None:
        self.settings = settings
        self.frame_ms = frame_ms
        self.noise_floor_dbfs = noise_floor_dbfs
        self.reset()

    def reset(self) -> None:
        self.speech_started = False
        self.elapsed_ms = 0
        self._speech_run_ms = 0
        self._silence_run_ms = 0

    @property
    def threshold_dbfs(self) -> float:
        return max(self.settings.energy_threshold_dbfs, self.noise_floor_dbfs + self.settings.noise_margin_db)

    def is_speech(self, dbfs: float) -> bool:
        return dbfs >= self.threshold_dbfs

    def observe_background(self, dbfs: float) -> None:
        """Track ambient level while idle: fall quickly to quieter levels, rise slowly (speech is transient)."""
        rate = 0.2 if dbfs < self.noise_floor_dbfs else 0.02
        self.noise_floor_dbfs += rate * (dbfs - self.noise_floor_dbfs)

    def process(self, dbfs: float) -> VadEvent | None:
        s = self.settings
        self.elapsed_ms += self.frame_ms
        if self.is_speech(dbfs):
            self._speech_run_ms += self.frame_ms
            self._silence_run_ms = 0
        else:
            self._silence_run_ms += self.frame_ms
            self._speech_run_ms = 0

        if not self.speech_started:
            if self._speech_run_ms >= max(s.speech_start_ms, self.frame_ms):
                self.speech_started = True
                return VadEvent.SPEECH_START
            if self.elapsed_ms >= s.no_speech_timeout_ms:
                return VadEvent.NO_SPEECH
            return None
        if self._silence_run_ms >= s.silence_end_ms:
            return VadEvent.SPEECH_END
        if self.elapsed_ms >= s.max_utterance_ms:
            return VadEvent.MAX_LENGTH
        return None
