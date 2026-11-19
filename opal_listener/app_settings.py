"""Process-level configuration (environment variables), distinct from DB-stored settings."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPAL_LISTENER_", extra="ignore")

    data_dir: Path = Path("var")
    database_file: str = "opal_listener.db"
    config_dir: Path = PACKAGE_DIR / "config"
    vosk_model_path: str = "/resources/vosk/models/vosk-model-small-en-us-0.15"
    host: str = "0.0.0.0"
    port: int = 8000
    # NoDecode: pydantic-settings would otherwise require a JSON array from the environment,
    # while generated .env files write a plain comma-separated list — both forms are accepted.
    cors_origins: Annotated[list[str], NoDecode] = ["*"]
    max_upload_bytes: int = 10 * 1024 * 1024
    # reaction_log.jsonl is a transcript-bearing execution journal; it is truncated from the
    # top (oldest entries dropped) once it exceeds this size
    reaction_log_max_bytes: int = 1024 * 1024
    seed_on_empty: bool = True
    # Bearer credential for the OPAL BearerAuth contract (token_env in the shared kernel). Unset
    # disables authentication, which the kernel permits on loopback deployments; set it whenever
    # the service listens on a non-loopback interface. Never exposed in settings or exports.
    api_token: str | None = None
    # Shared with the CLI's --xpub/--xsub (see cli/client.py); no OPAL_LISTENER_ prefix so the
    # server and CLI agree on the same event bus without separate configuration.
    xpub_url: str = Field(
        default="tcp://127.0.0.1:5555",
        validation_alias=AliasChoices("xpub_url", "OPAL_EVENT_XPUB_URL", "OPAL_LISTENER_XPUB_URL"),
    )
    # opal-event HTTP REST endpoint and credentials
    opal_event_url: str = Field(
        default="http://127.0.0.1:8080",
        validation_alias=AliasChoices("opal_event_url", "OPAL_EVENT_API_URL", "OPAL_LISTENER_OPAL_EVENT_URL"),
    )
    opal_event_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("opal_event_token", "OPAL_EVENT_API_TOKEN", "OPAL_LISTENER_OPAL_EVENT_TOKEN"),
    )
    # Event transport: "http" (default for opal-event REST) or "zmq". "both" is rejected at startup:
    # the same event would be delivered twice (opal-event does not deduplicate across transports).
    event_transport: str = Field(
        default="http",
        validation_alias=AliasChoices("event_transport", "OPAL_EVENT_TRANSPORT", "OPAL_LISTENER_EVENT_TRANSPORT"),
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    return json.loads(text)
                except ValueError:
                    pass  # fall through to the comma-separated form
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_file

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def dotenv_baseline() -> dict[str, object]:
    defaults = AppSettings.model_construct()
    return {
        "OPAL_LISTENER_DATA_DIR": defaults.data_dir,
        "OPAL_LISTENER_DATABASE_FILE": defaults.database_file,
        "OPAL_LISTENER_CONFIG_DIR": defaults.config_dir,
        "OPAL_LISTENER_VOSK_MODEL_PATH": defaults.vosk_model_path,
        "OPAL_LISTENER_HOST": defaults.host,
        "OPAL_LISTENER_PORT": defaults.port,
        "OPAL_LISTENER_CORS_ORIGINS": ",".join(defaults.cors_origins),
        "OPAL_LISTENER_MAX_UPLOAD_BYTES": defaults.max_upload_bytes,
        "OPAL_LISTENER_REACTION_LOG_MAX_BYTES": defaults.reaction_log_max_bytes,
        "OPAL_LISTENER_SEED_ON_EMPTY": defaults.seed_on_empty,
        "OPAL_LISTENER_API_TOKEN": "<set-a-token>",
        "OPAL_LISTENER_XPUB_URL": defaults.xpub_url,
        "OPAL_LISTENER_OPAL_EVENT_URL": defaults.opal_event_url,
        "OPAL_LISTENER_OPAL_EVENT_TOKEN": "<set-a-token>",
        "OPAL_LISTENER_EVENT_TRANSPORT": defaults.event_transport,
    }


@lru_cache
def get_app_settings() -> AppSettings:
    return AppSettings()
