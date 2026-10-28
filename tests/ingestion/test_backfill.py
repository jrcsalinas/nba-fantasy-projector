"""Tests for the season backfill: parsing, bulk storing and the run."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from nbaproj.db.locks import acquire_lock
from nbaproj.db.models import Game, GameLog, JobLock, Player, RefreshLog
from nbaproj.ingestion.backfill import (
    backfill_season,
    build_backfill_lines,
    parse_result_set_rows,
    store_backfill,
)
from nbaproj.ingestion.client import NbaClientError
from nbaproj.ingestion.loader import store_boxscore
from nbaproj.ingestion.parsing import PlayerLine, parse_boxscore
from nbaproj.ingestion.runs import LOCK_NAME
from nbaproj.ingestion.schedule import parse_schedule, store_schedule
from tests.ingestion.fakes import FakeStatsClient

G1 = "0022500001"
G2 = "0022500010"
NOW = datetime(2025, 10, 29, 5, 0, tzinfo=timezone.utc)
SEASON = "2025-26"


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _scheduled_lines(session, schedule_payload, player_log_payload, team_log_payload):
    """Store the schedule (game G1 only) and return the backfill lines for G1 and G2."""
    store_schedule(session, parse_schedule(schedule_payload))
    return build_backfill_lines(player_log_payload, team_log_payload)


def test_parse_result_set_rows_maps_headers_to_values(player_log_payload):
    """Each row becomes a dict keyed by the result set's headers."""
    rows = parse_result_set_rows(player_log_payload)

    assert len(rows) == 6
    assert rows[0]["PLAYER_NAME"] == "Alex Home"
    assert (rows[0]["PLAYER_ID"], rows[0]["MIN"], rows[0]["FG3M"]) == (1001, 36, 3)


def test_build_backfill_lines_joins_team_totals_for_usage(
    player_log_payload, team_log_payload
):
    """Lines are grouped by game; usage uses the team's totals, with overtime minutes."""
    by_game = build_backfill_lines(player_log_payload, team_log_payload)

    assert list(by_game) == [G1, G2]
    assert [line.player_id for line in by_game[G1]] == [1001, 1002, 2001, 2003]

    alex = by_game[G1][0]
    assert replace(alex, usage_pct=None) == PlayerLine(
        player_id=1001,
        name="Alex Home",
        team_id=1610612747,
        minutes=36.0,
        pts=28,
        reb=7,
        ast=6,
        stl=2,
        blk=1,
        tpm=3,
        fgm=10,
        fga=20,
        ftm=5,
        fta=5,
        tov=4,
        usage_pct=None,
    )
    assert alex.usage_pct == pytest.approx(30.9145, abs=1e-3)

    finn = by_game[G1][3]
    assert (finn.minutes, finn.usage_pct) == (0.0, None)

    gus = by_game[G2][0]
    expected = 100 * (26 + 0.44 * 8 + 5) * (265 / 5) / (44 * (95 + 0.44 * 28 + 13))
    assert gus.usage_pct == pytest.approx(expected)


def test_store_backfill_inserts_lines_for_scheduled_games(
    session, schedule_payload, player_log_payload, team_log_payload
):
    """Lines for a scheduled game are stored with their players, tagged as backfill."""
    lines = _scheduled_lines(session, schedule_payload, player_log_payload, team_log_payload)

    result = store_backfill(session, lines)

    assert result.lines_stored == 4
    assert _count(session, GameLog) == 4
    assert _count(session, Player) == 4
    alex = session.get(GameLog, (G1, 1001))
    assert (alex.source, alex.minutes, alex.pts) == ("backfill", 36.0, 28)
    assert alex.usage_pct == pytest.approx(30.9145, abs=1e-3)


def test_store_backfill_twice_leaves_the_same_rows(
    session, schedule_payload, player_log_payload, team_log_payload
):
    """Running the same backfill again changes nothing."""
    lines = _scheduled_lines(session, schedule_payload, player_log_payload, team_log_payload)
    store_backfill(session, lines)

    result = store_backfill(session, lines)

    assert result.lines_stored == 4
    assert _count(session, GameLog) == 4
    assert _count(session, Player) == 4


