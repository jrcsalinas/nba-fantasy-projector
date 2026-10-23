"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

EASTERN = ZoneInfo("America/New_York")


class Settings(BaseSettings):
    """Runtime configuration; override with ``NBA_PROJECTOR_<NAME>`` env vars or ``.env``."""

    model_config = SettingsConfigDict(env_prefix="NBA_PROJECTOR_", env_file=".env", extra="ignore")

    db_path: Path = Path("data/projector.db")
    raw_dir: Path = Path("data/raw")
    roster_path: Path = Path("data/roster.json")

    yahoo_client_id: str = ""
    yahoo_client_secret: SecretStr = SecretStr("")
    yahoo_redirect_uri: str = ""
    yahoo_league_id: str = ""
    yahoo_team_id: str = ""
    yahoo_token_path: Path = Path("data/yahoo_token.json")


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
