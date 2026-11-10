"""CLI coverage: every command runs against the API (via TestClient) and the tree matches the REST mapping."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import typer
from starlette.testclient import TestClient

from opal_listener.cli.main import OPERATION_COMMANDS
from opal_listener.cli.main import app as cli_app
from opal_listener.tests.conftest import FIXTURES_DIR, CliHarness


def command_paths() -> set[str]:
    root = typer.main.get_command(cli_app)
    listen = root.commands["listen"]  # type: ignore[attr-defined]
    paths: set[str] = set()

    def walk(commands: dict, prefix: str) -> None:
        for name, command in commands.items():
            if hasattr(command, "commands"):  # nested group (e.g. service perf)
                walk(command.commands, f"{prefix} {name}")
            else:
                paths.add(f"{prefix} {name}".strip())

    walk(listen.commands, "")
    return paths


REQUIRED_COMMANDS = {
    "wakeword list", "wakeword add", "wakeword remove",
    "intent list", "intent add", "intent remove", "intent add-phrase",
    "slot list", "slot add", "slot add-value", "slot add-alias",
    "profile list", "profile add",
    "config setting list", "config setting show", "config setting set", "config setting update",
    "config setting remove", "setting get", "setting unset",
    "test text", "test audio", "test listen", "test listen-debug", "test regression",
    "recognition test",
    "failed list", "failed promote",
    "service perf last", "service perf summary",
    "config validate", "config backup", "config restore", "config export", "config import",
    "service doctor", "service start", "service stop", "service log", "gui",
    "reaction list", "reaction get", "reaction add", "reaction update", "reaction remove",
    "reaction enable", "reaction disable", "reaction test", "reaction log", "reaction path",
}  # fmt: skip


def test_required_commands_exist() -> None:
    assert command_paths() >= REQUIRED_COMMANDS


def test_every_rest_mapping_points_to_a_real_command() -> None:
    assert set(OPERATION_COMMANDS.values()) <= command_paths()


def test_wakewords_commands(cli: CliHarness) -> None:
    assert cli("wakeword", "add", "Jarvis", "--threshold", "0.7").exit_code == 0
    names = [w["name"] for w in cli.json("wakeword", "list")]
    assert names == ["computer", "jarvis"]
    assert cli.json("wakeword", "get", "jarvis")["confidence_threshold"] == 0.7
    assert cli("wakeword", "update", "jarvis", "--disabled").exit_code == 0
    assert cli.json("wakeword", "get", "jarvis")["enabled"] is False
    assert cli("wakeword", "remove", "jarvis").exit_code == 0
    assert "Wakewords" in cli("wakeword", "list").output
    missing = cli("wakeword", "remove", "nobody")
    assert missing.exit_code == 1 and "not found" in missing.output


def test_intents_commands(cli: CliHarness) -> None:
    result = cli("intent", "add", "music_play", "--phrase", "play music", "--description", "Music")
    assert result.exit_code == 0, result.output
    assert cli("intent", "add-phrase", "music_play", "play (some | the) music").exit_code == 0
    assert len(cli.json("intent", "phrases", "music_play")) == 2
    assert cli.json("intent", "get", "music_play")["description"] == "Music"
    assert cli("intent", "remove-phrase", "music_play", "play music").exit_code == 0
    assert cli("intent", "update", "music_play", "--threshold", "0.9").exit_code == 0
    bad = cli("intent", "add-phrase", "music_play", "play {ghost}")
    assert bad.exit_code == 1 and "unknown slot" in bad.output
    assert cli("intent", "remove", "music_play").exit_code == 0
    assert "light_turn_on" in cli("intent", "list").output
    assert cli("intent", "update", "get_time").exit_code == 1  # nothing to update


def test_slots_commands(cli: CliHarness) -> None:
    assert cli("slot", "add", "color", "--value", "red").exit_code == 0
    assert cli("slot", "add-value", "color", "blue", "--alias", "navy").exit_code == 0
    assert cli("slot", "add-alias", "color", "red", "crimson").exit_code == 0
    values = cli.json("slot", "values", "color")
    assert [v["value"] for v in values] == ["red", "blue"]
    assert [a["alias"] for a in cli.json("slot", "aliases", "color", "red")] == ["crimson"]
    assert cli.json("slot", "get", "color")["values"][1]["aliases"][0]["alias"] == "navy"
    assert cli("slot", "remove-alias", "color", "red", "crimson").exit_code == 0
    assert cli("slot", "remove-value", "color", "blue").exit_code == 0
    assert cli("slot", "update", "color", "--description", "Colours").exit_code == 0
    assert "room_name" in cli("slot", "list").output
    assert cli("slot", "remove", "color").exit_code == 0
    in_use = cli("slot", "remove", "room_name")
    assert in_use.exit_code == 1


def test_profiles_and_settings_commands(cli: CliHarness) -> None:
    result = cli(
        "profile", "add", "night", "--threshold", "intent=0.9", "--vad", "silence_end_ms=900",
        "--intent", "get_time", "--wakeword", "computer",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    profile = cli.json("profile", "get", "night")
    assert profile["confidence_thresholds"] == {"intent": 0.9}
    assert profile["vad_settings"] == {"silence_end_ms": 900}
    assert cli("profile", "activate", "night").exit_code == 0
    assert [p["name"] for p in cli.json("profile", "list") if p["is_active"]] == ["night"]
    assert cli("profile", "update", "night", "--model", "vosk_model=/nope").exit_code == 1
    assert cli("profile", "activate", "default").exit_code == 0
    assert cli("profile", "remove", "night").exit_code == 0
    assert "noisy_room" in cli("profile", "list").output

    assert cli("config", "setting", "set", "cooldown_ms", "900").exit_code == 0
    assert cli.json("config", "setting", "list")["cooldown_ms"] == "900"
    assert cli("config", "setting", "set", "cooldown_ms", "abc").exit_code == 1
    assert cli("config", "setting", "set", "cooldown_ms", "1500").exit_code == 0
    assert cli.json("config", "setting", "list")["cooldown_ms"] == "1500"


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


def test_recognition_test_command(cli: CliHarness, tmp_path) -> None:
    result = cli.json("recognition", "test", "--text", "computer, turn on the kitchen light")
    assert result["intent_name"] == "light_turn_on" and result["slot_values"] == {"room_name": "kitchen"}
    # exactly one of --text/--audio
    assert cli("recognition", "test").exit_code != 0
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF" + bytes(40))
    assert cli("recognition", "test", "--text", "x", "--audio", str(wav)).exit_code != 0
    # --require-wakeword is audio-only
    assert cli("recognition", "test", "--text", "x", "--require-wakeword").exit_code != 0


@pytest.mark.vosk
def test_recognition_test_audio(cli: CliHarness) -> None:
    wav = str(FIXTURES_DIR / "golden_kitchen_light_on.wav")
    result = cli.json("recognition", "test", "--audio", wav, "--require-wakeword")
    assert result["intent_name"] == "light_turn_on" and result["wakeword"] == "computer"


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


def test_perf_commands(cli: CliHarness) -> None:
    # v1.7.1 removed the `log` command group; recognition history is covered by `service perf`.
    assert "no recognitions yet" in cli("service", "perf", "last").output
    cli("test", "text", "what time is it")
    cli("test", "text", "open the door")
    assert cli.json("service", "perf", "last")["matched"] is False
    assert cli.json("service", "perf", "summary")["count"] == 2
    assert "Latency" in cli("service", "perf", "summary").output
    history = cli.json("service", "perf", "history")
    assert len(history) == 2 and history[0]["matched"] is False


def test_json_list_returns_bounded_array(cli: CliHarness) -> None:
    for i in range(5):
        cli("test", "text", f"what time is it {i}")
    page = cli.json("service", "perf", "history", "--limit", "2")
    assert isinstance(page, list) and len(page) == 2
    full = cli.json("service", "perf", "history", "--limit", "10")
    assert len(full) == 5 and full[:2] == page


def test_config_commands(cli: CliHarness, tmp_path: Path) -> None:
    assert "valid" in cli("config", "validate").output
    out_dir = tmp_path / "export"
    assert cli("config", "export", "--out-dir", str(out_dir)).exit_code == 0
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(
        ["wakewords.yaml", "stt.yaml", "intents.yaml", "slots.yaml", "profiles.yaml", "personas.yaml", "settings.yaml"]
    )
    assert cli("config", "validate", "--from-dir", str(out_dir)).exit_code == 0
    assert json.loads(cli("config", "export").output)["wakewords"][0]["name"] == "computer"

    (out_dir / "wakewords.yaml").write_text("version: 1\nwakewords:\n  - name: jarvis\n")
    assert cli("config", "import", "--from-dir", str(out_dir), "--yes").exit_code == 0
    assert [w["name"] for w in cli.json("wakeword", "list")] == ["jarvis"]
    assert cli("config", "import", "--yes").exit_code == 0  # server config dir
    assert [w["name"] for w in cli.json("wakeword", "list")] == ["computer"]
    assert cli("config", "import", "--from-dir", str(out_dir), input="n\n").exit_code == 1  # aborted

    (out_dir / "intents.yaml").write_text("intents: [{name: x, phrases: ['{ghost}']}]")
    invalid = cli("config", "validate", "--from-dir", str(out_dir))
    assert invalid.exit_code == 1 and "unknown slot" in invalid.output

    backup = cli.json("config", "backup")["filename"]
    assert backup in [b["filename"] for b in cli.json("config", "backups")]
    assert cli("config", "download", backup, "--out", str(tmp_path)).exit_code == 0
    assert (tmp_path / backup).read_bytes()[:6] == b"SQLite"
    cli("wakeword", "add", "friday")
    assert cli("config", "restore", backup, "--yes").exit_code == 0
    assert [w["name"] for w in cli.json("wakeword", "list")] == ["computer"]
    assert cli("config", "delete-backup", backup, "--yes").exit_code == 0


def test_service_commands(cli: CliHarness) -> None:
    doctor = cli("service", "doctor")
    assert "sqlite" in doctor.output and "overall" in doctor.output
    assert isinstance(cli.json("service", "devices"), list)
    assert cli.json("service", "info")["name"] == "opal-listen-svc"
    assert "is up" in cli("service", "ping").output


def test_service_stop_and_log_without_server(cli: CliHarness, tmp_path: Path, monkeypatch) -> None:
    """service stop/log are REST-first with a local fallback: no managed server -> clean no-op."""
    monkeypatch.chdir(tmp_path)
    assert "stopped" in cli("service", "stop").output
    assert "no log file" in cli("service", "log").output
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "opal_listen.log").write_text("\n".join(f"line {i}" for i in range(15)))
    out = cli("service", "log").output
    assert "line 14" in out and "line 5" in out and "line 4" not in out


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

    result = CliRunner().invoke(cli_app, ["listen", "--api-url", "http://127.0.0.1:9", "service", "ping"])
    assert result.exit_code == 1
    assert "cannot reach the opal listener API" in result.output


def test_listen_tree_renders_live_command_tree(cli: CliHarness) -> None:
    result = cli("tree")
    assert result.exit_code == 0, result.output
    out = result.output
    assert out.splitlines()[0].startswith("opal listen")
    # groups and leaf commands are present with ASCII branches
    assert "+-- wakeword" in out and "+-- list" in out and "`-- " in out
    # notation: required options bare, optional bracketed, positionals bare names
    assert "promote fu_id --intent" in out
    assert "add name [--threshold]" in out
    # built-in help and shell-completion plumbing are skipped
    assert "--help" not in out and "--install-completion" not in out


def test_listen_tree_tracks_command_changes(cli: CliHarness) -> None:
    """The tree is introspected at call time: a new command/param appears on the next run."""
    from opal_listener.cli.main import listen_app

    marker = f"zz{uuid.uuid4().hex[:6]}"
    try:

        @listen_app.command(marker)
        def _tmp(newopt: str = typer.Option(...)) -> None:  # required option
            pass

        assert f"+-- {marker} --newopt" in cli("tree").output
    finally:
        listen_app.registered_commands = [c for c in listen_app.registered_commands if c.name != marker]


def test_settings_patch_wire_shape(cli: CliHarness, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`settings patch` sends one atomic collection PATCH with If-Match and a typed map body."""
    calls: list[tuple[str, str, dict[str, Any]]] = []
    original = client.request

    def spy(method: str, url: str, **kw: Any) -> Any:
        calls.append((method, str(url), kw))
        return original(method, url, **kw)

    monkeypatch.setattr(client, "request", spy)
    result = cli("config", "setting", "patch", "cooldown_ms=2500", "filler_words=um")
    assert result.exit_code == 0, result.output
    patches = [c for c in calls if c[0] == "PATCH"]
    assert len(patches) == 1
    _method, url, kw = patches[0]
    assert url.endswith("/api/v1/setting/")
    assert kw["headers"]["If-Match"].startswith('"')
    assert kw["json"] == {"cooldown_ms": 2500, "filler_words": "um"}
    # the collection ETag was fetched first
    etag_gets = [c for c in calls[: calls.index(patches[0])] if c[0] == "GET" and c[1].endswith("/api/v1/setting/")]
    assert etag_gets, "expected a GET /api/v1/setting/ ETag probe before the PATCH"
    assert cli.json("config", "setting", "show", "cooldown_ms")["value"] == 2500
    # atomicity: one invalid key means none are applied
    assert cli("config", "setting", "patch", "cooldown_ms=1", "not_a_setting=2").exit_code != 0
    assert cli.json("config", "setting", "show", "cooldown_ms")["value"] == 2500


