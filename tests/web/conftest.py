"""Fixtures for the web tests: fake secrets, a fast password hash, and settings."""

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from nbaproj.db.models import Base
from nbaproj.db.session import create_db_engine
from nbaproj.web.app import create_app
from nbaproj.web.settings import load_web_settings

TEST_PASSWORD = "correct horse battery staple"
TEST_SECRET = "test-session-secret-" + "x" * 20


@pytest.fixture(name="password_hash", scope="session")
def password_hash_fixture() -> str:
    """An argon2 hash of TEST_PASSWORD, made with the cheapest parameters so tests are fast."""
    return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(TEST_PASSWORD)


@pytest.fixture(name="web_env")
def web_env_fixture(password_hash: str) -> dict[str, str]:
    """A complete, valid web environment (plain-http cookies, as in local development)."""
    return {
        "SESSION_SECRET": TEST_SECRET,
        "PASSWORD_HASH": password_hash,
        "COOKIE_SECURE": "false",
        "OWNER_NAME": "josh",
    }


@pytest.fixture(name="app")
def app_fixture(web_env):
    """The app, built from the test settings."""
    return create_app(load_web_settings(web_env))


@pytest.fixture(name="client")
def client_fixture(app):
    """A test client; a server error in a route is raised so the test shows it."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(name="engine")
def engine_fixture(tmp_path):
    """A SQLite file database with all tables, shared by the requests of one test."""
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'web.db').as_posix()}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()
