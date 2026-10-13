"""Frame-driven recognition state machine shared by live listening and audio-file tests.

IDLE: frames go to the wakeword detector (and a pre-roll ring buffer).
CAPTURING: after a wakeword candidate, frames go to the transcriber + VAD while the detector keeps
running until it confirms (with confidence) or rejects the wakeword.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from opal_listener.service.audio.pcm import rms_dbfs
from opal_listener.service.audio.vad import EnergyVad, VadEvent, VadSettings
from opal_listener.service.nlu.normalizer import normalize_text, split_wakeword
from opal_listener.service.providers.base import (
    ListenerProvider,
    SttRefiner,
    Transcriber,
    Transcript,
    WakewordEvent,
    WakewordEventKind,
)


@dataclass
class PipelineEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    outcome: CommandOutcome | None = None


@dataclass
class CommandOutcome:
    session_id: str
    wakeword: str | None
    wakeword_confidence: float | None
    wakeword_latency_ms: int | None
    transcript: Transcript
    stt_latency_ms: int
    end_reason: str
    ended_at: float  # time.monotonic() when end of speech was decided
    audio_level: float | None
    pcm: bytes = b""


@dataclass
class _Session:
    session_id: str
    wakeword: str | None
    transcriber: Transcriber
    vad: EnergyVad
    wake_pending: bool
    wake_confidence: float | None = None
    wake_latency_ms: int | None = None
    frames: list[bytes] = field(default_factory=list)
    speech_levels: list[float] = field(default_factory=list)
    all_levels: list[float] = field(default_factory=list)


class RecognitionPipeline:
    def __init__(
        self,
        provider: ListenerProvider,
        wakewords: Sequence[str],
        wakeword_thresholds: dict[str, float],
        grammar: Sequence[str] | None,
        vad_settings: VadSettings,
        frame_ms: int,
        cooldown_ms: int,
        refiner: SttRefiner | None = None,
        debug: bool = False,
    ) -> None:
        self.provider = provider
        self.wakewords = list(wakewords)
        self.wakeword_thresholds = wakeword_thresholds
        self.grammar = list(grammar) if grammar is not None else None
        self.vad_settings = vad_settings
        self.frame_ms = frame_ms
        self.cooldown_ms = cooldown_ms
        self.refiner = refiner
        self.debug = debug
        self.detector = provider.create_wakeword_detector(self.wakewords) if self.wakewords else None
        self._background_vad = EnergyVad(vad_settings, frame_ms)
        self._ring: deque[bytes] = deque(maxlen=max(1, vad_settings.pre_roll_ms // frame_ms))
        self._session: _Session | None = None
        self._audio_ms = 0
        self._detector_started_ms = 0
        self._cooldown_until_ms = 0
        self._candidate_at: tuple[int, float] | None = None
        self.last_level: float | None = None

    @property
    def state(self) -> str:
        return "capturing" if self._session else "listening"

    @property
    def idle(self) -> bool:
        return self._session is None

    # --- frame handling -----------------------------------------------------------------------

    def feed(self, frame: bytes, captured_at: float | None = None) -> list[PipelineEvent]:
        captured_at = time.monotonic() if captured_at is None else captured_at
        self._audio_ms += self.frame_ms
        level = rms_dbfs(frame)
        self.last_level = level
        events: list[PipelineEvent] = []
        if self.debug:
            events.append(PipelineEvent("audio_level", {"dbfs": round(level, 1), "state": self.state}))
        if self._session is None:
            self._feed_idle(frame, level, captured_at, events)
        else:
            self._feed_session(frame, level, events)
        return events

    def _feed_idle(self, frame: bytes, level: float, captured_at: float, events: list[PipelineEvent]) -> None:
        self._ring.append(frame)
        self._background_vad.observe_background(level)
        if self.detector is None:
            return
        wake_events = self.detector.accept(frame)
        if self._audio_ms < self._cooldown_until_ms:
            return
        for event in wake_events:
            if event.kind in (WakewordEventKind.CANDIDATE, WakewordEventKind.CONFIRMED):
                self._open_session(event, captured_at, events)
                if event.kind is WakewordEventKind.CONFIRMED:
                    self._confirm(event, events)
                break

    def _open_session(self, event: WakewordEvent, captured_at: float, events: list[PipelineEvent]) -> None:
        self._candidate_at = (self._audio_ms, max(0.0, (time.monotonic() - captured_at) * 1000))
        vad = EnergyVad(self.vad_settings, self.frame_ms, self._background_vad.noise_floor_dbfs)
        session = _Session(
            session_id=uuid.uuid4().hex[:12],
            wakeword=event.wakeword,
            transcriber=self.provider.create_transcriber(self.grammar),
            vad=vad,
            wake_pending=True,
        )
        self._session = session
        events.append(
            PipelineEvent("wakeword_candidate", {"session_id": session.session_id, "wakeword": event.wakeword})
        )
        for pre in self._ring:
            session.frames.append(pre)
            session.transcriber.accept(pre)
        self._ring.clear()

    def _confirm(self, event: WakewordEvent, events: list[PipelineEvent]) -> None:
        session = self._session
        assert session is not None
        session.wake_pending = False
        session.wakeword = event.wakeword or session.wakeword
        session.wake_confidence = event.confidence
        processing_ms = 0.0
        audio_latency_ms = 0
        if self._candidate_at is not None:
            candidate_audio_ms, processing_ms = self._candidate_at
            if event.end_offset_ms is not None:
                audio_latency_ms = max(0, candidate_audio_ms - (self._detector_started_ms + event.end_offset_ms))
        session.wake_latency_ms = int(audio_latency_ms + processing_ms)
        threshold = self.wakeword_thresholds.get(session.wakeword or "", 0.0)
        data = {
            "session_id": session.session_id,
            "wakeword": session.wakeword,
            "confidence": event.confidence,
        }
        if event.confidence is not None and event.confidence < threshold:
            events.append(PipelineEvent("wakeword_rejected", {**data, "reason": "below_threshold"}))
            self._end_session()
            return
        events.append(PipelineEvent("wakeword_confirmed", data))

    def _handle_wake_events(self, wake_events: list[WakewordEvent], events: list[PipelineEvent]) -> bool:
        """Process detector events during a session. Returns False if the session was cancelled."""
        for event in wake_events:
            if self._session is None or not self._session.wake_pending:
                break
            if event.kind is WakewordEventKind.CONFIRMED:
                self._confirm(event, events)
            elif event.kind is WakewordEventKind.REJECTED:
                events.append(
                    PipelineEvent(
                        "wakeword_rejected",
                        {
                            "session_id": self._session.session_id,
                            "wakeword": event.wakeword,
                            "reason": "not_confirmed",
                        },
                    )
                )
                self._end_session()
        return self._session is not None

    def _feed_session(self, frame: bytes, level: float, events: list[PipelineEvent]) -> None:
        session = self._session
        assert session is not None
        session.frames.append(frame)
        session.all_levels.append(level)
        if (
            session.wake_pending
            and self.detector is not None
            and not self._handle_wake_events(self.detector.accept(frame), events)
        ):
            return
        update = session.transcriber.accept(frame)
        if self.debug and update.partial:
            events.append(PipelineEvent("partial", {"session_id": session.session_id, "text": update.partial}))
        vad_event = session.vad.process(level)
        if session.vad.is_speech(level):
            session.speech_levels.append(level)
        if vad_event is VadEvent.SPEECH_START:
            events.append(PipelineEvent("speech_start", {"session_id": session.session_id}))
        end_reason: str | None = None
        if vad_event in (VadEvent.SPEECH_END, VadEvent.MAX_LENGTH, VadEvent.NO_SPEECH):
            end_reason = vad_event.value
        elif (
            update.endpoint
            and self.vad_settings.use_stt_endpoint
            and session.vad.speech_started
            and self._has_command_words(update.final_text)
        ):
            end_reason = "stt_endpoint"
        if end_reason:
            self._finish(end_reason, events)

    def _has_command_words(self, text: str | None) -> bool:
        if not text:
            return False
        _, rest = split_wakeword(normalize_text(text), self.wakewords)
        return bool(rest)

    # --- session end --------------------------------------------------------------------------

    def _finish(self, end_reason: str, events: list[PipelineEvent]) -> None:
        session = self._session
        assert session is not None
        ended_at = time.monotonic()
        if session.wake_pending and self.detector is not None:
            if not self._handle_wake_events(self.detector.flush(), events):
                return
            if self._session is not None and self._session.wake_pending:
                self._session.wake_pending = False
        stt_started = time.monotonic()
        transcript = session.transcriber.finalize()
        pcm = b"".join(session.frames)
        if self.refiner is not None and transcript.text:
            transcript = self.refiner.refine(pcm, transcript)
        stt_latency_ms = int((time.monotonic() - stt_started) * 1000)
        levels = session.speech_levels or session.all_levels
        outcome = CommandOutcome(
            session_id=session.session_id,
            wakeword=session.wakeword,
            wakeword_confidence=session.wake_confidence,
            wakeword_latency_ms=session.wake_latency_ms,
            transcript=transcript,
            stt_latency_ms=stt_latency_ms,
            end_reason=end_reason,
            ended_at=ended_at,
            audio_level=round(sum(levels) / len(levels), 1) if levels else None,
            pcm=pcm,
        )
        events.append(PipelineEvent("speech_end", {"session_id": session.session_id, "reason": end_reason}))
        events.append(PipelineEvent("session_end", {"session_id": session.session_id}, outcome))
        self._end_session()

    def _end_session(self) -> None:
        self._session = None
        self._candidate_at = None
        self._cooldown_until_ms = self._audio_ms + self.cooldown_ms
        self._ring.clear()
        if self.detector is not None:
            self.detector.reset()
        self._detector_started_ms = self._audio_ms

    def flush(self) -> list[PipelineEvent]:
        """End of stream: finish an open session or resolve a final-only wakeword hit."""
        events: list[PipelineEvent] = []
        if self._session is not None:
            self._finish("end_of_audio", events)
            return events
        if self.detector is not None:
            for event in self.detector.flush():
                if event.kind is WakewordEventKind.CONFIRMED:
                    self._open_session(event, time.monotonic(), events)
                    self._confirm(event, events)
                    if self._session is not None:
                        self._finish("end_of_audio", events)
                    break
        return events

    def transcribe_direct(self, pcm_frames: Sequence[bytes]) -> CommandOutcome:
        """Transcribe audio without a wakeword session (audio tests with require_wakeword=False)."""
        transcriber = self.provider.create_transcriber(self.grammar)
        for frame in pcm_frames:
            transcriber.accept(frame)
        ended_at = time.monotonic()
        transcript = transcriber.finalize()
        pcm = b"".join(pcm_frames)
        if self.refiner is not None and transcript.text:
            transcript = self.refiner.refine(pcm, transcript)
        levels = [rms_dbfs(f) for f in pcm_frames]
        speech = [lv for lv in levels if lv >= self._background_vad.threshold_dbfs] or levels
        return CommandOutcome(
            session_id=uuid.uuid4().hex[:12],
            wakeword=None,
            wakeword_confidence=None,
            wakeword_latency_ms=None,
            transcript=transcript,
            stt_latency_ms=int((time.monotonic() - ended_at) * 1000),
            end_reason="end_of_audio",
            ended_at=ended_at,
            audio_level=round(sum(speech) / len(speech), 1) if speech else None,
            pcm=pcm,
        )