def test_store_backfill_never_overwrites_live_lines(
    session, schedule_payload, player_log_payload, team_log_payload, boxscore_payload
):
    """Live lines have decimal minutes, so a backfill must not replace them."""
    lines = _scheduled_lines(session, schedule_payload, player_log_payload, team_log_payload)
    store_boxscore(session, parse_boxscore(boxscore_payload))

    result = store_backfill(session, lines)

    assert (result.lines_stored, result.lines_skipped_live) == (0, 4)
    ben = session.get(GameLog, (G1, 1002))
    assert (ben.source, ben.minutes) == ("live", 33.25)
    assert _count(session, GameLog) == 5


def test_store_backfill_skips_and_counts_games_missing_from_the_schedule(
    session, schedule_payload, player_log_payload, team_log_payload
):
    """Lines for a game that is not in the schedule are counted and not stored."""
    lines = _scheduled_lines(session, schedule_payload, player_log_payload, team_log_payload)

    result = store_backfill(session, lines)

    assert result.lines_skipped_unscheduled == 2
    assert session.get(GameLog, (G2, 3001)) is None
    assert session.get(Player, 3001) is None


def _clock() -> datetime:
    return NOW


def _backfill(session, client):
    return backfill_season(session, client, SEASON, trigger="cli", clock=_clock)


def test_backfill_season_loads_schedule_then_logs_and_records_the_run(
    session, schedule_payload, player_log_payload, team_log_payload
):
    """The schedule is stored first, then the lines; the run is logged; the lock is freed."""
    client = FakeStatsClient(schedule_payload, player_log_payload, team_log_payload)

    result = _backfill(session, client)

    assert result.status == "succeeded"
    assert (result.games_scheduled, result.lines_stored) == (4, 4)
    assert result.lines_skipped_unscheduled == 2
    assert client.requests == [
        ("schedule", SEASON),
        ("players", SEASON),
        ("teams", SEASON),
    ]
    assert (_count(session, Game), _count(session, GameLog)) == (4, 4)
    log = session.scalars(select(RefreshLog)).one()
    assert (log.trigger, log.status, log.rows_upserted) == ("cli", "succeeded", 8)
    assert SEASON in log.message
    assert _count(session, JobLock) == 0


def test_backfill_is_rejected_when_lock_is_held(
    session, schedule_payload, player_log_payload, team_log_payload
):
    """A held lock rejects the run before any fetch, and the other holder keeps it."""
    acquire_lock(session, LOCK_NAME, "someone-else", timedelta(hours=1), NOW)
    client = FakeStatsClient(schedule_payload, player_log_payload, team_log_payload)

    result = _backfill(session, client)

    assert result.status == "rejected"
    assert not client.requests
    assert session.get(JobLock, LOCK_NAME).holder == "someone-else"
    assert session.scalars(select(RefreshLog.status)).one() == "rejected"


def test_backfill_reports_failed_and_rolls_back_the_season_on_error(
    session, schedule_payload, team_log_payload
):
    """A failure after the schedule is stored undoes the whole season."""
    client = FakeStatsClient(schedule_payload, NbaClientError("rate limited"), team_log_payload)

    result = _backfill(session, client)

    assert result.status == "failed"
    assert "rate limited" in result.message
    assert (_count(session, Game), _count(session, GameLog)) == (0, 0)
    log = session.scalars(select(RefreshLog)).one()
    assert (log.status, "rate limited" in log.message) == ("failed", True)
    assert _count(session, JobLock) == 0


def test_backfill_marks_the_log_failed_and_frees_the_lock_on_unexpected_errors(session):
    """An unexpected exception still propagates, but the log row is never left running."""
    client = FakeStatsClient(schedule=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        _backfill(session, client)

    log = session.scalars(select(RefreshLog)).one()
    assert (log.status, "boom" in log.message) == ("failed", True)
    assert _count(session, JobLock) == 0
