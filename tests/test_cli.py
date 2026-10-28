"""Tests for the command line: backfill, schedule and catch-up."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nbaproj.cli import Clients, main
from nbaproj.db.locks import acquire_lock
from nbaproj.db.models import Base, Game, GameLog
from nbaproj.db.session import create_db_engine
from nbaproj.ingestion.client import NbaClientError
from nbaproj.ingestion.runs import LOCK_NAME
from tests.ingestion.fakes import FakeClient, FakeStatsClient

TODAY = date(2026, 10, 28)


@pytest.fixture(name="env")
def env_fixture(tmp_path):
    """Environment pointing at a fresh SQLite file with all tables created."""
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    engine = create_db_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return {"DATABASE_URL": url}


def _clients(stats=None, live=None) -> Clients:
    return Clients(
        stats=lambda: stats or FakeStatsClient(),
        live=lambda: live or FakeClient({}, {}),
        today=lambda: TODAY,
    )


def _count(env, model) -> int:
    engine = create_db_engine(env["DATABASE_URL"])
    try:
        with Session(engine) as session:
            return session.scalar(select(func.count()).select_from(model))
    finally:
        engine.dispose()


def test_cli_backfill_runs_for_the_given_seasons_and_prints_a_summary(
    env, capsys, schedule_payload, player_log_payload, team_log_payload
):
    """backfill --season loads that season and prints one summary line."""
    stats = FakeStatsClient(schedule_payload, player_log_payload, team_log_payload)

    code = main(["backfill", "--season", "2025-26"], _clients(stats=stats), env)

    assert code == 0
    assert "backfill: succeeded" in capsys.readouterr().out
    assert stats.requests[0] == ("schedule", "2025-26")
    assert (_count(env, Game), _count(env, GameLog)) == (4, 4)


def test_cli_default_seasons(env, schedule_payload, player_log_payload, team_log_payload):
    """backfill defaults to the previous season and schedule to the current one."""
    stats = FakeStatsClient(schedule_payload, player_log_payload, team_log_payload)

    main(["backfill"], _clients(stats=stats), env)
    main(["schedule"], _clients(stats=stats), env)

    assert [r for r in stats.requests if r[0] == "schedule"] == [
        ("schedule", "2025-26"),
        ("schedule", "2026-27"),
    ]


def test_cli_schedule_and_catch_up_commands_and_exit_codes(
    env, capsys, schedule_payload
):
    """schedule and catch-up run; failed and rejected runs exit non-zero."""
    stats = FakeStatsClient(schedule=schedule_payload)
    assert main(["schedule", "--season", "2025-26"], _clients(stats=stats), env) == 0
    assert _count(env, Game) == 4

    # Every stored game tips off before TODAY by more than 14 days, so nothing is due.
    assert main(["catch-up"], _clients(), env) == 0
    assert "catch-up: skipped" in capsys.readouterr().out

    broken = FakeStatsClient(schedule=NbaClientError("blocked"))
    assert main(["schedule"], _clients(stats=broken), env) == 1
    assert "schedule: failed" in capsys.readouterr().out

    engine = create_db_engine(env["DATABASE_URL"])
    with Session(engine) as session:
        now = datetime.now(timezone.utc)
        acquire_lock(session, LOCK_NAME, "someone-else", timedelta(hours=1), now)
    engine.dispose()
    assert main(["catch-up"], _clients(), env) == 1
    assert "catch-up: rejected" in capsys.readouterr().out
