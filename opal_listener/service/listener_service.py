"""listener_service: owns configuration snapshots, the provider, the live audio pipeline and results.

Everything speech-engine specific goes through the ListenerProvider abstraction.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from opal_listener.app_settings import AppSettings
from opal_listener.data.db import Database
from opal_listener.models import schemas as s
from opal_listener.models.config_schemas import ConfigBundle  # noqa: F401 - type used by reload()/compile
from opal_listener.models.tables import Intent, Profile, Setting, Wakeword, utc_now
from opal_listener.service import recognition_store
from opal_listener.service.audio.capture import AudioCaptureError, AudioSource, MicrophoneSource
from opal_listener.service.audio.pcm import iter_frames, load_wav_bytes
from opal_listener.service.config_store import export_bundle, load_config_dir, replace_config
from opal_listener.service.errors import InvalidRequestError, ServiceUnavailableError
from opal_listener.service.events import EventBus
from opal_listener.service.http_events import HttpEventPublisher
from opal_listener.service.nlu.normalizer import normalize_text, split_wakeword
from opal_listener.service.pipeline import CommandOutcome, PipelineEvent, RecognitionPipeline
from opal_listener.service.providers.base import (
    ListenerProvider,
    ProviderConfig,
    ProviderError,
    SttRefiner,
    Transcript,
)
from opal_listener.service.providers.registry import create_provider, create_refiner
from opal_listener.service.reactions import ReactionRunner
from opal_listener.service.refinement import DEFAULT_QUEUE_CAPACITY, RefinementWorker, RefineTask
from opal_listener.service.runtime_config import ConfigInvalidError, RuntimeSnapshot, compile_bundle
from opal_listener.service.settings_registry import SETTINGS, to_storage
from opal_listener.service.zmq_events import EventPublisher, ZmqEventPublisher

log = logging.getLogger(__name__)

# Published whenever a live-microphone recognition matches an intent (see ListenerService._record).
INTENT_MATCH_TOPIC = "opal.listen.intent.match"

# Diagnostic sources (the test_text / test_audio endpoints): results are logged and streamed to
# local subscribers but are never published as external intent-match events.
DIAGNOSTIC_SOURCES = ("text", "audio")

# Settings that describe the physical capture stream: changing any of them requires reopening the
# audio source, not just rebuilding the pipeline (frame duration must stay coherent end to end).
_CAPTURE_SETTING_KEYS = ("audio_device", "audio_capture_rate", "audio_block_ms")

# Dispatch targets a matched intent can take: 'local' = file-provisioned reactions.yaml
# commands, 'event' = external opal-event publication.
DISPATCH_TARGETS = ("local", "event")


def dispatch_targets(settings: Mapping[str, Any]) -> frozenset[str]:
    """Resolve the intent dispatch policy to the set of targets to invoke.

    'auto' (the default) migrates the legacy booleans transparently: reactions_enabled ->
    'local', event_publication_enabled -> 'event'. Any explicit intent_dispatch value
    ('off', 'local', 'event', 'both') overrides both flags."""
    mode = str(settings.get("intent_dispatch", "auto"))
    if mode == "auto":
        targets = set()
        if settings.get("reactions_enabled"):
            targets.add("local")
        if settings.get("event_publication_enabled"):
            targets.add("event")
        return frozenset(targets)
    if mode == "both":
        return frozenset(DISPATCH_TARGETS)
    if mode == "off":
        return frozenset()
    return frozenset({mode})


def _capture_signature(settings: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(settings[k] for k in _CAPTURE_SETTING_KEYS)


def _provider_fingerprint(config: ProviderConfig) -> tuple[Any, ...]:
    """Hashable identity of a provider's model configuration.

    Providers are cached per (name, fingerprint): an unchanged fingerprint reuses the live
    instance (no reload), a changed one creates a fresh instance so in-flight sessions keep
    running on the configuration they started with."""
    return (
        config.sample_rate,
        tuple(sorted(config.model_paths.items())),
        tuple(sorted((k, repr(v)) for k, v in config.options.items())),
    )


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


class _DispatchSequencer:
    """Orders external dispatch (event publish, reactions) by capture sequence.

    Mic results finalize on two different threads — the capture thread (no refiner, or a
    full refine queue) and the refinement worker — so a later command can finalize first.
    External side effects are therefore gated through capture-order tickets: a result's
    dispatch runs only after every earlier-captured command has finalized. Recording,
    streaming and dedup marking stay immediate; only dispatch waits its turn.

    Tickets are monotonic per service. `reset()` on pipeline start re-bases the gate so a
    late finalization from a previous run (already dispatch-blocked by its generation
    stamp) can never gate or fire behind a newer run's commands.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0
        self._expected = 1
        self._pending: dict[int, Callable[[], None]] = {}

    def assign(self) -> int:
        with self._lock:
            self._next += 1
            return self._next

    def reset(self) -> None:
        with self._lock:
            self._expected = self._next + 1
            self._pending.clear()

    def complete(self, seq: int, fn: Callable[[], None]) -> None:
        """Release `fn` (dispatch) when every earlier seq has completed; drop stale tickets."""
        with self._lock:
            if seq < self._expected:
                return  # superseded generation or already released
            self._pending[seq] = fn
            ready = []
            while self._expected in self._pending:
                ready.append(self._pending.pop(self._expected))
                self._expected += 1
        for fn_ in ready:
            fn_()


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
    last_recovery_reason: str | None = None
    audio_device: str | None = None
    capture_sample_rate: int | None = None
    # capture signature (device, rate, block_ms) the running source was opened with; differs
    # from the latest snapshot's signature while a capture-setting change is unapplied
    capture_signature: tuple[Any, ...] | None = None
    # per-stage delays of the last finalized result (ms) and capture/reliability counters
    stt_latency_ms: float | None = None
    refine_latency_ms: float | None = None
    intent_latency_ms: float | None = None
    publish_latency_ms: float | None = None
    mic_overflows: int = 0  # overflow reports accumulated from retired sources
    dropped_audio_blocks: int = 0  # full-queue drops accumulated from retired sources
    dispatched_local: int = 0  # matched results dispatched to local reactions this run
    dispatched_event: int = 0  # matched results published to the event bus this run
    dispatch_suppressed: int = 0  # actionable results with no target selected (policy 'off')


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
        # name -> (config fingerprint, instance); at most one loaded instance per name — a replaced
        # entry is GC'd once the last in-flight pipeline drops its reference
        self._providers: dict[str, tuple[tuple[Any, ...], ListenerProvider]] = {}
        self._refiners: dict[str, tuple[tuple[Any, ...], SttRefiner]] = {}
        self._stats = _RuntimeStats()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._shutdown = False
        # restart-only setting values as captured at startup (pending_restart provenance)
        self._startup_setting_values: dict[str, Any] = {}
        self._dedup_seen: dict[tuple[object, ...], float] = {}
        self._dedup_lock = threading.Lock()
        # run identity: bumped on every start(); outcomes carry the generation that captured
        # them so a late finalization (e.g. deferred refinement) cannot dispatch into a new run
        self._generation = 0
        self._active_source: AudioSource | None = None
        self._sequencer = _DispatchSequencer()
        self._refine_worker = RefinementWorker(
            capacity=DEFAULT_QUEUE_CAPACITY, refiner_for=self._refiner_for, finalize=self._finalize_result
        )
        # File-provisioned intent reactions (config_dir/reactions.yaml). Commands live only in
        # that file — never in the DB — so the REST API cannot write executable content (RCE
        # boundary). Executions append to data_dir/reaction_log.jsonl.
        self._reaction_runner = ReactionRunner(
            config_path=app_settings.config_dir / "reactions.yaml",
            log_path=app_settings.data_dir / "reaction_log.jsonl",
            log_max_bytes=app_settings.reaction_log_max_bytes,
        )
        self._reaction_runner.start()
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
        if self._snapshot:
            with self.db.session() as session:
                self._prune_storage(session, self._snapshot.settings)
                session.commit()
        if self._snapshot:
            self._startup_setting_values = {
                k: v for k, v in self._snapshot.settings.items() if SETTINGS[k].restart_required
            }
        if self._snapshot and self._snapshot.settings.get("auto_start_listener"):
            self.start()

    def setting_pending_restart(self, key: str, configured: Any) -> bool:
        """True when a restart-only setting's desired value diverges from the startup value."""
        if not SETTINGS[key].restart_required:
            return False
        running = self._startup_setting_values.get(key, SETTINGS[key].default_value())
        return configured != running

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown:
                return
            self._shutdown = True
        self.stop()
        self._refine_worker.close()  # drain pending refinements before the publisher closes
        self._reaction_runner.close()
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
            provider = self._provider_factory(new.provider_name)
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

    def config_etag(self) -> str:
        """Strong live-configuration revision (snapshot scope), the If-Match for config load."""
        from opal_listener.service.snapshots import config_revision

        with self.db.session() as session:
            return config_revision(session)

    # --- providers ----------------------------------------------------------------------------

    def provider_for(self, snapshot: RuntimeSnapshot) -> ListenerProvider:
        """Returns the loaded provider for snapshot's configuration.

        An unchanged config fingerprint reuses the live instance without reloading. A changed one
        creates and loads a fresh instance under the lock — the previous instance is dropped from
        the cache (never mutated or unloaded while in-flight sessions still reference it)."""
        fingerprint = _provider_fingerprint(snapshot.provider_config)
        with self._provider_lock:
            cached = self._providers.get(snapshot.provider_name)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
            provider = self._provider_factory(snapshot.provider_name)
            provider.load(snapshot.provider_config)
            self._providers[snapshot.provider_name] = (fingerprint, provider)
            return provider

    def provider_status(self) -> tuple[ListenerProvider | None, RuntimeSnapshot | None]:
        """Inspection-only: returns the active provider (or a transient instance for check())
        without loading models or mutating recognition state."""
        snap = self._snapshot
        if snap is None:
            return None, None
        try:
            with self._provider_lock:
                cached = self._providers.get(snap.provider_name)
            provider = cached[1] if cached else self._provider_factory(snap.provider_name)
            return provider, snap
        except ProviderError:
            return None, snap

    def _refiner_for(self, snapshot: RuntimeSnapshot) -> SttRefiner | None:
        name = snapshot.refiner_name
        if name in ("", "none"):
            return None
        fingerprint = _provider_fingerprint(snapshot.provider_config)
        with self._provider_lock:
            cached = self._refiners.get(name)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
            refiner = create_refiner(name)
            if refiner is None:
                return None
            refiner.load(snapshot.provider_config)
            self._refiners[name] = (fingerprint, refiner)
            return refiner

    def _make_pipeline(
        self, snapshot: RuntimeSnapshot, debug: bool, cooldown: bool, *, live: bool = False
    ) -> RecognitionPipeline:
        return RecognitionPipeline(
            provider=self.provider_for(snapshot),
            wakewords=snapshot.wakewords,
            wakeword_thresholds=snapshot.wakeword_thresholds,
            grammar=snapshot.grammar,
            vad_settings=snapshot.vad,
            frame_ms=int(snapshot.settings["audio_block_ms"]),
            cooldown_ms=int(snapshot.settings["cooldown_ms"]) if cooldown else 0,
            # live pipelines never refine in-loop: refinement runs on the dedicated worker
            refiner=None if live else self._refiner_for(snapshot),
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

    def _record(
        self,
        result: s.RecognitionResult,
        save_log: bool,
        *,
        allow_dispatch: bool = True,
        gate: Callable[[Callable[[], None]], None] | None = None,
    ) -> s.RecognitionResult:
        """Log, stream, and (when allowed) dispatch a recognition result.

        ``allow_dispatch`` is False for results captured under an older run generation: they
        are still logged and streamed locally but can never publish events or fire reactions.
        ``gate`` defers the external side effects (event publish, reactions) until every
        earlier-captured command has finalized — see _DispatchSequencer; logging, dedup
        marking and the local result stream are not gated.
        for side effects regardless of which config version captured the utterance."""
        snapshot = self.snapshot
        if result.source == "mic" and result.matched and allow_dispatch:
            key = (
                result.source,
                result.profile_name,
                result.intent_name,
                tuple(sorted(result.slot_values.items())),
            )
            now = time.monotonic()
            window = int(snapshot.settings["dedup_window_ms"]) / 1000
            with self._dedup_lock:  # _record may run on the refinement worker thread
                # evict expired entries so a long-running listener cannot grow this map without bound
                self._dedup_seen = {k: ts for k, ts in self._dedup_seen.items() if now - ts < window}
                if key in self._dedup_seen:
                    result.deduplicated = True
                else:
                    self._dedup_seen[key] = now
        if save_log:
            capture = bool(snapshot.settings["auto_capture_failures"]) and result.source != "text"
            with self.db.session() as session:
                saved = recognition_store.save_result(session, result, capture)
                result.recognition_log_id = str(saved) if saved is not None else None
                self._results_since_prune += 1
                if self._results_since_prune >= 100:
                    self._results_since_prune = 0
                    self._prune_storage(session, snapshot.settings)
                session.commit()
        actionable = (
            result.matched and not result.deduplicated and result.source not in DIAGNOSTIC_SOURCES and allow_dispatch
        )
        targets = dispatch_targets(snapshot.settings) if actionable else frozenset()
        # the resolved path is recorded on the result before dispatch so the local stream,
        # the event payload and the reaction payload all carry the same selection
        result.dispatched_to = sorted(targets)

        def _dispatch() -> None:
            if "event" in targets:
                publish_start = time.monotonic()
                self.event_publisher.publish(INTENT_MATCH_TOPIC, result.model_dump(mode="json"))
                self._stats.publish_latency_ms = round((time.monotonic() - publish_start) * 1000, 2)
                self._stats.dispatched_event += 1
            if "local" in targets:
                self._reaction_runner.dispatch(result.model_dump(mode="json"))
                self._stats.dispatched_local += 1
            if actionable and not targets:
                self._stats.dispatch_suppressed += 1

        if gate is not None:
            gate(_dispatch)  # released in capture order via _DispatchSequencer
        else:
            _dispatch()
        self.bus.publish("result", result.model_dump(mode="json"))
        return result

    def _prune_storage(self, session: Session, settings: dict[str, Any]) -> None:
        """Retention cycle: bound log rows, expire transcript text and stale unreviewed
        failures. Runs at startup and every 100 recorded results; the settings in effect at
        prune time govern all existing rows (retroactive by age, not by policy-at-write)."""
        recognition_store.prune_logs(session, int(settings["max_recognition_logs"]))
        recognition_store.prune_transcripts(session, int(settings["transcript_retention_days"]))
        recognition_store.prune_failed_utterances(session, int(settings["unreviewed_failure_ttl_days"]))

    def test_text(self, text: str, save_log: bool = True) -> s.RecognitionResult:
        return self._record(self.interpret(text, self.snapshot, source="text"), save_log)

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
        with self._lifecycle_lock:
            if self._thread is not None:
                if self._thread.is_alive():
                    if not self._stop.is_set():
                        return {"status": "already_running"}
                    # a previous worker is still unwinding; wait for it before restarting so two
                    # workers are never alive at once
                    self._thread.join(timeout=5)
                    if self._thread.is_alive():
                        return {"status": "stopping"}
                # worker confirmed terminated — only now is the reference cleared
                self._thread = None
            self.snapshot  # noqa: B018 - raises if configuration is not loaded
            self._stop.clear()
            self._generation += 1
            with self._dedup_lock:
                self._dedup_seen.clear()  # dedup state belongs to the run that collected it
            self._sequencer.reset()  # stale-run dispatches can never gate a new generation
            self._stats = _RuntimeStats(state="starting", started_at=utc_now(), started_monotonic=time.monotonic())
            self._thread = threading.Thread(target=self._run, name="listener-runtime", daemon=True)
            self._thread.start()
            return {"status": "started"}

    def stop(self) -> dict[str, str]:
        with self._lifecycle_lock:
            if self._thread is None:
                return {"status": "not_running"}
            thread = self._thread
            self._stop.set()
            if thread.is_alive():
                self._set_state("stopping")
            thread.join(timeout=5)
            if thread.is_alive():
                # still unwinding: keep the reference and do not report "stopped"
                return {"status": "stopping"}
            self._thread = None
            self._stats.state = "stopped"
            self._publish_status()
            return {"status": "stopped"}

    # internal run-state vocabulary -> contract PipelineStatus.state enum; "recovering"
    # surfaces as itself (transient, self-healing) rather than collapsing into "error",
    # which is reserved for a worker that terminated without a stop request.
    _STATUS_STATES = {"listening": "running", "capturing": "running"}

    def status(self) -> s.PipelineStatus:
        st = self._stats
        snap = self._snapshot
        if self.running:
            state = self._STATUS_STATES.get(st.state, st.state)
        elif st.state == "recovering" and not self._stop.is_set():
            state = "error"  # worker died mid-recovery without a stop request
        else:
            state = "stopped"
        return s.PipelineStatus(
            running=self.running,
            state=state,
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
            metrics=self._metrics(),
        )

    def _metrics(self) -> dict[str, float]:
        """Lightweight reliability counters. All values reset per run (stats reset on start);
        retired-source counters accumulate into _stats so source swaps never lose them."""
        st = self._stats
        source = self._active_source
        capacity = float(getattr(source, "queue_capacity", 0) or 0)
        depth = float(getattr(source, "queue_depth", 0) or 0)
        latest = self._snapshot
        capture_pending = (
            self.running
            and latest is not None
            and st.capture_signature is not None
            and _capture_signature(latest.settings) != st.capture_signature
        )
        return {
            "mic_overflows": st.mic_overflows + getattr(source, "overflows", 0),
            "dropped_audio_blocks": st.dropped_audio_blocks + getattr(source, "dropped_blocks", 0),
            "audio_queue_depth": depth,
            "audio_queue_utilization": round(depth / capacity, 3) if capacity else 0.0,
            # requested (latest snapshot) vs active capture settings: pending=1 while a
            # capture-setting change is committed but not applied (e.g. a rejected reopen);
            # the rejected device name rides along in last_error
            "requested_capture_rate": float(latest.settings["audio_capture_rate"]) if latest else 0.0,
            "requested_audio_block_ms": float(latest.settings["audio_block_ms"]) if latest else 0.0,
            "capture_settings_pending": 1.0 if capture_pending else 0.0,
            "refine_queue_depth": float(self._refine_worker.queue_depth),
            "refine_unresolved": float(self._refine_worker.unresolved),
            "refinement_completed": float(self._refine_worker.refined),
            "refinement_failed": float(self._refine_worker.failed),
            "refinement_saturated": float(self._refine_worker.saturated),
            "dispatched_local": float(st.dispatched_local),
            "dispatched_event": float(st.dispatched_event),
            "dispatch_suppressed": float(st.dispatch_suppressed),
            # delivery accounting from the configured publisher: events_accepted (queue/send
            # accepted), events_delivered (HTTP = 2xx confirmed; ZMQ = socket handed the
            # frames, subscriber receipt unverifiable), event_delivery_failures
            **getattr(self.event_publisher, "stats", dict)(),
            "recoveries": float(st.audio_restarts),
            "stt_latency_ms": st.stt_latency_ms or 0.0,
            "refine_latency_ms": st.refine_latency_ms or 0.0,
            "intent_latency_ms": st.intent_latency_ms or 0.0,
            "publish_latency_ms": st.publish_latency_ms or 0.0,
            **self._reaction_runner.stats(),
        }

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
                pipeline = self._make_pipeline(snapshot, debug=self.bus.subscriber_count > 0, cooldown=True, live=True)
                source = self._source_factory(snapshot.settings)
                source.start()
                self._active_source = source
                self._stats.audio_device = source.description
                self._stats.capture_sample_rate = source.capture_rate
                self._stats.capture_signature = _capture_signature(snapshot.settings)
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
                    self._stats.mic_overflows += getattr(source, "overflows", 0)
                    self._stats.dropped_audio_blocks += getattr(source, "dropped_blocks", 0)
                    if self._active_source is source:
                        self._active_source = None
            if not self._stop.is_set() and self._stats.state == "recovering":
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 30.0)

    def _recover(self, message: str) -> None:
        log.warning("listener runtime error: %s", message)
        self._stats.last_error = message
        self._stats.last_recovery_reason = message
        self._stats.audio_restarts += 1
        self._set_state("recovering")

    def _reopen_source(self, source: AudioSource, snapshot: RuntimeSnapshot) -> AudioSource:
        """Replaces a live capture stream after a capture-setting change.

        The old stream is closed before the new one opens so captures never overlap. If the new
        source cannot start, the last-known-good stream is restarted when possible and an
        AudioCaptureError is raised so the caller can keep the previous configuration."""
        source.stop()
        self._stats.mic_overflows += getattr(source, "overflows", 0)
        self._stats.dropped_audio_blocks += getattr(source, "dropped_blocks", 0)
        try:
            new_source = self._source_factory(snapshot.settings)
            new_source.start()
        except Exception as exc:
            with contextlib.suppress(Exception):
                source.start()  # best-effort: keep the previously working stream alive
            raise AudioCaptureError(
                f"audio capture restart failed (requested device={snapshot.settings['audio_device']!r} "
                f"rate={snapshot.settings['audio_capture_rate']} block_ms={snapshot.settings['audio_block_ms']}"
                f"; active device={source.description!r} rate={source.capture_rate}): {exc}"
            ) from exc
        self._active_source = new_source
        self._stats.audio_device = new_source.description
        self._stats.capture_sample_rate = new_source.capture_rate
        return new_source

    def _listen(self, source: AudioSource, pipeline: RecognitionPipeline, snapshot: RuntimeSnapshot) -> None:
        watchdog_s = int(snapshot.settings["audio_watchdog_timeout_ms"]) / 1000
        last_frame = time.monotonic()
        failed_capture_version: int | None = None
        try:
            while not self._stop.is_set():
                current = self._snapshot
                if current is not None and current.version != snapshot.version and pipeline.idle:
                    # Config changes apply only at the idle session boundary. Capture-setting
                    # changes additionally require reopening the audio source; a failed reopen
                    # keeps the last-known-good stream *and* snapshot (frame duration stays
                    # coherent) and is recorded through the normal recovery accounting. A
                    # rejected version must NOT skip read(): the loop falls through so frames
                    # keep flowing on the restored source.
                    applied = False
                    if _capture_signature(snapshot.settings) == _capture_signature(current.settings):
                        snapshot, applied = current, True
                    elif current.version != failed_capture_version:
                        try:
                            source = self._reopen_source(source, current)
                        except AudioCaptureError as exc:
                            failed_capture_version = current.version
                            self._recover(str(exc))
                        else:
                            snapshot, applied = current, True
                            last_frame = time.monotonic()  # fresh stream: reset the watchdog baseline
                    if applied:
                        self._stats.capture_signature = _capture_signature(snapshot.settings)
                        pipeline = self._make_pipeline(snapshot, debug=pipeline.debug, cooldown=True, live=True)
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
        finally:
            # always stop the *current* source (it may have been swapped by _reopen_source);
            # _run's finally then closes the original a second time, which every source tolerates
            source.stop()

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
            outcome.generation = self._generation  # stamp the run that captured the command
            outcome.seq = self._sequencer.assign()  # dispatch ticket (capture order)
            self._set_state("listening")  # capture resumes immediately; finalization may be deferred
            if snapshot.refiner_name not in ("", "none") and outcome.transcript.text:
                if self._refine_worker.submit(RefineTask(outcome=outcome, snapshot=snapshot)):
                    return
                log.warning("refinement queue full; finalizing on the draft transcript")
                self._finalize_result(outcome, snapshot, None, "queue_full")
            else:
                self._finalize_result(outcome, snapshot, None, None)
            return
        debug_only = event.kind in ("audio_level", "partial")
        self.bus.publish(event.kind, event.data, debug_only=debug_only)

    def _finalize_result(
        self,
        outcome: CommandOutcome,
        snapshot: RuntimeSnapshot,
        refined: Transcript | None,
        refinement_error: str | None,
        refine_ms: float = 0.0,
    ) -> None:
        """Finalizes a captured command on the capture thread or the refinement worker: applies the
        refined (or draft) transcript, interprets with the capture-time snapshot, records, and
        updates runtime stats. Results from a superseded generation are logged but not dispatched."""
        if refined is not None:
            outcome.transcript = refined
        result = self.interpret(outcome.transcript.text, snapshot, source="mic", outcome=outcome)
        if refinement_error:
            result.refinement_error = refinement_error
        seq = outcome.seq if outcome.generation == self._generation else 0
        gate = (lambda fn: self._sequencer.complete(seq, fn)) if seq else None
        self._record(result, save_log=True, allow_dispatch=bool(seq), gate=gate)
        self._stats.results += 1
        self._stats.last_result_at = result.created_at
        self._stats.stt_latency_ms = result.stt_latency_ms
        self._stats.intent_latency_ms = result.intent_latency_ms
        self._stats.refine_latency_ms = round(refine_ms, 2) if refine_ms else None
