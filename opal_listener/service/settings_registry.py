"""Known settings (stored as strings in the settings table), their types, defaults and YAML group."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from opal_listener.app_settings import get_app_settings

Scalar = str | int | float | bool


@dataclass(frozen=True)
class SettingDef:
    key: str
    kind: type
    default: Scalar | Callable[[], Scalar]
    group: str  # "stt" -> stt.yaml, "settings" -> settings.yaml
    description: str
    choices: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None

    def default_value(self) -> Scalar:
        return self.default() if callable(self.default) else self.default


def _provider_choices() -> tuple[str, ...]:
    from opal_listener.service.providers.registry import available_providers

    return tuple(sorted(available_providers()))


SETTINGS: dict[str, SettingDef] = {
    d.key: d
    for d in [
        SettingDef("listener_provider", str, "vosk", "stt", "ListenerProvider implementation"),
        SettingDef("vosk_model_path", str, lambda: get_app_settings().vosk_model_path, "stt",
                   "Default Vosk model directory (profiles may override via model_paths.vosk_model)"),
        SettingDef("stt_mode", str, "grammar", "stt", "grammar: constrain STT to intent vocabulary; open: dictation",
                   choices=("grammar", "open")),
        SettingDef("stt_refiner", str, "none", "stt", "Optional second-pass STT", choices=("none", "faster_whisper")),
        SettingDef("refiner_timeout_ms", int, 5000, "stt",
                   "Second-pass refinement timeout; on timeout the draft transcript is used",
                   minimum=500, maximum=300000),
        SettingDef("whisper_model", str, "tiny.en", "stt", "faster-whisper model name or path"),
        SettingDef("whisper_compute_type", str, "int8", "stt", "faster-whisper compute type",
                   choices=("int8", "int8_float32", "float32")),
        SettingDef("wakeword_trigger", str, "partial", "stt",
                   "partial: open session on partial hit (fast); final: wait for final result",
                   choices=("partial", "final")),
        SettingDef("wakeword_distractors", str, "commuter,compute,computing,commute", "stt",
                   "Comma-separated sound-alike words that reject false wakeword hits"),
        SettingDef("audio_device", str, "", "stt", "Input device index or name substring ('' = default)"),
        SettingDef("audio_capture_rate", int, 0, "stt", "Capture sample rate (0 = auto)", minimum=0, maximum=192000),
        SettingDef("audio_block_ms", int, 100, "stt", "Audio frame size in ms", minimum=20, maximum=500),
        SettingDef("auto_start_listener", bool, False, "settings", "Start the microphone pipeline on startup"),
        SettingDef("cooldown_ms", int, 1500, "settings", "Ignore wakewords for this long after a session",
                   minimum=0, maximum=60000),
        SettingDef("dedup_window_ms", int, 2000, "settings", "Flag identical results within this window",
                   minimum=0, maximum=60000),
        SettingDef("auto_capture_failures", bool, True, "settings", "Store no_match utterances as failures"),
        SettingDef("event_publication_enabled", bool, False, "settings",
                   "Publish matched intents to the external event bus (transport: event_transport)"),
        SettingDef("filler_words", str, "uh,um,er,please", "settings", "Comma-separated words dropped"),
        SettingDef("slot_fuzzy_min_score", float, 0.85, "settings", "Minimum fuzzy score for slot values",
                   minimum=0.0, maximum=1.0),
        SettingDef("token_fuzzy_min_score", float, 0.8, "settings", "Minimum fuzzy score for phrase tokens",
                   minimum=0.0, maximum=1.0),
        SettingDef("audio_watchdog_timeout_ms", int, 3000, "settings", "Restart capture if no audio for this long",
                   minimum=500, maximum=60000),
        SettingDef("max_recognition_logs", int, 10000, "settings", "Recognition log retention (rows)",
                   minimum=100, maximum=1000000),
    ]
}  # fmt: skip


class SettingValueError(ValueError):
    pass


def parse_setting(key: str, raw: Any) -> Scalar:
    """Validate and convert a raw value (string or scalar) for a known key."""
    definition = SETTINGS.get(key)
    if definition is None:
        raise SettingValueError(f"unknown setting '{key}' (known: {', '.join(sorted(SETTINGS))})")
    value: Scalar
    try:
        if definition.kind is bool:
            if isinstance(raw, bool):
                value = raw
            elif str(raw).strip().lower() in ("true", "1", "yes", "on"):
                value = True
            elif str(raw).strip().lower() in ("false", "0", "no", "off"):
                value = False
            else:
                raise ValueError("expected a boolean")
        elif definition.kind is int:
            if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
                raise ValueError("expected an integer")
            value = int(raw)
        elif definition.kind is float:
            if isinstance(raw, bool):
                raise ValueError("expected a number")
            value = float(raw)
        else:
            value = str(raw)
    except (TypeError, ValueError) as exc:
        raise SettingValueError(f"setting '{key}': {exc}") from exc
    choices = definition.choices or (_provider_choices() if key == "listener_provider" else None)
    if choices and value not in choices:
        raise SettingValueError(f"setting '{key}' must be one of {', '.join(choices)}")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if definition.minimum is not None and value < definition.minimum:
            raise SettingValueError(f"setting '{key}' must be >= {definition.minimum}")
        if definition.maximum is not None and value > definition.maximum:
            raise SettingValueError(f"setting '{key}' must be <= {definition.maximum}")
    return value


def to_storage(value: Scalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def effective_settings(stored: Mapping[str, str]) -> dict[str, Scalar]:
    """Defaults overlaid with valid stored values (invalid stored values fall back to defaults)."""
    result: dict[str, Scalar] = {k: d.default_value() for k, d in SETTINGS.items()}
    for key, raw in stored.items():
        if key in SETTINGS:
            try:
                result[key] = parse_setting(key, raw)
            except SettingValueError:
                continue
    return result


def keys_in_group(group: str) -> list[str]:
    return [k for k, d in SETTINGS.items() if d.group == group]
