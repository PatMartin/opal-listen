"""Unit tests: templates, normalization, numbers, intent engine, VAD, resampling."""

from __future__ import annotations

import numpy as np
import pytest

from opal_listener.service.audio.capture import StreamResampler
from opal_listener.service.audio.pcm import AudioFormatError, load_wav_bytes, rms_dbfs, write_wav_bytes
from opal_listener.service.audio.vad import EnergyVad, VadEvent, VadSettings
from opal_listener.service.nlu.engine import EngineOptions, IntentDef, IntentEngine, SlotDef
from opal_listener.service.nlu.normalizer import normalize_text, split_wakeword
from opal_listener.service.nlu.numbers import parse_number
from opal_listener.service.nlu.templates import SlotRef, TemplateError, expand_template

SLOTS = [
    SlotDef("room_name", "list", [("kitchen", ["cooking area"]), ("living_room", ["lounge"]), ("bedroom", [])]),
    SlotDef("temperature", "number"),
    SlotDef("item", "text"),
]
INTENTS = [
    IntentDef("light_turn_on", ["turn on [the] {room_name} (light | lights)"], 0.75),
    IntentDef("light_turn_off", ["turn off [the] {room_name} (light | lights)"], 0.75),
    IntentDef("set_temperature", ["set [the] temperature to {temperature} [degrees]"], 0.75),
    IntentDef("add_item", ["add {item} to [my] shopping list"], 0.75),
]


@pytest.fixture
def engine() -> IntentEngine:
    engine, report = IntentEngine.compile(INTENTS, SLOTS)
    assert report.ok, report.errors
    return engine


# --- templates / normalizer / numbers --------------------------------------------------------


def test_expand_optional_and_alternatives() -> None:
    variants = expand_template("turn on [the] {room_name} (light | lights)")
    rendered = {" ".join(str(t) for t in v) for v in variants}
    assert rendered == {
        "turn on the {room_name} light",
        "turn on the {room_name} lights",
        "turn on {room_name} light",
        "turn on {room_name} lights",
    }
    assert any(isinstance(t, SlotRef) for t in variants[0])


@pytest.mark.parametrize("bad", ["turn on [the", "turn (on | off", "turn on {}", "a ] b", "[ ]", "{unclosed"])
def test_template_syntax_errors(bad: str) -> None:
    with pytest.raises(TemplateError):
        expand_template(bad)


def test_template_expansion_limit() -> None:
    with pytest.raises(TemplateError, match="more than"):
        expand_template(" ".join(["[a]"] * 10))


def test_normalize_text() -> None:
    assert normalize_text("Computer, turn ON the Kitchen-Light!! [unk]") == "computer turn on the kitchen light"
    assert normalize_text("um please turn on", ["um", "please"]) == "turn on"
    assert normalize_text("what's up") == "what's up"


def test_split_wakeword_prefers_longest_and_drops_preroll() -> None:
    assert split_wakeword("hey computer turn on", ["computer", "hey computer"]) == ("hey computer", "turn on")
    assert split_wakeword("noise computer lights", ["computer"]) == ("computer", "lights")
    assert split_wakeword("turn on", ["computer"]) == (None, "turn on")


@pytest.mark.parametrize(
    ("tokens", "value"),
    [("five", 5), ("twenty one", 21), ("one hundred twenty", 120), ("42", 42), ("two thousand five", 2005)],
)
def test_parse_number(tokens: str, value: int) -> None:
    assert parse_number(tokens.split()) == value


def test_parse_number_rejects_words() -> None:
    assert parse_number(["five", "apples"]) is None
    assert parse_number([]) is None


# --- engine ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "intent", "slots"),
    [
        ("turn on the kitchen light", "light_turn_on", {"room_name": "kitchen"}),
        ("turn on cooking area lights", "light_turn_on", {"room_name": "kitchen"}),
        ("turn off the lounge light", "light_turn_off", {"room_name": "living_room"}),
        ("turn on the living room light", "light_turn_on", {"room_name": "living_room"}),
        ("set temperature to twenty one degrees", "set_temperature", {"temperature": 21}),
        ("add oat milk to my shopping list", "add_item", {"item": "oat milk"}),
    ],
)
def test_engine_matches(engine: IntentEngine, text: str, intent: str, slots: dict) -> None:
    result = engine.match(normalize_text(text))
    assert (result.intent_name, result.slot_values, result.matched) == (intent, slots, True)


