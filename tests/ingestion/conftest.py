"""Fixtures shared by the ingestion tests."""

import pytest

from tests.ingestion.fakes import backfill_games


@pytest.fixture(name="backfilled")
def backfilled_fixture(session, schedule_payload, player_log_payload, team_log_payload):
    """The session, with the schedule stored and game 0022500001 backfilled."""
    backfill_games(session, schedule_payload, player_log_payload, team_log_payload)
    return session
