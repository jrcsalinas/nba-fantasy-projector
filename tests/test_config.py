"""Tests for application settings."""

import pytest

from nbaproj.config import ConfigError, load_settings


def test_settings_use_defaults_when_env_missing():
    """With no environment values, local SQLite and UTC are used."""
    settings = load_settings({})

    assert settings.database_url == "sqlite:///nbaproj.db"
    assert settings.app_timezone == "UTC"


def test_settings_read_env_overrides():
    """DATABASE_URL and APP_TIMEZONE override the defaults."""
    env = {
        "DATABASE_URL": "postgresql://user:pw@host/db",
        "APP_TIMEZONE": "Asia/Manila",
    }

    settings = load_settings(env)

    assert settings.database_url == "postgresql://user:pw@host/db"
    assert settings.app_timezone == "Asia/Manila"


def test_settings_read_process_environment_by_default(monkeypatch):
    """Without an explicit mapping, the process environment is read."""
    monkeypatch.setenv("APP_TIMEZONE", "America/New_York")

    settings = load_settings()

    assert settings.app_timezone == "America/New_York"


def test_invalid_timezone_raises_config_error():
    """An unknown timezone name fails with a message naming APP_TIMEZONE."""
    with pytest.raises(ConfigError, match="APP_TIMEZONE"):
        load_settings({"APP_TIMEZONE": "Mars/Olympus_Mons"})
