"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import PositiveFloat
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for Notes Facade."""

    obsidian_api_key: str
    obsidian_api_url: str
    obsidian_connect_timeout_seconds: PositiveFloat
    obsidian_read_timeout_seconds: PositiveFloat
    obsidian_write_timeout_seconds: PositiveFloat
    obsidian_pool_timeout_seconds: PositiveFloat
    projects_config_path: Path
    vault_ro_path: Path
    review_inbox_days: int
    review_stale_active_days: int
    tz: str
    puid: int
    pgid: int
    kasm_password: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache settings as the single env access point."""
    return Settings()  # type: ignore[call-arg]
