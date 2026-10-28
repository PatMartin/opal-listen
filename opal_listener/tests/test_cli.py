"""CLI coverage: every command runs against the API (via TestClient) and the tree matches the REST mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer

from opal_listener.cli.main import OPERATION_COMMANDS
from opal_listener.cli.main import app as cli_app
from opal_listener.tests.conftest import FIXTURES_DIR, CliHarness


def command_paths() -> set[str]:
    root = typer.main.get_command(cli_app)
    listen = root.commands["listen"]  # type: ignore[attr-defined]
    paths: set[str] = set()
    for group_name, group in listen.commands.items():
        assert hasattr(group, "commands"), f"{group_name} is not a command group"
        paths.update(f"{group_name} {name}" for name in group.commands)
    return paths


REQUIRED_COMMANDS = {
    "wakewords list", "wakewords add", "wakewords remove",
    "intents list", "intents add", "intents remove", "intents add-phrase",
    "slots list", "slots add", "slots add-value", "slots add-alias",
    "profiles list", "profiles add",
    "test text", "test audio", "test listen", "test listen-debug", "test regression",
    "failed list", "failed promote",
    "perf last", "perf summary",
    "config validate", "config backup", "config restore", "config export", "config import",
    "system doctor",
}  # fmt: skip


def test_required_commands_exist() -> None:
    assert command_paths() >= REQUIRED_COMMANDS


def test_every_rest_mapping_points_to_a_real_command() -> None:
    assert set(OPERATION_COMMANDS.values()) <= command_paths()


def test_wakewords_commands(cli: CliHarness) -> None:
    assert cli("wakewords", "add", "Jarvis", "--threshold", "0.7").exit_code == 0
    names = [w["name"] for w in cli.json("wakewords", "list")]
    assert names == ["computer", "jarvis"]
    assert cli.json("wakewords", "get", "jarvis")["confidence_threshold"] == 0.7
    assert cli("wakewords", "update", "jarvis", "--disabled").exit_code == 0
    assert cli.json("wakewords", "get", "jarvis")["enabled"] is False
    assert cli("wakewords", "remove", "jarvis").exit_code == 0
    assert "Wakewords" in cli("wakewords", "list").output
    missing = cli("wakewords", "remove", "nobody")
    assert missing.exit_code == 1 and "not found" in missing.output


def test_intents_commands(cli: CliHarness) -> None:
    result = cli("intents", "add", "music_play", "--phrase", "play music", "--description", "Music")
    assert result.exit_code == 0, result.output
    assert cli("intents", "add-phrase", "music_play", "play (some | the) music").exit_code == 0
    assert len(cli.json("intents", "phrases", "music_play")) == 2
    assert cli.json("intents", "get", "music_play")["description"] == "Music"
    assert cli("intents", "remove-phrase", "music_play", "play music").exit_code == 0
    assert cli("intents", "update", "music_play", "--threshold", "0.9").exit_code == 0
    bad = cli("intents", "add-phrase", "music_play", "play {ghost}")
    assert bad.exit_code == 1 and "unknown slot" in bad.output
    assert cli("intents", "remove", "music_play").exit_code == 0
    assert "light_turn_on" in cli("intents", "list").output
    assert cli("intents", "update", "get_time").exit_code == 1  # nothing to update


def test_slots_commands(cli: CliHarness) -> None:
    assert cli("slots", "add", "color", "--value", "red").exit_code == 0
    assert cli("slots", "add-value", "color", "blue", "--alias", "navy").exit_code == 0
    assert cli("slots", "add-alias", "color", "red", "crimson").exit_code == 0
    values = cli.json("slots", "values", "color")
    assert [v["value"] for v in values] == ["red", "blue"]
    assert [a["alias"] for a in cli.json("slots", "aliases", "color", "red")] == ["crimson"]
    assert cli.json("slots", "get", "color")["values"][1]["aliases"][0]["alias"] == "navy"
    assert cli("slots", "remove-alias", "color", "red", "crimson").exit_code == 0
    assert cli("slots", "remove-value", "color", "blue").exit_code == 0
    assert cli("slots", "update", "color", "--description", "Colours").exit_code == 0
    assert "room_name" in cli("slots", "list").output
    assert cli("slots", "remove", "color").exit_code == 0
    in_use = cli("slots", "remove", "room_name")
    assert in_use.exit_code == 1


def test_profiles_and_settings_commands(cli: CliHarness) -> None:
    result = cli(
        "profiles", "add", "night", "--threshold", "intent=0.9", "--vad", "silence_end_ms=900",
        "--intent", "get_time", "--wakeword", "computer",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    profile = cli.json("profiles", "get", "night")
    assert profile["confidence_thresholds"] == {"intent": 0.9}
    assert profile["vad_settings"] == {"silence_end_ms": 900}
    assert cli("profiles", "activate", "night").exit_code == 0
    assert [p["name"] for p in cli.json("profiles", "list") if p["is_active"]] == ["night"]
    assert cli("profiles", "update", "night", "--model", "vosk_model=/nope").exit_code == 1
    assert cli("profiles", "activate", "default").exit_code == 0
    assert cli("profiles", "remove", "night").exit_code == 0
    assert "noisy_room" in cli("profiles", "list").output

    assert cli("settings", "set", "cooldown_ms", "900").exit_code == 0
    assert cli.json("settings", "list")["cooldown_ms"] == "900"
    assert cli("settings", "set", "cooldown_ms", "abc").exit_code == 1
    assert cli("settings", "set", "cooldown_ms", "1500").exit_code == 0
    assert cli.json("settings", "list")["cooldown_ms"] == "1500"


def test_test_text_command(cli: CliHarness) -> None:
    result = cli.json("test", "text", "computer, turn on the kitchen light")
    assert result["intent_name"] == "light_turn_on" and result["slot_values"] == {"room_name": "kitchen"}
    assert "MATCH" in cli("test", "text", "what time is it").output
    assert cli("test", "text", "open the door", "--expect-intent", "get_time").exit_code == 1
    full = cli.json("test", "text", "what time is it", "--verbose", "--no-save")
    assert "alternatives" in full


@pytest.mark.vosk
def test_test_audio_command(cli: CliHarness) -> None:
    wav = str(FIXTURES_DIR / "golden_kitchen_light_on.wav")
    result = cli.json("test", "audio", wav, "--require-wakeword")
    assert result["intent_name"] == "light_turn_on" and result["wakeword"] == "computer"
    assert cli("test", "audio", wav, "--expect-intent", "light_turn_on").exit_code == 0


def test_test_listen_commands(cli: CliHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    from opal_listener.cli import client as client_module

    events = [
        ("status", {"state": "listening", "last_error": None}),
        ("audio_level", {"dbfs": -42.0, "state": "listening"}),
        ("wakeword_candidate", {"wakeword": "computer"}),
        ("partial", {"text": "turn on"}),
        ("result", {"intent_name": "light_turn_on", "matched": True, "slot_values": {"room_name": "kitchen"}}),
        ("result", {"intent_name": "get_time", "matched": True, "slot_values": {}}),
    ]
    calls: list[str] = []

    def fake_stream(self: Any, path: str, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        calls.append(f"{path}?debug={params['debug']}")
        yield from events

    monkeypatch.setattr(client_module.ApiClient, "stream_events", fake_stream)
    service = cli.client.app.state.listener  # type: ignore[attr-defined]
    monkeypatch.setattr(service, "start", lambda: {"status": "already_running"})
    out = cli("test", "listen", "--count", "1")
    assert out.exit_code == 0 and "light_turn_on" in out.output and "get_time" not in out.output
    debug = cli("test", "listen-debug", "--count", "2")
    assert debug.exit_code == 0
    assert "dBFS" in debug.output and "turn on" in debug.output and "wakeword_candidate" in debug.output
    assert calls == ["/api/v1/recognition/stream?debug=False", "/api/v1/recognition/stream?debug=True"]


def test_pipeline_commands(cli: CliHarness, fake_provider: type) -> None:
    from opal_listener.tests.test_pipeline_runtime import ScriptedSource

    cli.client.app.state.listener._source_factory = lambda settings: ScriptedSource([])  # type: ignore[attr-defined]
    assert cli.json("pipeline", "start")["status"] == "started"
    assert cli.json("pipeline", "status")["running"] is True
    assert cli.json("pipeline", "stop")["status"] == "stopped"


def test_regression_and_failed_commands(cli: CliHarness) -> None:
    assert cli("regression", "add", "time", "what time is it", "--expect-intent", "get_time").exit_code == 0
    result = cli(
        "regression", "add", "kitchen", "turn on the kitchen light",
        "--expect-intent", "light_turn_on", "--slot", "room_name=kitchen",
    )  # fmt: skip
    assert result.exit_code == 0
    assert cli("test", "regression").exit_code == 0
    tests = cli.json("regression", "list")
    assert tests[1]["expected_slots"] == {"room_name": "kitchen"}
    assert cli.json("regression", "get", str(tests[0]["id"]))["last_result"] is True
    assert cli("regression", "update", str(tests[0]["id"]), "--expect-intent", "cancel").exit_code == 0
    failing = cli("test", "regression")
    assert failing.exit_code == 1 and "1 regression test(s) failed" in failing.output
    assert cli("regression", "remove", str(tests[0]["id"])).exit_code == 0

    added = cli.json("failed", "add", "illuminate the garage", "--notes", "user said")
    assert cli.json("failed", "get", str(added["id"]))["notes"] == "user said"
    assert cli("failed", "update", str(added["id"]), "--reviewed").exit_code == 0
    assert cli.json("failed", "list", "--reviewed")[0]["id"] == added["id"]
    promoted = cli.json("failed", "promote", str(added["id"]), "--intent", "light_turn_on")
    assert promoted["phrase"]["phrase"] == "illuminate the {room_name}"
    assert cli.json("test", "text", "illuminate the office")["slot_values"] == {"room_name": "office"}
    assert cli("failed", "remove", str(added["id"])).exit_code == 0


def test_logs_and_perf_commands(cli: CliHarness) -> None:
    assert "no recognitions yet" in cli("perf", "last").output
    cli("test", "text", "what time is it")
    cli("test", "text", "open the door")
    assert cli.json("logs", "count") == {"total": 2, "matched": 1, "no_match": 1}
    logs = cli.json("logs", "list", "--no-match")
    assert logs[0]["normalized_transcript"] == "open the door"
    assert cli.json("logs", "get", str(logs[0]["id"]))["matched"] is False
    assert cli.json("perf", "last")["matched"] is False
    assert cli.json("perf", "summary")["count"] == 2
    assert "Latency" in cli("perf", "summary").output
    assert len(cli.json("perf", "history")) == 2


def test_config_commands(cli: CliHarness, tmp_path: Path) -> None:
    assert "valid" in cli("config", "validate").output
    out_dir = tmp_path / "export"
    assert cli("config", "export", "--out-dir", str(out_dir)).exit_code == 0
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(
        ["wakewords.yaml", "stt.yaml", "intents.yaml", "slots.yaml", "profiles.yaml", "settings.yaml"]
    )
    assert cli("config", "validate", "--from-dir", str(out_dir)).exit_code == 0
    assert json.loads(cli("config", "export").output)["wakewords"][0]["name"] == "computer"

    (out_dir / "wakewords.yaml").write_text("version: 1\nwakewords:\n  - name: jarvis\n")
    assert cli("config", "import", "--from-dir", str(out_dir), "--yes").exit_code == 0
    assert [w["name"] for w in cli.json("wakewords", "list")] == ["jarvis"]
    assert cli("config", "import", "--yes").exit_code == 0  # server config dir
    assert [w["name"] for w in cli.json("wakewords", "list")] == ["computer"]
    assert cli("config", "import", "--from-dir", str(out_dir), input="n\n").exit_code == 1  # aborted

    (out_dir / "intents.yaml").write_text("intents: [{name: x, phrases: ['{ghost}']}]")
    invalid = cli("config", "validate", "--from-dir", str(out_dir))
    assert invalid.exit_code == 1 and "unknown slot" in invalid.output

    backup = cli.json("config", "backup")["filename"]
    assert backup in [b["filename"] for b in cli.json("config", "backups")]
    assert cli("config", "download", backup, "--out", str(tmp_path)).exit_code == 0
    assert (tmp_path / backup).read_bytes()[:6] == b"SQLite"
    cli("wakewords", "add", "friday")
    assert cli("config", "restore", backup, "--yes").exit_code == 0
    assert [w["name"] for w in cli.json("wakewords", "list")] == ["computer"]
    assert cli("config", "delete-backup", backup, "--yes").exit_code == 0


def test_system_commands(cli: CliHarness) -> None:
    doctor = cli("system", "doctor")
    assert "sqlite" in doctor.output and "overall" in doctor.output
    assert isinstance(cli.json("system", "devices"), list)
    assert cli.json("system", "info")["name"] == "opal_listener"
    assert "is up" in cli("system", "ping").output


def test_event_publish_command(cli: CliHarness) -> None:
    """The CLI's PUB socket connects to the broker's XSUB endpoint; a real subscriber connects to
    the broker's XPUB endpoint. This mirrors the real opal_listener event bus topology (an
    XPUB/XSUB proxy), where --xpub/--xsub name the proxy's two bound endpoints."""
    import contextlib
    import threading

    import zmq

    def _run_proxy(frontend: zmq.Socket, backend: zmq.Socket) -> None:
        with contextlib.suppress(zmq.ZMQError):  # raised when torn down at the end of the test
            zmq.proxy(frontend, backend)

    ctx = zmq.Context()
    xpub = ctx.socket(zmq.XPUB)
    xpub_port = xpub.bind_to_random_port("tcp://127.0.0.1")
    xsub = ctx.socket(zmq.XSUB)
    xsub_port = xsub.bind_to_random_port("tcp://127.0.0.1")
    proxy_thread = threading.Thread(target=_run_proxy, args=(xpub, xsub), daemon=True)
    proxy_thread.start()

    sub = ctx.socket(zmq.SUB)
    sub.connect(f"tcp://127.0.0.1:{xpub_port}")
    sub.setsockopt_string(zmq.SUBSCRIBE, "")
    try:
        result = cli("--xpub", f"tcp://127.0.0.1:{xsub_port}", "event", "publish", "wakeword", "hello world")
        assert result.exit_code == 0, result.output
        assert f"tcp://127.0.0.1:{xsub_port}" in result.output
        assert sub.poll(3000), "no message received through the XPUB/XSUB broker"
        topic, message = sub.recv_multipart()
        assert topic == b"wakeword"
        envelope = json.loads(message)
        assert envelope["topic"] == "wakeword" and envelope["source"] == "opal-listen"
        assert envelope["data"]["message"] == "hello world"
    finally:
        sub.close()
        xpub.close()
        xsub.close()
        ctx.term()


