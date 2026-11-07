"""CLI tests for `opal listen reactions` — file-managed intent reactions (no REST arming)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from opal_listener.service.reactions import load_reactions
from opal_listener.tests.conftest import CliHarness


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    config_dir, data_dir = tmp_path / "config", tmp_path / "data"
    monkeypatch.setenv("OPAL_LISTENER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("OPAL_LISTENER_DATA_DIR", str(data_dir))
    return config_dir, data_dir


def _script(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / f"{name}.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_add_list_get_update_remove(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    config_dir, _ = cfg
    result = cli(
        "reactions", "add", "kitchen",
        "--cond", "intent_name eq light_turn_on",
        "--cond", "slot_values.room_name eq kitchen",
        "--cond", "intent_confidence gte 0.8",
        "--run", "sh /opt/hue.sh kitchen on",
        "--timeout", "20",
    )
    assert result.exit_code == 0, result.output

    # The written file loads through the service-side parser.
    reactions = load_reactions(config_dir / "reactions.yaml")
    assert len(reactions) == 1
    assert reactions[0].actions[0].argv == ["sh", "/opt/hue.sh", "kitchen", "on"]
    assert reactions[0].actions[0].timeout_s == 20.0

    listed = cli.json("reactions", "list")
    assert listed[0]["name"] == "kitchen"
    assert len(listed[0]["conditions"]) == 3

    got = cli.json("reactions", "get", "kitchen")
    assert got["conditions"][1] == {"path": "slot_values.room_name", "operator": "eq", "value": "kitchen"}

    assert cli("reactions", "update", "kitchen", "--name", "kitchen_lights", "--timeout", "5").exit_code == 0
    got = cli.json("reactions", "get", "kitchen_lights")
    assert got["actions"][0]["timeout_s"] == 5.0

    assert cli("reactions", "update", "kitchen_lights", "--clear-conditions").exit_code == 0
    assert cli.json("reactions", "get", "kitchen_lights")["conditions"] == []

    assert cli("reactions", "disable", "kitchen_lights").exit_code == 0
    assert cli.json("reactions", "get", "kitchen_lights")["enabled"] is False
    assert cli("reactions", "enable", "kitchen_lights").exit_code == 0

    assert cli("reactions", "remove", "kitchen_lights").exit_code == 0
    assert cli.json("reactions", "list") == []
    assert (config_dir / "reactions.yaml").is_file()


def test_add_requires_run_and_rejects_duplicates(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    assert cli("reactions", "add", "r1").exit_code != 0  # no --run
    assert cli("reactions", "add", "r1", "--run", "/bin/true").exit_code == 0
    assert cli("reactions", "add", "r1", "--run", "/bin/true").exit_code != 0  # duplicate name


def test_cond_parsing_and_validation(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    assert cli("reactions", "add", "bad", "--run", "/bin/true", "--cond", "onlypath").exit_code != 0
    assert cli("reactions", "add", "bad2", "--run", "/bin/true", "--cond", "p bogus_op v").exit_code != 0
    assert cli("reactions", "add", "bad3", "--run", "/bin/true", "--cond", "p exists x").exit_code != 0
    # 'in' value parses as a YAML list
    assert cli(
        "reactions", "add", "ok", "--run", "/bin/true",
        "--cond", "slot_values.room in [kitchen, den]",
    ).exit_code == 0
    got = cli.json("reactions", "get", "ok")
    assert got["conditions"][0]["value"] == ["kitchen", "den"]


def test_update_replaces_conditions_and_actions(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    assert cli("reactions", "add", "r", "--run", "a 1", "--cond", "intent_name eq x").exit_code == 0
    assert cli(
        "reactions", "update", "r",
        "--cond", "intent_name eq y",
        "--run", "b 2", "--run", "c 3", "--env", "K=V",
    ).exit_code == 0
    got = cli.json("reactions", "get", "r")
    assert got["conditions"] == [{"path": "intent_name", "operator": "eq", "value": "y"}]
    assert [a["argv"] for a in got["actions"]] == [["b", "2"], ["c", "3"]]
    assert all(a["env"] == {"K": "V"} for a in got["actions"])


def test_missing_name_errors(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    assert cli("reactions", "get", "ghost").exit_code != 0
    assert cli("reactions", "update", "ghost", "--disabled").exit_code != 0
    assert cli("reactions", "remove", "ghost").exit_code != 0
    assert cli("reactions", "enable", "ghost").exit_code != 0


def test_dry_run_with_payload(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    assert cli(
        "reactions", "add", "time", "--run", "/bin/true",
        "--cond", "intent_name eq get_time",
        "--cond", "intent_confidence gte 0.8",
    ).exit_code == 0
    hit = cli.json("reactions", "test", "--payload",
                   '{"intent_name": "get_time", "intent_confidence": 0.9}')
    assert hit == [{"name": "time", "enabled": True, "match": True, "failed_conditions": []}]
    miss = cli.json("reactions", "test", "--payload",
                    '{"intent_name": "get_time", "intent_confidence": 0.5}')
    assert miss[0]["match"] is False
    assert miss[0]["failed_conditions"][0]["path"] == "intent_confidence"
    # exactly one source flag
    assert cli("reactions", "test").exit_code != 0
    assert cli("reactions", "test", "--payload", "{}", "--text", "x").exit_code != 0


def test_execute_runs_matched_commands(cli: CliHarness, cfg: tuple[Path, Path], tmp_path: Path) -> None:
    marker = tmp_path / "fired.txt"
    script = _script(tmp_path, "mark",
                     "import json,sys; d=json.load(sys.stdin); "
                     "open(sys.argv[1],'w').write(d['intent_name'])")
    assert cli(
        "reactions", "add", "r", "--run", f"{sys.executable} {script} {marker}",
        "--cond", "intent_name eq light_turn_on",
    ).exit_code == 0
    result = cli("reactions", "test", "--payload", '{"intent_name": "light_turn_on"}', "--execute")
    assert result.exit_code == 0, result.output
    assert marker.read_text() == "light_turn_on"
    # non-matching payload: nothing runs
    marker.unlink()
    result = cli("reactions", "test", "--payload", '{"intent_name": "other"}', "--execute")
    assert result.exit_code == 0
    assert not marker.exists()


def test_text_uses_real_recognition(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    assert cli("reactions", "add", "r", "--run", "/bin/true", "--cond", "intent_name eq light_turn_on").exit_code == 0
    report = cli.json("reactions", "test", "--text", "turn on the kitchen light")
    assert report[0]["match"] is True


def test_log_reads_execution_records(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    _, data_dir = cfg
    data_dir.mkdir(parents=True, exist_ok=True)
    records = [
        {"ts": "2026-11-07T05:00:00Z", "reactions": "r1", "argv": ["/bin/true"],
         "exit_code": 0, "duration_ms": 12.3},
        {"ts": "2026-11-07T05:00:01Z", "reactions": "r2", "argv": ["/bin/false"],
         "exit_code": 1, "stderr_tail": "boom", "duration_ms": 8.0},
    ]
    (data_dir / "reaction_log.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    out = cli.json("reactions", "log")
    assert [r["reactions"] for r in out] == ["r1", "r2"]
    out1 = cli.json("reactions", "log", "--limit", "1")
    assert out1[0]["reactions"] == "r2"


def test_log_missing_file_is_graceful(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    """An absent journal is a normal state (nothing fired yet), not an error."""
    result = cli("reactions", "log")
    assert result.exit_code == 0
    assert "no reaction executions" in result.output
    assert cli.json("reactions", "log") == []


def test_path_shows_managed_files(cli: CliHarness, cfg: tuple[Path, Path]) -> None:
    config_dir, data_dir = cfg
    out = cli.json("reactions", "path")
    assert out["reactions_file"] == str(config_dir / "reactions.yaml")
    assert out["execution_log"] == str(data_dir / "reaction_log.jsonl")


def test_cli_writes_file_service_reads(cfg: tuple[Path, Path], cli: CliHarness) -> None:
    """Round-trip: CLI-managed file must validate under the service loader."""
    config_dir, _ = cfg
    assert cli(
        "reactions", "add", "svc", "--run", "/opt/x.sh", "--timeout", "30",
        "--cwd", "/opt", "--env", "A=1", "--cond", "intent_name eq x",
    ).exit_code == 0
    reactions = load_reactions(config_dir / "reactions.yaml")
    r = reactions[0]
    assert r.actions[0].cwd == "/opt" and r.actions[0].env == {"A": "1"}
    assert r.actions[0].timeout_s == 30.0
