"""Intent reactions: file-provisioned config, condition evaluation, dispatch gate, execution."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from opal_listener.service.reactions import (
    ReactionCondition,
    ReactionRunner,
    check_condition,
    load_reactions,
)

from .conftest import patch_settings


def write_reactions(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def wait_file(path: Path, timeout: float = 5.0) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return path.read_text(encoding="utf-8")
        time.sleep(0.05)
    return None


def marker_argv(marker: Path) -> list[str]:
    """Subprocess that copies stdin's intent_name field into the marker file."""
    return [sys.executable, "-c",
            "import json,sys; d=json.load(sys.stdin); open(sys.argv[1],'w').write(d['intent_name'])",
            str(marker)]


PAYLOAD: dict[str, Any] = {
    "intent_name": "light_turn_on",
    "slot_values": {"room_name": "kitchen"},
    "intent_confidence": 0.92,
    "wakeword": "computer",
    "transcript": "turn on the kitchen light",
    "source": "mic",
    "matched": True,
}


# --- config loading ------------------------------------------------------------------------------


def test_load_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_reactions(tmp_path / "nope.yaml") == []


def test_load_valid_file(tmp_path: Path) -> None:
    path = tmp_path / "reactions.yaml"
    write_reactions(path, {"reactions": [
        {"name": "r1", "conditions": [{"path": "intent_name", "operator": "eq", "value": "x"}],
         "actions": [{"type": "command", "argv": ["/bin/true"]}]},
    ]})
    reactions = load_reactions(path)
    assert len(reactions) == 1
    assert reactions[0].name == "r1"
    assert reactions[0].actions[0].argv == ["/bin/true"]


def test_load_invalid_yaml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "reactions.yaml"
    path.write_text("reactions: [unclosed", encoding="utf-8")
    assert load_reactions(path) == []


def test_load_invalid_schema_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "reactions.yaml"
    write_reactions(path, {"reactions": [{"name": "bad", "actions": []}]})  # actions min 1
    assert load_reactions(path) == []


# --- condition evaluation ------------------------------------------------------------------------


@pytest.mark.parametrize("cond,expected", [
    ({"path": "intent_name", "operator": "eq", "value": "light_turn_on"}, True),
    ({"path": "intent_name", "operator": "eq", "value": "other"}, False),
    ({"path": "intent_name", "operator": "ne", "value": "other"}, True),
    ({"path": "intent_confidence", "operator": "gte", "value": 0.8}, True),
    ({"path": "intent_confidence", "operator": "gt", "value": 0.95}, False),
    ({"path": "intent_confidence", "operator": "lt", "value": "high"}, False),  # non-numeric right
    ({"path": "slot_values.room_name", "operator": "in", "value": ["kitchen", "den"]}, True),
    ({"path": "slot_values.room_name", "operator": "not_in", "value": ["den"]}, True),
    ({"path": "transcript", "operator": "contains", "value": "kitchen"}, True),
    ({"path": "slot_values.missing", "operator": "exists"}, False),
    ({"path": "slot_values.room_name", "operator": "exists"}, True),
    ({"path": "slot_values.missing", "operator": "eq", "value": "x"}, False),  # absent never matches
])
def test_condition_operators(cond: dict[str, Any], expected: bool) -> None:
    assert check_condition(ReactionCondition.model_validate(cond), PAYLOAD) is expected


# --- runner --------------------------------------------------------------------------------------


def test_dispatch_fires_command(tmp_path: Path) -> None:
    config = tmp_path / "reactions.yaml"
    marker = tmp_path / "fired.txt"
    write_reactions(config, {"reactions": [
        {"name": "light", "enabled": True,
         "conditions": [{"path": "intent_name", "operator": "eq", "value": "light_turn_on"}],
         "actions": [{"type": "command", "argv": marker_argv(marker)}]},
    ]})
    runner = ReactionRunner(config, tmp_path / "log.jsonl")
    runner.start()
    try:
        assert runner.dispatch(PAYLOAD) == 1
        assert wait_file(marker) == "light_turn_on"
        time.sleep(0.2)
        assert runner.stats()["reactions_fired"] == 1.0
        lines = (tmp_path / "log.jsonl").read_text().strip().splitlines()
        assert json.loads(lines[0])["exit_code"] == 0
    finally:
        runner.close()


def test_dispatch_conditions_filter(tmp_path: Path) -> None:
    config = tmp_path / "reactions.yaml"
    marker = tmp_path / "fired.txt"
    write_reactions(config, {"reactions": [
        {"name": "wrong_room", "enabled": True,
         "conditions": [{"path": "slot_values.room_name", "operator": "eq", "value": "den"}],
         "actions": [{"type": "command", "argv": marker_argv(marker)}]},
    ]})
    runner = ReactionRunner(config, tmp_path / "log.jsonl")
    runner.start()
    try:
        assert runner.dispatch(PAYLOAD) == 0
        assert wait_file(marker, timeout=0.5) is None
    finally:
        runner.close()


def test_disabled_reaction_does_not_fire(tmp_path: Path) -> None:
    config = tmp_path / "reactions.yaml"
    marker = tmp_path / "fired.txt"
    write_reactions(config, {"reactions": [
        {"name": "off", "enabled": False, "conditions": [],
         "actions": [{"type": "command", "argv": marker_argv(marker)}]},
    ]})
    runner = ReactionRunner(config, tmp_path / "log.jsonl")
    assert runner.dispatch(PAYLOAD) == 0


