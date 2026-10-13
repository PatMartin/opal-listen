"""Process-level configuration (environment variables), distinct from DB-stored settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPAL_LISTENER_", env_file=".env", extra="ignore")

    data_dir: Path = Path("var")
    database_file: str = "opal_listener.db"
    backup_dir_name: str = "backups"
    config_dir: Path = PACKAGE_DIR / "config"
    vosk_model_path: str = "/nabu/ears/vosk/models/vosk-model-small-en-us-0.15"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]
    max_upload_bytes: int = 10 * 1024 * 1024
    seed_on_empty: bool = True
    # Shared with the CLI's --xpub/--xsub (see cli/client.py); no OPAL_LISTENER_ prefix so the
    # server and CLI agree on the same event bus without separate configuration.
    xpub_url: str = Field(default="tcp://127.0.0.1:5559", validation_alias="OPAL_EVENT_XPUB_URL")

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_file

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / self.backup_dir_name

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_app_settings() -> AppSettings:
    return AppSettings()
