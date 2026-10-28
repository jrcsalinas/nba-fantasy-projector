"""Tests for the season schedule: parsing, storing and refreshing."""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from nbaproj.db.models import Game, RefreshLog, Team
from nbaproj.ingestion.client import NbaClientError
from nbaproj.ingestion.parsing import TeamInfo
from nbaproj.ingestion.schedule import (
    parse_schedule,
    previous_season,
    refresh_schedule,
    run_schedule_refresh,
    season_for_date,
    store_schedule,
)
from tests.ingestion.fakes import FakeStatsClient


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _game(payload: dict, game_id: str) -> dict:
    for day in payload["leagueSchedule"]["gameDates"]:
        for game in day["games"]:
            if game["gameId"] == game_id:
                return game
    raise KeyError(game_id)


def test_parse_schedule_keeps_countable_games_with_real_teams(schedule_payload):
    """Preseason, All-Star, Cup final and TBD-team games are dropped."""
    games = parse_schedule(schedule_payload)

    assert [g.game_id for g in games] == [
        "0022500001",
        "0022500002",
        "0052500001",
        "0042500102",
    ]
    assert [g.status for g in games] == ["final", "scheduled", "scheduled", "scheduled"]
    first = games[0]
    assert first.tip_off_utc == datetime(2025, 10, 21, 23, 30, tzinfo=timezone.utc)
    assert first.home == TeamInfo(1610612747, "LAL", "Lakers", "Los Angeles")
    assert first.away == TeamInfo(1610612744, "GSW", "Warriors", "Golden State")


def test_store_schedule_inserts_teams_and_games(session, schedule_payload):
    """The kept games and their teams are stored."""
    written = store_schedule(session, parse_schedule(schedule_payload))

    assert written == 4
    assert _count(session, Team) == 8
    assert _count(session, Game) == 4
    playoff = session.get(Game, "0042500102")
    assert (playoff.season, playoff.game_type, playoff.status) == (
        "2025-26",
        "playoffs",
        "scheduled",
    )
    assert (playoff.home_team_id, playoff.away_team_id) == (1610612760, 1610612762)


def test_store_schedule_twice_leaves_the_same_rows_and_updates_tip_off(
    session, schedule_payload
):
    """A second load adds nothing and picks up a moved tip-off time and new status."""
    store_schedule(session, parse_schedule(schedule_payload))
    moved = _game(schedule_payload, "0022500002")
    moved["gameDateTimeUTC"] = "2025-10-23T01:00:00Z"
    moved["gameStatus"] = 3

    store_schedule(session, parse_schedule(schedule_payload))

    assert _count(session, Team) == 8
    assert _count(session, Game) == 4
    game = session.get(Game, "0022500002")
    assert game.tip_off_utc == datetime(2025, 10, 23, 1, 0, tzinfo=timezone.utc)
    assert game.status == "final"


def test_store_schedule_never_downgrades_a_final_game(session, schedule_payload):
    """A stale schedule cannot move a finished game back to scheduled."""
    store_schedule(session, parse_schedule(schedule_payload))
    _game(schedule_payload, "0022500001")["gameStatus"] = 1

    store_schedule(session, parse_schedule(schedule_payload))

    assert session.get(Game, "0022500001").status == "final"


def test_refresh_schedule_fetches_parses_and_stores(session, schedule_payload):
    """refresh_schedule asks the client for the season and commits the games."""
    client = FakeStatsClient(schedule=schedule_payload)

    written = refresh_schedule(session, client, "2025-26")
    session.rollback()

    assert written == 4
    assert client.requests == [("schedule", "2025-26")]
    assert _count(session, Game) == 4


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 28), "2026-27"),
        (date(2026, 10, 1), "2026-27"),
        (date(2027, 4, 1), "2026-27"),
        (date(2026, 9, 30), "2025-26"),
    ],
)
def test_season_for_date_starts_a_season_in_october(day, expected):
    """From October a new season label begins; before that it is the previous one."""
    assert season_for_date(day) == expected


def test_previous_season_steps_back_one_year():
    """The label before 2026-27 is 2025-26, and the century rolls over."""
    assert previous_season("2026-27") == "2025-26"
    assert previous_season("2000-01") == "1999-00"


def test_run_schedule_refresh_logs_the_run(session, schedule_payload):
    """The locked run stores the schedule and records it in the refresh log."""
    client = FakeStatsClient(schedule=schedule_payload)

    result = run_schedule_refresh(session, client, "2025-26", trigger="cli")

    assert (result.status, result.rows_upserted) == ("succeeded", 4)
    log = session.scalars(select(RefreshLog)).one()
    assert (log.trigger, log.status, log.rows_upserted) == ("cli", "succeeded", 4)


def test_run_schedule_refresh_reports_failed_and_stores_nothing(session):
    """A fetch error is reported as a failed run, not raised."""
    client = FakeStatsClient(schedule=NbaClientError("blocked"))

    result = run_schedule_refresh(session, client, "2025-26", trigger="cli")

    assert result.status == "failed"
    assert "blocked" in result.message
    assert _count(session, Game) == 0
    assert session.scalars(select(RefreshLog.status)).one() == "failed"
