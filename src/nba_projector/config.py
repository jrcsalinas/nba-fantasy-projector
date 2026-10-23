"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

EASTERN = ZoneInfo("America/New_York")


class Settings(BaseSettings):
    """Runtime configuration; override with ``NBA_PROJECTOR_<NAME>`` env vars or ``.env``."""

    model_config = SettingsConfigDict(env_prefix="NBA_PROJECTOR_", env_file=".env", extra="ignore")

    db_path: Path = Path("data/projector.db")
    raw_dir: Path = Path("data/raw")


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
