"""Fixtures for the refresh and banner tests: a fixed clock, a log seeder, signed-in clients."""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nbaproj.roster.directory import PlayerDirectory
from nbaproj.web.app import create_app
from nbaproj.web.settings import load_web_settings

from tests.ingestion.fakes import FakeClient, only_games
from tests.web.helpers import add_live_run, add_log, log_in

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


@pytest.fixture(name="make_client")
def make_client_fixture(web_env, engine, scoreboard_payload):
    """A factory for clients of an app with a database, the clock fixed at NOW, and no network.

    The NBA client is a fake with an empty scoreboard, so a refresh that a test starts by
    the way never reaches the real service; tests that care replace it.
    """
    with ExitStack() as stack:

        def make(signed_in: bool = True, **env_overrides: str) -> TestClient:
            settings = load_web_settings({**web_env, **env_overrides})
            app = create_app(settings, engine=engine, directory=PlayerDirectory([(1, "A B")]))
            app.state.clock = lambda: NOW
            app.state.nba_client = FakeClient(only_games(scoreboard_payload), {})
            client = stack.enter_context(TestClient(app))
            if signed_in:
                assert log_in(client).status_code == 303
            return client

        yield make


@pytest.fixture(name="seed_log")
def seed_log_fixture(engine):
    """Add refresh log rows: ``seed_log("succeeded", 3 * HOUR)`` began three hours before NOW."""

    def seed(status: str, ago: timedelta, message: str | None = None):
        with Session(engine) as session:
            add_log(session, NOW, status, ago, message)

    return seed


@pytest.fixture(name="seed_live_run")
def seed_live_run_fixture(engine):
    """Add a run in progress (a running row and its lock) that began ``ago`` before NOW."""

    def seed(ago: timedelta):
        with Session(engine) as session:
            add_live_run(session, NOW, ago)

    return seed
