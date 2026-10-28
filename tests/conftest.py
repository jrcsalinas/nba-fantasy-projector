"""Shared fixtures for all tests."""

import json
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from nbaproj.db.models import Base
from nbaproj.db.session import create_db_engine


@pytest.fixture(name="session")
def session_fixture():
    """A session on a fresh in-memory SQLite database with all tables created."""
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Read a JSON payload from tests/fixtures."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(name="scoreboard_payload")
def scoreboard_payload_fixture() -> dict:
    """The hand-built live scoreboard payload."""
    return load_fixture("scoreboard.json")


@pytest.fixture(name="boxscore_payload")
def boxscore_payload_fixture() -> dict:
    """The hand-built regular box score payload."""
    return load_fixture("boxscore.json")


@pytest.fixture(name="overtime_payload")
def overtime_payload_fixture() -> dict:
    """The hand-built overtime box score payload."""
    return load_fixture("boxscore_overtime.json")


@pytest.fixture(name="schedule_payload")
def schedule_payload_fixture() -> dict:
    """The hand-built season schedule payload."""
    return load_fixture("schedule.json")


@pytest.fixture(name="player_log_payload")
def player_log_payload_fixture() -> dict:
    """The hand-built player game log, in resultSets format."""
    return load_fixture("gamelog_players.json")


@pytest.fixture(name="team_log_payload")
def team_log_payload_fixture() -> dict:
    """The hand-built team game log, in resultSets format."""
    return load_fixture("gamelog_teams.json")
