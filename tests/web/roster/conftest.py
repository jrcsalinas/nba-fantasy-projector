"""Fixtures for the roster page tests: a small player directory, a signed-in client, seeding."""

from contextlib import ExitStack
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nbaproj.roster.directory import PlayerDirectory
from nbaproj.roster.service import RosterService
from nbaproj.web.app import create_app
from nbaproj.web.settings import load_web_settings

from tests.web.helpers import log_in

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
PLAYERS = [
    (1, "Luka Dončić"),
    (2, "Jayson Tatum"),
    (3, "Nikola Jokić"),
    (4, "De'Aaron Fox"),
    (5, "<b>Evil</b> Name"),
]


@pytest.fixture(name="directory")
def directory_fixture() -> PlayerDirectory:
    """A small directory with an accent, an apostrophe, and a name that is HTML."""
    return PlayerDirectory(PLAYERS)


@pytest.fixture(name="make_client")
def make_client_fixture(web_env, engine, directory):
    """A factory for signed-in clients of an app with a database and the small directory."""
    with ExitStack() as stack:

        def make(
            directory_override: PlayerDirectory | None = None, **env_overrides: str
        ) -> TestClient:
            settings = load_web_settings({**web_env, **env_overrides})
            app = create_app(settings, engine=engine, directory=directory_override or directory)
            client = stack.enter_context(TestClient(app))
            assert log_in(client).status_code == 303
            return client

        yield make


@pytest.fixture(name="roster_client")
def roster_client_fixture(make_client) -> TestClient:
    """A signed-in client for the owner "josh"."""
    return make_client()


@pytest.fixture(name="seed")
def seed_fixture(engine, directory):
    """Write roster changes straight to the database: ``seed(("add", 1, when), ...)``."""

    def seed(*actions, owner: str = "josh") -> None:
        with Session(engine) as session:
            for action, player_id, when in actions:
                service = RosterService(session, owner, directory, clock=lambda when=when: when)
                getattr(service, action)(player_id)
            session.commit()

    return seed
