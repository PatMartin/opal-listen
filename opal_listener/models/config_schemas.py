"""Schemas for the YAML configuration files and the export/import bundle."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CONFIG_VERSION = 1

SettingScalar = str | int | float | bool


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WakewordSpec(_Spec):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)


class IntentSpec(_Spec):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    phrases: list[str] = []


class SlotValueSpec(_Spec):
    value: str = Field(min_length=1, max_length=200)
    aliases: list[str] = []


class SlotSpec(_Spec):
    name: str = Field(min_length=1, max_length=100)
    slot_type: str = "list"
    description: str | None = Field(default=None, max_length=500)
    values: list[SlotValueSpec] = []


class ProfileSpec(_Spec):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    is_active: bool = False
    wakeword_names: list[str] = []
    confidence_thresholds: dict[str, Any] = {}
    vad_settings: dict[str, Any] = {}
    enabled_intents: list[str] = []
    enabled_slots: list[str] = []
    model_paths: dict[str, str] = {}


class WakewordsFile(_Spec):
    version: int = CONFIG_VERSION
    wakewords: list[WakewordSpec] = []


class IntentsFile(_Spec):
    version: int = CONFIG_VERSION
    intents: list[IntentSpec] = []


class SlotsFile(_Spec):
    version: int = CONFIG_VERSION
    slots: list[SlotSpec] = []


class ProfilesFile(_Spec):
    version: int = CONFIG_VERSION
    profiles: list[ProfileSpec] = []


class SettingsFile(_Spec):
    version: int = CONFIG_VERSION
    settings: dict[str, SettingScalar] = {}


class SttFile(_Spec):
    version: int = CONFIG_VERSION
    stt: dict[str, SettingScalar] = {}


# file name -> (schema, top-level key)
CONFIG_FILES: dict[str, tuple[type[_Spec], str]] = {
    "wakewords.yaml": (WakewordsFile, "wakewords"),
    "stt.yaml": (SttFile, "stt"),
    "intents.yaml": (IntentsFile, "intents"),
    "slots.yaml": (SlotsFile, "slots"),
    "profiles.yaml": (ProfilesFile, "profiles"),
    "settings.yaml": (SettingsFile, "settings"),
}


class ConfigBundle(_Spec):
    """Complete declarative configuration (union of all YAML files)."""

    version: int = CONFIG_VERSION
    wakewords: list[WakewordSpec] = []
    stt: dict[str, SettingScalar] = {}
    intents: list[IntentSpec] = []
    slots: list[SlotSpec] = []
    profiles: list[ProfileSpec] = []
    settings: dict[str, SettingScalar] = {}
