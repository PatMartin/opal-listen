"""listener_service: owns configuration snapshots, the provider, the live audio pipeline and results.

Everything speech-engine specific goes through the ListenerProvider abstraction.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from opal_listener.app_settings import AppSettings
from opal_listener.data.db import Database
from opal_listener.models import schemas as s
from opal_listener.models.config_schemas import ConfigBundle
from opal_listener.models.tables import Intent, Profile, Setting, Wakeword, utc_now
from opal_listener.service import backup as backup_ops
from opal_listener.service import recognition_store
from opal_listener.service.audio.capture import AudioCaptureError, AudioSource, MicrophoneSource
from opal_listener.service.audio.pcm import iter_frames, load_wav_bytes
from opal_listener.service.config_store import export_bundle, load_config_dir, replace_config
from opal_listener.service.errors import InvalidRequestError, ServiceUnavailableError
from opal_listener.service.events import EventBus
from opal_listener.service.http_events import HttpEventPublisher
from opal_listener.service.nlu.normalizer import normalize_text, split_wakeword
from opal_listener.service.pipeline import CommandOutcome, PipelineEvent, RecognitionPipeline
from opal_listener.service.providers.base import ListenerProvider, ProviderError, SttRefiner
from opal_listener.service.providers.registry import create_provider, create_refiner
from opal_listener.service.runtime_config import ConfigInvalidError, RuntimeSnapshot, compile_bundle
from opal_listener.service.settings_registry import SETTINGS, to_storage
from opal_listener.service.zmq_events import EventPublisher, ZmqEventPublisher

log = logging.getLogger(__name__)

# Published whenever a live-microphone recognition matches an intent (see ListenerService._record).
INTENT_MATCH_TOPIC = "opal.listen.intent.match"

# Diagnostic sources (the test_text / test_audio endpoints): results are logged and streamed to
# local subscribers but are never published as external intent-match events.
DIAGNOSTIC_SOURCES = ("text", "audio")

SourceFactory = Callable[[dict[str, Any]], AudioSource]


def default_source_factory(settings: dict[str, Any]) -> AudioSource:
    return MicrophoneSource(
        device=str(settings["audio_device"]),
        frame_ms=int(settings["audio_block_ms"]),
        capture_rate=int(settings["audio_capture_rate"]),
    )


def default_event_publisher(settings: AppSettings) -> EventPublisher:
    """Builds the event publisher for the configured transport (http or zmq).

    'both' is rejected: it would deliver the same actionable event twice — once via REST and once
    via ZeroMQ — and opal-event does not deduplicate across transports, so every matched intent
    could execute an action twice."""
    transport = (settings.event_transport or "http").lower()
    if transport == "both":
        raise ValueError(
            "event_transport='both' is not supported: opal-event does not deduplicate events across "
            "transports, so each match would be delivered (and its action executed) twice. "
            "Use 'http' or 'zmq'."
        )
    if transport == "zmq":
        return ZmqEventPublisher(settings.xpub_url)
    return HttpEventPublisher(base_url=settings.opal_event_url, token=settings.opal_event_token)


@dataclass
class _RuntimeStats:
    state: str = "stopped"
    started_at: datetime | None = None
    started_monotonic: float | None = None
    frames_processed: int = 0
    wakeword_detections: int = 0
    sessions: int = 0
    results: int = 0
    audio_restarts: int = 0
    last_audio_level: float | None = None
    last_result_at: datetime | None = None
    last_error: str | None = None
    audio_device: str | None = None
    capture_sample_rate: int | None = None


class ListenerService:
    def __init__(
        self,
        db: Database,
        app_settings: AppSettings,
        provider_factory: Callable[[str], ListenerProvider] = create_provider,
        source_factory: SourceFactory = default_source_factory,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        self.db = db
        self.app_settings = app_settings
        self.bus = EventBus()
        self.event_publisher: EventPublisher = event_publisher or default_event_publisher(app_settings)
        self._provider_factory = provider_factory
        self._source_factory = source_factory
        self._config_lock = threading.RLock()
        self._provider_lock = threading.Lock()
        self._snapshot: RuntimeSnapshot | None = None
        self._version = 0
        self._providers: dict[str, ListenerProvider] = {}
        self._refiners: dict[str, SttRefiner] = {}
        self._stats = _RuntimeStats()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._dedup_seen: dict[tuple[object, ...], float] = {}
        self._results_since_prune = 0

    # --- lifecycle ----------------------------------------------------------------------------

    def startup(self) -> None:
        self.app_settings.ensure_dirs()
        self.db.migrate()
        self._seed_if_empty()
        self._ensure_default_settings()
        try:
            self.reload()
        except ConfigInvalidError as exc:
            log.error("stored configuration is invalid: %s", exc)
        if self._snapshot and self._snapshot.settings.get("auto_start_listener"):
            self.start()

    def shutdown(self) -> None:
        self.stop()
        self.event_publisher.close()

    def _seed_if_empty(self) -> None:
        if not self.app_settings.seed_on_empty:
            return
        with self.db.session() as session:
            empty = not any(session.exec(select(model.id).limit(1)).first() for model in (Wakeword, Intent, Profile))
        if not empty:
            return
        try:
            bundle = load_config_dir(self.app_settings.config_dir)
        except ValueError as exc:
            log.error("cannot seed from %s: %s", self.app_settings.config_dir, exc)
            return
        report, _ = compile_bundle(bundle)
        if not report.ok:
            log.error("seed config invalid: %s", "; ".join(report.errors))
            return
        with self.db.session() as session:
            replace_config(session, bundle)
            session.commit()
        log.info("seeded configuration from %s", self.app_settings.config_dir)

    def _ensure_default_settings(self) -> None:
        with self.db.session() as session:
            existing = set(session.exec(select(Setting.key)).all())
            for key, definition in SETTINGS.items():
                if key not in existing:
                    session.add(Setting(key=key, value=to_storage(definition.default_value())))
            session.commit()

    # --- configuration ------------------------------------------------------------------------

    @property
    def snapshot(self) -> RuntimeSnapshot:
        if self._snapshot is None:
            raise ServiceUnavailableError("configuration is not loaded (see /api/v1/health/)")
        return self._snapshot

    def reload(self) -> RuntimeSnapshot:
        with self._config_lock, self.db.session() as session:
            report, snapshot = compile_bundle(export_bundle(session), self._version + 1)
            if snapshot is None:
                raise ConfigInvalidError(report)
            self._apply(snapshot)
            return snapshot

    def _apply(self, snapshot: RuntimeSnapshot) -> None:
        self._version = snapshot.version
        self._snapshot = snapshot
        if snapshot.settings.get("event_publication_enabled"):
            # Connect eagerly so the handshake settles before the first real intent match; every
            # publisher connect() is idempotent, and publish() would connect lazily anyway.
            self.event_publisher.connect()

    def _check_provider_change(self, new: RuntimeSnapshot) -> None:
        """Validate provider/model changes before committing (cheap checks, no model load)."""
        old = self._snapshot
        if old and (old.provider_name, old.provider_config.model_paths) == (
            new.provider_name,
            new.provider_config.model_paths,
        ):
            return
        try:
            provider = self._get_provider(new.provider_name)
        except ProviderError as exc:
            raise InvalidRequestError(str(exc)) from exc
        failures = [c for c in provider.check(new.provider_config) if not c.ok and c.severity == "error"]
        if failures:
            raise InvalidRequestError(f"provider check failed: {failures[0].detail}")

    @contextlib.contextmanager
    def config_change(self) -> Iterator[Session]:
        """Transaction that is committed only if the resulting configuration compiles.

        On any failure the transaction is rolled back and the running snapshot is untouched.
        """
        with self._config_lock, self.db.session() as session:
            try:
                yield session
                session.flush()
                session.expire_all()
                report, snapshot = compile_bundle(export_bundle(session), self._version + 1)
                if snapshot is None:
                    raise ConfigInvalidError(report)
                self._check_provider_change(snapshot)
                session.commit()
            except BaseException:
                session.rollback()
                raise
            self._apply(snapshot)

    def validate_bundle(self, bundle: ConfigBundle) -> s.ConfigValidationReport:
        report, _ = compile_bundle(bundle)
        return s.ConfigValidationReport(valid=report.ok, errors=report.errors, warnings=report.warnings)

    def import_bundle(self, bundle: ConfigBundle) -> s.ConfigImportResult:
        """Validate, back up, then replace configuration in one transaction (rolled back on failure)."""
        report, _ = compile_bundle(bundle)
        if not report.ok:
            raise ConfigInvalidError(report)
        with self._config_lock:
            backup_name = self.create_backup("pre_import")
            with self.config_change() as session:
                counts = replace_config(session, bundle)
        return s.ConfigImportResult(
            status="imported", backup_filename=backup_name, counts=counts, warnings=report.warnings
        )

    # --- backups ------------------------------------------------------------------------------

    def create_backup(self, label: str = "manual") -> str:
        return backup_ops.create_backup(self.db.path, self.app_settings.backup_dir, label)

    def _restore_file(self, filename: str) -> None:
        path = backup_ops.safe_backup_path(self.app_settings.backup_dir, filename)
        backup_ops.verify_backup(path)
        self.db.dispose()
        backup_ops.copy_database(path, self.db.path)
        self.db.dispose()
        self.db.migrate()
        self.reload()

    def restore_backup(self, filename: str) -> dict[str, str]:
        path = backup_ops.safe_backup_path(self.app_settings.backup_dir, filename)
        backup_ops.verify_backup(path)
        with self._config_lock:
            safety = self.create_backup("pre_restore")
            try:
                self._restore_file(filename)
            except Exception as exc:
                log.exception("restore of %s failed; rolling back to %s", filename, safety)
                self._restore_file(safety)
                if isinstance(exc, ConfigInvalidError):
                    raise InvalidRequestError(f"backup contains invalid configuration: {exc}") from exc
                raise
        return {"status": "restored", "filename": filename, "rollback_backup": safety}

    # --- providers ----------------------------------------------------------------------------

    def _get_provider(self, name: str) -> ListenerProvider:
        with self._provider_lock:
            provider = self._providers.get(name)
            if provider is None:
                provider = self._provider_factory(name)
                self._providers[name] = provider
            return provider

    def provider_for(self, snapshot: RuntimeSnapshot) -> ListenerProvider:
        provider = self._get_provider(snapshot.provider_name)
        provider.load(snapshot.provider_config)
        return provider

    def provider_status(self) -> tuple[ListenerProvider | None, RuntimeSnapshot | None]:
        snap = self._snapshot
        if snap is None:
            return None, None
        try:
            return self._get_provider(snap.provider_name), snap
        except ProviderError:
            return None, snap

    def _refiner_for(self, snapshot: RuntimeSnapshot) -> SttRefiner | None:
        name = snapshot.refiner_name
        if name in ("", "none"):
            return None
        refiner = self._refiners.get(name)
        if refiner is None:
            refiner = create_refiner(name)
            if refiner is None:
                return None
            self._refiners[name] = refiner
        refiner.load(snapshot.provider_config)
        return refiner

    def _make_pipeline(self, snapshot: RuntimeSnapshot, debug: bool, cooldown: bool) -> RecognitionPipeline:
        return RecognitionPipeline(
            provider=self.provider_for(snapshot),
            wakewords=snapshot.wakewords,
            wakeword_thresholds=snapshot.wakeword_thresholds,
            grammar=snapshot.grammar,
            vad_settings=snapshot.vad,
            frame_ms=int(snapshot.settings["audio_block_ms"]),
            cooldown_ms=int(snapshot.settings["cooldown_ms"]) if cooldown else 0,
            refiner=self._refiner_for(snapshot),
            debug=debug,
        )

    # --- recognition --------------------------------------------------------------------------

    def interpret(
        self,
        raw_text: str,
        snapshot: RuntimeSnapshot,
        *,
        source: str,
        session_id: str | None = None,
        outcome: CommandOutcome | None = None,
    ) -> s.RecognitionResult:
        started = time.perf_counter()
        spoken = normalize_text(raw_text)
        wakeword, command = split_wakeword(spoken, snapshot.wakewords)
        normalized = normalize_text(command, snapshot.filler_words)
        match = snapshot.engine.match(normalized)
        intent_latency_ms = int((time.perf_counter() - started) * 1000)

        matched, reason = match.matched, match.no_match_reason
        stt_confidence = outcome.transcript.confidence if outcome else None
        if outcome and not normalized and outcome.end_reason == "no_speech":
            reason = "no_speech"
        if matched and stt_confidence is not None and stt_confidence < snapshot.stt_threshold:
            matched, reason = False, "low_stt_confidence"

        wakeword_confidence = None
        if outcome and outcome.wakeword:
            wakeword, wakeword_confidence = outcome.wakeword, outcome.wakeword_confidence
        elif wakeword and not outcome:
            wakeword_confidence = 1.0

        total_ms = intent_latency_ms
        if outcome:
            total_ms = int((time.monotonic() - outcome.ended_at) * 1000)
        return s.RecognitionResult(
            session_id=session_id or (outcome.session_id if outcome else uuid.uuid4().hex[:12]),
            source=source,  # type: ignore[arg-type]
            profile_name=snapshot.profile_name,
            wakeword=wakeword,
            transcript=command or None,
            normalized_transcript=normalized or None,
            intent_name=match.intent_name if matched else s.NO_MATCH,
            slot_values=match.slot_values if matched else {},
            wakeword_confidence=wakeword_confidence,
            stt_confidence=stt_confidence,
            intent_confidence=match.confidence,
            matched=matched,
            no_match_reason=None if matched else reason,
            matched_phrase=match.matched_phrase if matched else None,
            alternatives=[s.IntentAlternative(**a.__dict__) for a in match.alternatives],
            wakeword_latency_ms=outcome.wakeword_latency_ms if outcome else None,
            stt_latency_ms=outcome.stt_latency_ms if outcome else None,
            intent_latency_ms=intent_latency_ms,
            total_latency_ms=total_ms,
            audio_level=outcome.audio_level if outcome else None,
            created_at=utc_now(),
        )

    def _record(self, result: s.RecognitionResult, save_log: bool) -> s.RecognitionResult:
        snapshot = self.snapshot
        if result.source == "mic" and result.matched:
            key = (
                result.source,
                result.profile_name,
                result.intent_name,
                tuple(sorted(result.slot_values.items())),
            )
            now = time.monotonic()
            window = int(snapshot.settings["dedup_window_ms"]) / 1000
            # evict expired entries so a long-running listener cannot grow this map without bound
            self._dedup_seen = {k: ts for k, ts in self._dedup_seen.items() if now - ts < window}
            if key in self._dedup_seen:
                result.deduplicated = True
            else:
                self._dedup_seen[key] = now
        if save_log:
            capture = bool(snapshot.settings["auto_capture_failures"]) and result.source != "text"
            with self.db.session() as session:
                result.recognition_log_id = recognition_store.save_result(session, result, capture)
                self._results_since_prune += 1
                if self._results_since_prune >= 100:
                    self._results_since_prune = 0
                    recognition_store.prune_logs(session, int(snapshot.settings["max_recognition_logs"]))
                session.commit()
        payload = result.model_dump(mode="json")

        self.bus.publish("result", payload)
        if (
            result.matched
            and not result.deduplicated
            and result.source not in DIAGNOSTIC_SOURCES
            and snapshot.settings["event_publication_enabled"]
        ):
            log.info("publishing event to topic '%s': %s", INTENT_MATCH_TOPIC, payload)
            self.event_publisher.publish(INTENT_MATCH_TOPIC, payload)
        return result

    def test_text(self, text: str, save_log: bool = True) -> s.RecognitionResult:
        return self._record(self.interpret(text, self.snapshot, source="text"), save_log)

    def interpret_text(self, text: str) -> s.RecognitionResult:
        """Pure intent evaluation (no logging) used by regression runs."""
        return self.interpret(text, self.snapshot, source="text")

    def test_audio(self, data: bytes, save_log: bool = True, require_wakeword: bool = False) -> s.RecognitionResult:
        if len(data) > self.app_settings.max_upload_bytes:
            raise InvalidRequestError(f"audio file exceeds {self.app_settings.max_upload_bytes} bytes")
        try:
            pcm = load_wav_bytes(data)
        except ValueError as exc:
            raise InvalidRequestError(str(exc)) from exc
        snapshot = self.snapshot
        try:
            pipeline = self._make_pipeline(snapshot, debug=False, cooldown=False)
        except ProviderError as exc:
            raise ServiceUnavailableError(f"listener provider unavailable: {exc}") from exc
        frames = iter_frames(pcm, pipeline.frame_ms)
        events: list[PipelineEvent] = []
        for frame in frames:
            events.extend(pipeline.feed(frame))
        events.extend(pipeline.flush())
        outcome = next((e.outcome for e in events if e.kind == "session_end" and e.outcome), None)
        if outcome is None:
            if require_wakeword:
                result = self.interpret("", snapshot, source="audio")
                result.no_match_reason = "no_wakeword"
                return self._record(result, save_log)
            outcome = pipeline.transcribe_direct(frames)
        return self._record(
            self.interpret(outcome.transcript.text, snapshot, source="audio", outcome=outcome), save_log
        )

    # --- live pipeline ------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> dict[str, str]:
        if self.running:
            return {"status": "already_running"}
        self.snapshot  # noqa: B018 - raises if configuration is not loaded
        self._stop.clear()
        self._stats = _RuntimeStats(state="starting", started_at=utc_now(), started_monotonic=time.monotonic())
        self._thread = threading.Thread(target=self._run, name="listener-runtime", daemon=True)
        self._thread.start()
        return {"status": "started"}

    def stop(self) -> dict[str, str]:
        if not self.running:
            return {"status": "not_running"}
        self._stop.set()
        assert self._thread is not None
        self._thread.join(timeout=5)
        self._thread = None
        self._stats.state = "stopped"
        self._publish_status()
        return {"status": "stopped"}

    def status(self) -> s.PipelineStatus:
        st = self._stats
        snap = self._snapshot
        return s.PipelineStatus(
            running=self.running,
            state=st.state if self.running else "stopped",
            provider=snap.provider_name if snap else "unknown",
            profile_name=snap.profile_name if snap else None,
            audio_device=st.audio_device,
            capture_sample_rate=st.capture_sample_rate,
            started_at=st.started_at if self.running else None,
            uptime_s=round(time.monotonic() - st.started_monotonic, 1)
            if self.running and st.started_monotonic
            else None,
            frames_processed=st.frames_processed,
            wakeword_detections=st.wakeword_detections,
            sessions=st.sessions,
            results=st.results,
            audio_restarts=st.audio_restarts,
            last_audio_level=st.last_audio_level,
            last_result_at=st.last_result_at,
            last_error=st.last_error,
        )

    def _publish_status(self) -> None:
        self.bus.publish("status", self.status().model_dump(mode="json"))

    def _set_state(self, state: str) -> None:
        if self._stats.state != state:
            self._stats.state = state
            self._publish_status()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            source: AudioSource | None = None
            try:
                snapshot = self.snapshot
                pipeline = self._make_pipeline(snapshot, debug=self.bus.subscriber_count > 0, cooldown=True)
                source = self._source_factory(snapshot.settings)
                source.start()
                self._stats.audio_device = source.description
                self._stats.capture_sample_rate = source.capture_rate
                self._stats.last_error = None
                self._set_state("listening")
                backoff = 1.0
                self._listen(source, pipeline, snapshot)
            except (AudioCaptureError, ProviderError, ServiceUnavailableError) as exc:
                self._recover(str(exc))
            except Exception as exc:  # noqa: BLE001 - watchdog must survive any failure
                log.exception("listener runtime crashed")
                self._recover(f"unexpected error: {exc}")
            finally:
                if source is not None:
                    source.stop()
            if not self._stop.is_set() and self._stats.state == "recovering":
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)

    def _recover(self, message: str) -> None:
        log.warning("listener runtime error: %s", message)
        self._stats.last_error = message
        self._stats.audio_restarts += 1
        self._set_state("recovering")

    def _listen(self, source: AudioSource, pipeline: RecognitionPipeline, snapshot: RuntimeSnapshot) -> None:
        watchdog_s = int(snapshot.settings["audio_watchdog_timeout_ms"]) / 1000
        last_frame = time.monotonic()
        while not self._stop.is_set():
            current = self._snapshot
            if current is not None and current.version != snapshot.version and pipeline.idle:
                snapshot = current
                pipeline = self._make_pipeline(snapshot, debug=pipeline.debug, cooldown=True)
                watchdog_s = int(snapshot.settings["audio_watchdog_timeout_ms"]) / 1000
            want_debug = self.bus.subscriber_count > 0
            if pipeline.debug != want_debug and pipeline.idle:
                pipeline.debug = want_debug
            frame = source.read(timeout=0.25)
            if frame is None:
                if time.monotonic() - last_frame > watchdog_s:
                    raise AudioCaptureError(f"no audio received for {watchdog_s:.1f}s (watchdog)")
                continue
            last_frame = time.monotonic()
            self._stats.frames_processed += 1
            events = pipeline.feed(frame.pcm, frame.captured_at)
            self._stats.last_audio_level = pipeline.last_level
            for event in events:
                self._handle_event(event, snapshot)

    def _handle_event(self, event: PipelineEvent, snapshot: RuntimeSnapshot) -> None:
        if event.kind == "wakeword_candidate":
            self._stats.sessions += 1
            self._set_state("capturing")
        elif event.kind == "wakeword_confirmed":
            self._stats.wakeword_detections += 1
        elif event.kind == "wakeword_rejected":
            self._set_state("listening")
        if event.kind == "session_end" and event.outcome is not None:
            outcome = event.outcome
            result = self.interpret(outcome.transcript.text, snapshot, source="mic", outcome=outcome)
            self._record(result, save_log=True)
            self._stats.results += 1
            self._stats.last_result_at = result.created_at
            self._set_state("listening")
            return
        debug_only = event.kind in ("audio_level", "partial")
        self.bus.publish(event.kind, event.data, debug_only=debug_only)
