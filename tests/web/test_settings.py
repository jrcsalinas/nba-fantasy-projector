"""Tests for the web settings."""

import pytest

from nbaproj.config import ConfigError
from nbaproj.web.settings import load_web_settings

from tests.web.conftest import TEST_SECRET


def test_web_settings_reads_the_required_values(web_env, password_hash):
    """The secret, the hash, the cookie flag, and the owner name come from the environment."""
    settings = load_web_settings(web_env)

    assert settings.session_secret == TEST_SECRET
    assert settings.password_hash == password_hash
    assert settings.cookie_secure is False
    assert settings.owner_name == "josh"


def test_web_settings_defaults(web_env):
    """Cookies are Secure unless turned off, and the owner is called "owner"."""
    del web_env["COOKIE_SECURE"]
    del web_env["OWNER_NAME"]

    settings = load_web_settings(web_env)

    assert settings.cookie_secure is True
    assert settings.owner_name == "owner"


@pytest.mark.parametrize(
    ("change", "variable"),
    [
        ({"SESSION_SECRET": None}, "SESSION_SECRET"),
        ({"SESSION_SECRET": "x" * 31}, "SESSION_SECRET"),
        ({"PASSWORD_HASH": None}, "PASSWORD_HASH"),
        ({"PASSWORD_HASH": "not-an-argon2-hash"}, "PASSWORD_HASH"),
        ({"COOKIE_SECURE": "maybe"}, "COOKIE_SECURE"),
        ({"OWNER_NAME": ""}, "OWNER_NAME"),
        ({"OWNER_NAME": "x" * 65}, "OWNER_NAME"),
    ],
    ids=lambda value: str(value)[:40],
)
def test_web_settings_rejects_bad_values(web_env, change, variable):
    """A missing, short, or malformed value stops startup and names the variable."""
    for name, value in change.items():
        if value is None:
            del web_env[name]
        else:
            web_env[name] = value

    with pytest.raises(ConfigError, match=variable):
        load_web_settings(web_env)


def test_settings_errors_and_repr_never_contain_the_secret(web_env):
    """Secrets must not leak through an error message or a printed settings object."""
    web_env["SESSION_SECRET"] = "SHORT-SECRET-VALUE"
    with pytest.raises(ConfigError) as short_secret:
        load_web_settings(web_env)
    assert "SHORT-SECRET-VALUE" not in str(short_secret.value)

    web_env["SESSION_SECRET"] = TEST_SECRET
    web_env["PASSWORD_HASH"] = "garbage-hash-VALUE"
    with pytest.raises(ConfigError) as bad_hash:
        load_web_settings(web_env)
    assert "garbage-hash-VALUE" not in str(bad_hash.value)

    web_env["PASSWORD_HASH"] = "$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo"
    shown = repr(load_web_settings(web_env))
    assert TEST_SECRET not in shown and "$argon2id" not in shown


def test_web_settings_reads_the_timezone(web_env):
    """Pages show times in APP_TIMEZONE, which defaults to UTC."""
    assert load_web_settings(web_env).timezone == "UTC"

    web_env["APP_TIMEZONE"] = "Pacific/Auckland"

    assert load_web_settings(web_env).timezone == "Pacific/Auckland"


def test_web_settings_rejects_a_bad_timezone(web_env):
    """A timezone name that does not exist stops startup and names the variable."""
    web_env["APP_TIMEZONE"] = "Not/AZone"

    with pytest.raises(ConfigError, match="APP_TIMEZONE"):
        load_web_settings(web_env)


REFRESH_TOKEN = "refresh-token-" + "r" * 20


def test_refresh_token_is_optional_and_off_by_default(web_env):
    """Without REFRESH_TOKEN there is no token, so bearer calls can never match."""
    assert load_web_settings(web_env).refresh_token is None


def test_refresh_token_is_read_from_the_environment(web_env):
    """A long enough REFRESH_TOKEN is kept as it is."""
    web_env["REFRESH_TOKEN"] = REFRESH_TOKEN

    assert load_web_settings(web_env).refresh_token == REFRESH_TOKEN


@pytest.mark.parametrize("value", ["", "x" * 31])
def test_a_short_refresh_token_is_rejected_without_echoing_it(web_env, value):
    """A set but too short token stops startup, names the variable, and never shows the value."""
    web_env["REFRESH_TOKEN"] = value

    with pytest.raises(ConfigError) as error:
        load_web_settings(web_env)

    assert "REFRESH_TOKEN" in str(error.value)
    assert value == "" or value not in str(error.value)


def test_the_refresh_token_is_not_in_the_settings_repr(web_env):
    """Printing the settings (a log line, a traceback) never prints the token."""
    web_env["REFRESH_TOKEN"] = REFRESH_TOKEN

    assert REFRESH_TOKEN not in repr(load_web_settings(web_env))
