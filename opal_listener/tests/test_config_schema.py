"""Schema validation for YAML configuration files and bundles."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from opal_listener.app_settings import PACKAGE_DIR
from opal_listener.models.config_schemas import CONFIG_FILES, ConfigBundle
from opal_listener.service.config_store import ConfigFileError, bundle_to_files, load_config_dir
from opal_listener.service.runtime_config import compile_bundle

CONFIG_DIR = PACKAGE_DIR / "config"


@pytest.mark.parametrize("filename", list(CONFIG_FILES))
def test_shipped_yaml_file_matches_schema(filename: str) -> None:
    schema, key = CONFIG_FILES[filename]
    data = yaml.safe_load((CONFIG_DIR / filename).read_text())
    parsed = schema.model_validate(data)
    assert getattr(parsed, key) is not None


def test_shipped_config_compiles_without_errors() -> None:
    report, snapshot = compile_bundle(load_config_dir(CONFIG_DIR))
    assert report.ok, report.errors
    assert snapshot is not None
    assert snapshot.profile_name == "default"
    assert snapshot.wakewords == ("computer",)


def test_every_shipped_profile_compiles() -> None:
    bundle = load_config_dir(CONFIG_DIR)
    for profile in bundle.profiles:
        variant = bundle.model_copy(deep=True)
        for p in variant.profiles:
            p.is_active = p.name == profile.name
        report, snapshot = compile_bundle(variant)
        assert report.ok, (profile.name, report.errors)
        assert snapshot is not None and snapshot.profile_name == profile.name


def test_bundle_yaml_roundtrip(tmp_path: Path) -> None:
    bundle = load_config_dir(CONFIG_DIR)
    for filename, text in bundle_to_files(bundle).items():
        (tmp_path / filename).write_text(text)
    assert load_config_dir(tmp_path) == bundle


def _write(tmp_path: Path, filename: str, content: str) -> Path:
    (tmp_path / filename).write_text(content)
    return tmp_path


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("wakewords.yaml", "wakewords: [{name: computer, confidence_threshold: 1.5}]", "less than or equal"),
        ("wakewords.yaml", "wakewords: [{name: computer, colour: red}]", "Extra inputs"),
        ("intents.yaml", "intents: [{phrases: [hi]}]", "Field required"),
        ("slots.yaml", "slots: [{name: room, values: [{aliases: [x]}]}]", "Field required"),
        ("settings.yaml", "settings: {cooldown_ms: [1, 2]}", "settings"),
        ("stt.yaml", "stt: - not a mapping", "invalid YAML"),
    ],
)
def test_schema_errors_are_reported_with_file_names(tmp_path: Path, filename: str, content: str, message: str) -> None:
    with pytest.raises(ConfigFileError) as exc:
        load_config_dir(_write(tmp_path, filename, content))
    assert any(filename in e and message in e for e in exc.value.errors), exc.value.errors


@pytest.mark.parametrize(
    ("bundle", "message"),
    [
        ({"intents": [{"name": "a", "phrases": ["x {missing}"]}]}, "unknown slot"),
        ({"intents": [{"name": "a"}, {"name": "a"}]}, "duplicate name"),
        ({"wakewords": [{"name": "Computer"}, {"name": "computer"}]}, "duplicate name"),
        ({"slots": [{"name": "s", "values": [{"value": "a"}, {"value": "a"}]}]}, "duplicate value"),
        ({"profiles": [{"name": "a", "is_active": True}, {"name": "b", "is_active": True}]}, "more than one active"),
        ({"profiles": [{"name": "a", "confidence_thresholds": {"intent": 2}}]}, "confidence_thresholds"),
        ({"profiles": [{"name": "a", "confidence_thresholds": {"typo": 0.5}}]}, "confidence_thresholds"),
        ({"profiles": [{"name": "a", "vad_settings": {"silence_end_ms": 1}}]}, "vad_settings"),
        ({"profiles": [{"name": "a", "model_paths": {"other": "/x"}}]}, "model_paths"),
        ({"settings": {"cooldown_ms": -5}}, "cooldown_ms"),
        ({"settings": {"unknown_key": 1}}, "unknown setting"),
        ({"settings": {"stt_mode": "grammar"}}, "belongs in stt.yaml"),
        ({"stt": {"stt_mode": "fancy"}}, "must be one of"),
    ],
)
def test_bundle_semantic_errors(bundle: dict, message: str) -> None:
    report, snapshot = compile_bundle(ConfigBundle.model_validate(bundle))
    assert snapshot is None
    assert any(message in e for e in report.errors), report.errors


def test_unknown_profile_references_are_warnings() -> None:
    report, snapshot = compile_bundle(
        ConfigBundle.model_validate({"profiles": [{"name": "p", "is_active": True, "enabled_intents": ["ghost"]}]})
    )
    assert snapshot is not None
    assert any("ghost" in w for w in report.warnings)