def test_fuzzy_slot_value(engine: IntentEngine) -> None:
    result = engine.match("turn on the bedrooms light")
    assert result.matched and result.slot_values == {"room_name": "bedroom"}
    assert result.confidence < 1.0


def test_on_off_are_not_confused(engine: IntentEngine) -> None:
    result = engine.match("turn off the kitchen light")
    assert result.intent_name == "light_turn_off"
    assert result.alternatives[1].intent_name == "light_turn_on"
    assert result.alternatives[1].confidence < 0.75


@pytest.mark.parametrize(
    ("text", "reason"),
    [("", "empty_transcript"), ("turn on the light", "missing_slot"), ("sing me a song", "no_candidate")],
)
def test_engine_no_match_reasons(engine: IntentEngine, text: str, reason: str) -> None:
    result = engine.match(text)
    assert not result.matched and result.intent_name == "no_match" and result.no_match_reason == reason


def test_threshold_floor_from_profile() -> None:
    engine, _ = IntentEngine.compile(INTENTS, SLOTS, EngineOptions(intent_threshold_floor=0.95))
    assert not engine.match("turn on kitchen light now").matched
    assert engine.match("turn on the kitchen light").matched


@pytest.mark.parametrize(
    ("intents", "slots", "error"),
    [
        ([IntentDef("x", ["turn on {nope}"])], [], "unknown slot"),
        ([IntentDef("Bad-Name", ["hi"])], [], "snake_case"),
        ([IntentDef("no_match", ["hi"])], [], "reserved"),
        ([IntentDef("x", ["{room_name} and {room_name}"])], SLOTS, "only once"),
        ([IntentDef("x", ["go to {empty}"])], [SlotDef("empty", "list", [])], "has no values"),
        ([], [SlotDef("s", "color", [])], "slot_type"),
        ([], [SlotDef("s", "list", [("a", ["x"]), ("b", ["x"])])], "maps to both"),
    ],
)
def test_compile_errors(intents: list, slots: list, error: str) -> None:
    _, report = IntentEngine.compile(intents, slots)
    assert any(error in e for e in report.errors), report.errors


def test_grammar_keeps_literal_runs_together(engine: IntentEngine) -> None:
    phrases = engine.grammar_phrases()
    assert "set the temperature to" in phrases
    assert "cooking area" in phrases and "twenty" in phrases
    assert engine.requires_open_vocabulary is True


# --- audio helpers ---------------------------------------------------------------------------


def test_vad_start_end_and_timeouts() -> None:
    vad = EnergyVad(VadSettings(speech_start_ms=200, silence_end_ms=300, no_speech_timeout_ms=1000), 100)
    events = [vad.process(level) for level in [-20, -20, -20, -80, -80, -80]]
    assert events[1] == VadEvent.SPEECH_START
    assert events[5] == VadEvent.SPEECH_END
    vad = EnergyVad(VadSettings(no_speech_timeout_ms=500), 100)
    assert [vad.process(-80) for _ in range(5)][-1] == VadEvent.NO_SPEECH
    vad = EnergyVad(VadSettings(max_utterance_ms=1000, speech_start_ms=0), 100)
    assert [vad.process(-10) for _ in range(10)][-1] == VadEvent.MAX_LENGTH


def test_vad_noise_floor_adapts_upwards() -> None:
    vad = EnergyVad(VadSettings(), 100)
    for _ in range(300):
        vad.observe_background(-35.0)
    assert vad.threshold_dbfs > -30.0
    assert not vad.is_speech(-35.0)


def test_vad_settings_reject_unknown_keys() -> None:
    with pytest.raises(ValueError):
        VadSettings.model_validate({"silence_ms": 5})


def test_stream_resampler_48k_matches_length_and_tone() -> None:
    resampler = StreamResampler(48000)
    t = np.arange(48000) / 48000
    tone = (8000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
    out = np.concatenate([resampler.process(block) for block in np.split(tone, 10)])
    assert len(out) == 16000
    spectrum = np.abs(np.fft.rfft(out[1000:]))
    assert abs(np.argmax(spectrum) * 16000 / len(out[1000:]) - 440) < 5


def test_wav_roundtrip_and_validation() -> None:
    pcm = (np.ones(1600) * 1000).astype(np.int16).tobytes()
    assert load_wav_bytes(write_wav_bytes(pcm)) == pcm
    assert load_wav_bytes(write_wav_bytes(pcm, rate=8000))  # resampled
    with pytest.raises(AudioFormatError):
        load_wav_bytes(b"not a wav file" * 10)
    assert rms_dbfs(b"") == -96.0