def test_event_publish_wraps_plain_text(cli: CliHarness) -> None:
    from opal_listener.cli.event_cmds import event_envelope_for

    envelope = event_envelope_for("opal.listen.note", "hello world")
    assert envelope["topic"] == "opal.listen.note"
    assert envelope["id"] and envelope["source"] == "opal-listen" and envelope["timestamp"].endswith("Z")
    assert envelope["data"]["message"] == "hello world"


def test_event_publish_handles_json_payloads(cli: CliHarness) -> None:
    from opal_listener.cli.event_cmds import event_envelope_for

    # a JSON object becomes the event data
    envelope = event_envelope_for("opal.listen.note", '{"room": "kitchen"}')
    assert envelope["data"]["room"] == "kitchen"
    assert envelope["data"]["metadata"]["schema_version"] == "2.0"
    # a complete envelope passes through with defaults filled for missing optional fields
    envelope = event_envelope_for("opal.listen.note", '{"topic": "opal.listen.note", "data": {"x": 1}}')
    assert envelope["data"] == {"x": 1}
    assert envelope["id"] and envelope["source"] == "opal-listen" and envelope["timestamp"].endswith("Z")


def test_event_publish_rejects_envelope_topic_mismatch(cli: CliHarness) -> None:
    result = cli("event", "publish", "a.b", '{"topic": "x.y", "data": {}}')
    assert result.exit_code == 1
    assert "does not match" in result.output


def test_event_publish_rejects_invalid_topic(cli: CliHarness) -> None:
    result = cli("event", "publish", "Not A Topic", "hello")
    assert result.exit_code == 1
    assert "invalid topic" in result.output


def test_unreachable_api_gives_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    result = CliRunner().invoke(cli_app, ["listen", "--api-url", "http://127.0.0.1:9", "system", "ping"])
    assert result.exit_code == 1
    assert "cannot reach the opal listener API" in result.output
