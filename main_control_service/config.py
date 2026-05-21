from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MainControlSettings(BaseSettings):
    config_dir: Path = Field(default=Path(__file__).resolve().parent / "config")
    repo_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    host: str = "0.0.0.0"
    port: int = 8910

    model_config = SettingsConfigDict(
        env_prefix="MAIN_CONTROL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