def test_failed_exit_is_counted(tmp_path: Path) -> None:
    config = tmp_path / "reactions.yaml"
    write_reactions(config, {"reactions": [
        {"name": "boom", "conditions": [],
         "actions": [{"type": "command", "argv": [sys.executable, "-c", "import sys;sys.exit(3)"]}]},
    ]})
    runner = ReactionRunner(config, tmp_path / "log.jsonl")
    runner.start()
    try:
        runner.dispatch(PAYLOAD)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and runner.stats()["reactions_failed"] < 1:
            time.sleep(0.05)
        assert runner.stats()["reactions_failed"] == 1.0
        record = json.loads((tmp_path / "log.jsonl").read_text().strip().splitlines()[0])
        assert record["exit_code"] == 3
    finally:
        runner.close()


def test_timeout_is_counted_as_failure(tmp_path: Path) -> None:
    config = tmp_path / "reactions.yaml"
    write_reactions(config, {"reactions": [
        {"name": "slow", "conditions": [],
         "actions": [{"type": "command",
                      "argv": [sys.executable, "-c", "import time;time.sleep(10)"], "timeout_s": 1}]},
    ]})
    runner = ReactionRunner(config, tmp_path / "log.jsonl")
    runner.start()
    try:
        runner.dispatch(PAYLOAD)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and runner.stats()["reactions_failed"] < 1:
            time.sleep(0.05)
        record = json.loads((tmp_path / "log.jsonl").read_text().strip().splitlines()[0])
        assert "timeout" in record["error"]
    finally:
        runner.close()


def test_full_queue_drops(tmp_path: Path) -> None:
    config = tmp_path / "reactions.yaml"
    write_reactions(config, {"reactions": [
        {"name": "r", "conditions": [], "actions": [{"type": "command", "argv": ["/bin/true"]}]},
    ]})
    runner = ReactionRunner(config, tmp_path / "log.jsonl", capacity=1)
    # Worker never started: first dispatch fills the queue, the second must drop.
    runner.dispatch(PAYLOAD)
    runner.dispatch(PAYLOAD)
    assert runner.stats()["reactions_dropped"] == 1.0


def test_mtime_reload_picks_up_edits(tmp_path: Path) -> None:
    config = tmp_path / "reactions.yaml"
    marker = tmp_path / "fired.txt"
    write_reactions(config, {"reactions": []})
    runner = ReactionRunner(config, tmp_path / "log.jsonl")
    runner.start()
    try:
        assert runner.dispatch(PAYLOAD) == 0
        write_reactions(config, {"reactions": [
            {"name": "new", "conditions": [], "actions": [{"type": "command", "argv": marker_argv(marker)}]},
        ]})
        assert runner.dispatch(PAYLOAD) == 1
        assert wait_file(marker) == "light_turn_on"
    finally:
        runner.close()


# --- service gate ---------------------------------------------------------------------------------


def _arm_marker_reaction(service: Any, tmp_path: Path, marker: Path) -> None:
    config = tmp_path / "reactions.yaml"
    write_reactions(config, {"reactions": [
        {"name": "light", "conditions": [], "actions": [{"type": "command", "argv": marker_argv(marker)}]},
    ]})
    service._reaction_runner._config_path = config
    service._reaction_runner._loaded_mtime_ns = None


def test_mic_match_fires_reaction(client: Any, service: Any, tmp_path: Path) -> None:
    marker = tmp_path / "fired.txt"
    _arm_marker_reaction(service, tmp_path, marker)
    assert patch_settings(client, reactions_enabled=True).status_code == 200
    result = service.interpret("turn on the kitchen light", service.snapshot, source="mic")
    assert result.matched
    service._record(result, save_log=False)
    assert wait_file(marker) == "light_turn_on"


def test_text_match_does_not_fire(client: Any, service: Any, tmp_path: Path) -> None:
    marker = tmp_path / "fired.txt"
    _arm_marker_reaction(service, tmp_path, marker)
    assert patch_settings(client, reactions_enabled=True).status_code == 200
    result = service.test_text("turn on the kitchen light", save_log=False)
    assert result.matched
    assert wait_file(marker, timeout=0.5) is None


def test_reactions_disabled_by_default(client: Any, service: Any, tmp_path: Path) -> None:
    marker = tmp_path / "fired.txt"
    _arm_marker_reaction(service, tmp_path, marker)
    result = service.interpret("turn on the kitchen light", service.snapshot, source="mic")
    assert result.matched
    service._record(result, save_log=False)
    assert wait_file(marker, timeout=0.5) is None


def test_reaction_counters_in_pipeline_status(client: Any, service: Any, tmp_path: Path) -> None:
    marker = tmp_path / "fired.txt"
    _arm_marker_reaction(service, tmp_path, marker)
    assert patch_settings(client, reactions_enabled=True).status_code == 200
    result = service.interpret("turn on the kitchen light", service.snapshot, source="mic")
    service._record(result, save_log=False)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and service._reaction_runner.stats()["reactions_fired"] < 1:
        time.sleep(0.05)
    metrics = client.get("/api/v1/recognition/status").json()["metrics"]
    assert metrics["reactions_fired"] == 1.0
    assert metrics["reactions_failed"] == 0.0
