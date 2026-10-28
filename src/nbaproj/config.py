"""Application settings read from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DATABASE_URL = "sqlite:///nbaproj.db"
DEFAULT_TIMEZONE = "UTC"


class ConfigError(ValueError):
    """Raised when a setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the application."""

    database_url: str
    app_timezone: str


def _validate_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"APP_TIMEZONE {name!r} is not a valid IANA timezone") from exc
    return name


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load settings from ``env`` (defaults to the process environment)."""
    source = os.environ if env is None else env
    return Settings(
        database_url=source.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        app_timezone=_validate_timezone(source.get("APP_TIMEZONE", DEFAULT_TIMEZONE)),
    )
