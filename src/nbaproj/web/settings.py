"""Settings for the web app, read from environment variables.

Kept apart from ``nbaproj.config`` so the data layer and the CLI never need the web
secrets. Error messages name the variable but never print its value.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError

from nbaproj.config import ConfigError

MIN_SECRET_LENGTH = 32
MAX_OWNER_NAME_LENGTH = 64
DEFAULT_OWNER_NAME = "owner"


@dataclass(frozen=True)
class WebSettings:
    """Runtime settings for the web app. The secrets are hidden from ``repr``."""

    session_secret: str = field(repr=False)
    password_hash: str = field(repr=False)
    cookie_secure: bool = True
    owner_name: str = DEFAULT_OWNER_NAME


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _session_secret(env: Mapping[str, str]) -> str:
    secret = _required(env, "SESSION_SECRET")
    if len(secret) < MIN_SECRET_LENGTH:
        raise ConfigError(f"SESSION_SECRET must be at least {MIN_SECRET_LENGTH} characters")
    return secret


def _password_hash(env: Mapping[str, str]) -> str:
    password_hash = _required(env, "PASSWORD_HASH")
    try:
        extract_parameters(password_hash)
    except InvalidHashError:
        raise ConfigError(
            "PASSWORD_HASH is not a valid argon2 hash (make one with: "
            "python -m nbaproj.cli hash-password)"
        ) from None
    return password_hash


def _cookie_secure(env: Mapping[str, str]) -> bool:
    raw = env.get("COOKIE_SECURE", "true").strip().lower()
    if raw not in ("true", "false"):
        raise ConfigError("COOKIE_SECURE must be true or false")
    return raw == "true"


def _owner_name(env: Mapping[str, str]) -> str:
    owner = env.get("OWNER_NAME", DEFAULT_OWNER_NAME).strip()
    if not owner or len(owner) > MAX_OWNER_NAME_LENGTH:
        raise ConfigError(f"OWNER_NAME must be 1 to {MAX_OWNER_NAME_LENGTH} characters")
    return owner


def load_web_settings(env: Mapping[str, str]) -> WebSettings:
    """Read and validate the web settings from ``env``. Raises ``ConfigError``."""
    return WebSettings(
        session_secret=_session_secret(env),
        password_hash=_password_hash(env),
        cookie_secure=_cookie_secure(env),
        owner_name=_owner_name(env),
    )
