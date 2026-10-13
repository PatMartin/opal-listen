"""Vosk implementation of ListenerProvider (grammar-constrained wakeword spotting + command STT)."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opal_listener.service.nlu.normalizer import normalize_text
from opal_listener.service.providers.base import (
    ListenerProvider,
    ProviderCheck,
    ProviderConfig,
    ProviderError,
    Transcriber,
    TranscriberUpdate,
    Transcript,
    WakewordDetector,
    WakewordEvent,
    WakewordEventKind,
)

log = logging.getLogger(__name__)
UNK = "[unk]"


def _vosk() -> Any:
    try:
        import vosk
    except ImportError as exc:  # pragma: no cover - dependency is required
        raise ProviderError("vosk is not installed") from exc
    vosk.SetLogLevel(-1)
    return vosk


def _find_sequence(tokens: list[str], needle: list[str]) -> int:
    for i in range(len(tokens) - len(needle) + 1):
        if tokens[i : i + len(needle)] == needle:
            return i
    return -1


class VoskWakewordDetector(WakewordDetector):
    """Two grammar recognizers over the same audio.

    fast:    [wakewords, [unk]] -> early partial hit opens the session (CANDIDATE) without losing audio.
    confirm: [wakewords, distractors, [unk]] -> final result CONFIRMS (with word confidence) or REJECTS,
             so sound-alikes such as "commuter" are absorbed by a distractor instead of the wakeword.
    """

    def __init__(
        self,
        model: Any,
        wakewords: Sequence[str],
        sample_rate: int,
        trigger_on_partial: bool,
        distractors: Sequence[str] = (),
    ) -> None:
        self._vosk = _vosk()
        self._model = model
        self._rate = sample_rate
        self._trigger_on_partial = trigger_on_partial
        self._wakewords = sorted(
            ((w, normalize_text(w).split()) for w in wakewords), key=lambda x: len(x[1]), reverse=True
        )
        phrases = sorted({" ".join(t) for _, t in self._wakewords})
        extra = sorted({normalize_text(d) for d in distractors} - set(phrases) - {""})
        self._fast_grammar = json.dumps([*phrases, UNK])
        self._confirm_grammar = json.dumps([*phrases, *extra, UNK]) if extra else None
        self.reset()

    def _recognizer(self, grammar: str) -> Any:
        rec = self._vosk.KaldiRecognizer(self._model, self._rate, grammar)
        rec.SetWords(True)
        return rec

    def reset(self) -> None:
        self._fast = self._recognizer(self._fast_grammar)
        self._confirm = self._recognizer(self._confirm_grammar) if self._confirm_grammar else None
        self._candidate: str | None = None
        self._candidate_ms = 0
        self._audio_ms = 0

    def _match(self, tokens: list[str]) -> tuple[str, int] | None:
        for name, wtoks in self._wakewords:
            pos = _find_sequence(tokens, wtoks)
            if pos >= 0:
                return name, pos
        return None

    def accept(self, frame: bytes) -> list[WakewordEvent]:
        self._audio_ms += len(frame) * 1000 // (2 * self._rate)
        events: list[WakewordEvent] = []
        fast_final = self._fast.AcceptWaveform(frame)
        if self._confirm is None:
            if fast_final:
                return self._final(json.loads(self._fast.Result()))
        elif self._confirm.AcceptWaveform(frame):
            events.extend(self._final(json.loads(self._confirm.Result())))
        if self._trigger_on_partial and self._candidate is None and not events:
            partial = json.loads(self._fast.PartialResult()).get("partial", "")
            hit = self._match(partial.split())
            if hit:
                self._candidate = hit[0]
                self._candidate_ms = self._audio_ms
                events.append(WakewordEvent(WakewordEventKind.CANDIDATE, hit[0]))
        if fast_final and self._confirm is not None:
            self._fast.Result()  # discard; the confirm recognizer decides
        return events

    def flush(self) -> list[WakewordEvent]:
        rec = self._confirm if self._confirm is not None else self._fast
        events = self._final(json.loads(rec.FinalResult()), at_end=True)
        if self._candidate is not None:
            events.append(WakewordEvent(WakewordEventKind.REJECTED, self._candidate))
            self._candidate = None
        return events

    def _final(self, result: dict[str, Any], at_end: bool = False) -> list[WakewordEvent]:
        words = result.get("result") or []
        tokens = [w.get("word", "") for w in words]
        hit = self._match(tokens)
        if hit:
            name, pos = hit
            span = words[pos : pos + len(dict(self._wakewords)[name])]
            confidence = sum(float(w.get("conf", 0.0)) for w in span) / max(len(span), 1)
            end_ms = int(float(span[-1].get("end", 0.0)) * 1000) if span else None
            self._candidate = None
            return [WakewordEvent(WakewordEventKind.CONFIRMED, name, round(confidence, 4), end_ms)]
        if self._candidate is not None:
            # Only reject if this final covers the audio where the candidate was heard.
            last_end_ms = int(float(words[-1].get("end", 0.0)) * 1000) if words else 0
            if at_end or last_end_ms + 300 >= self._candidate_ms:
                rejected, self._candidate = self._candidate, None
                return [WakewordEvent(WakewordEventKind.REJECTED, rejected)]
        return []


class VoskTranscriber(Transcriber):
    def __init__(self, model: Any, grammar: Sequence[str] | None, sample_rate: int) -> None:
        vosk = _vosk()
        if grammar is not None:
            self._rec = vosk.KaldiRecognizer(model, sample_rate, json.dumps([*grammar, UNK]))
        else:
            self._rec = vosk.KaldiRecognizer(model, sample_rate)
        self._rec.SetWords(True)
        self._segments: list[dict[str, Any]] = []

    def _add(self, result: dict[str, Any]) -> str:
        text = result.get("text", "").strip()
        if text:
            self._segments.append(result)
        return text

    def accept(self, frame: bytes) -> TranscriberUpdate:
        if self._rec.AcceptWaveform(frame):
            text = self._add(json.loads(self._rec.Result()))
            meaningful = bool(normalize_text(text))
            return TranscriberUpdate(endpoint=meaningful, final_text=text or None)
        partial = json.loads(self._rec.PartialResult()).get("partial") or None
        return TranscriberUpdate(partial=partial)

    def finalize(self) -> Transcript:
        self._add(json.loads(self._rec.FinalResult()))
        text = " ".join(seg["text"] for seg in self._segments).strip()
        words = tuple(w for seg in self._segments for w in seg.get("result", []) if w.get("word") != UNK)
        confidence = round(sum(float(w.get("conf", 0.0)) for w in words) / len(words), 4) if words else None
        return Transcript(text=text, confidence=confidence, words=words)


class VoskListenerProvider(ListenerProvider):
    name = "vosk"

    def __init__(self) -> None:
        self._model: Any = None
        self._model_path: str | None = None
        self._rate = 16000
        self._trigger_on_partial = True
        self._distractors: list[str] = []
        self._lock = threading.Lock()

    @staticmethod
    def _model_path_from(config: ProviderConfig) -> str:
        path = config.model_paths.get("vosk_model", "")
        if not path:
            raise ProviderError("model_paths.vosk_model is not configured")
        return path

    def check(self, config: ProviderConfig) -> list[ProviderCheck]:
        try:
            path = Path(self._model_path_from(config))
        except ProviderError as exc:
            return [ProviderCheck("vosk_model", False, str(exc))]
        if not path.is_dir():
            return [ProviderCheck("vosk_model", False, f"model directory not found: {path}")]
        if not (path / "am").is_dir() or not (path / "conf").is_dir():
            return [ProviderCheck("vosk_model", False, f"not a Vosk model (missing am/ or conf/): {path}")]
        return [ProviderCheck("vosk_model", True, str(path))]

    def load(self, config: ProviderConfig) -> None:
        path = self._model_path_from(config)
        self._trigger_on_partial = config.options.get("wakeword_trigger", "partial") == "partial"
        self._rate = config.sample_rate
        with self._lock:
            if self._model is not None and self._model_path == path:
                self._distractors = self._known(config.options.get("wakeword_distractors", []))
                return
            failures = [c for c in self.check(config) if not c.ok]
            if failures:
                raise ProviderError(failures[0].detail)
            log.info("loading Vosk model %s", path)
            try:
                model = _vosk().Model(path)
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"failed to load Vosk model: {exc}") from exc
            self._model, self._model_path = model, path
            self._distractors = self._known(config.options.get("wakeword_distractors", []))

    def _known(self, phrases: Sequence[str]) -> list[str]:
        """Drop phrases containing words the model cannot recognize (Vosk would ignore them anyway)."""
        model = self._model
        return [p for p in phrases if p and all(model.vosk_model_find_word(w) >= 0 for w in p.split())]

    def is_loaded(self) -> bool:
        return self._model is not None

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._model_path = None

    def _require_model(self) -> Any:
        if self._model is None:
            raise ProviderError("Vosk model not loaded")
        return self._model

    def create_wakeword_detector(self, wakewords: Sequence[str]) -> WakewordDetector:
        return VoskWakewordDetector(
            self._require_model(), wakewords, self._rate, self._trigger_on_partial, self._distractors
        )

    def create_transcriber(self, grammar: Sequence[str] | None) -> Transcriber:
        self._require_model()
        return VoskTranscriber(self._model, self._known(grammar) if grammar is not None else None, self._rate)

    def missing_words(self, words: Sequence[str]) -> list[str]:
        model = self._require_model()
        return sorted(w for w in set(words) if model.vosk_model_find_word(w) < 0)

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "loaded": self.is_loaded(), "model_path": self._model_path}
