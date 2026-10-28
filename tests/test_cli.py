"""Tests for the command line: backfill and schedule."""

import getpass as getpass_module
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from nbaproj import cli
from nbaproj.cli import Clients, describe_database, main
from nbaproj.db.locks import acquire_lock
from nbaproj.db.models import Base, Game, GameLog
from nbaproj.db.session import create_db_engine
from nbaproj.ingestion.client import NbaClientError
from nbaproj.ingestion.runs import LOCK_NAME, RunResult
from nbaproj.web.security import verify_password
from tests.ingestion.fakes import FakeStatsClient

TODAY = date(2026, 10, 28)


@pytest.fixture(name="env")
def env_fixture(tmp_path):
    """Environment pointing at a fresh SQLite file with all tables created."""
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    engine = create_db_engine(url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return {"DATABASE_URL": url}


def _clients(stats=None, today=TODAY) -> Clients:
    return Clients(
        stats=lambda: stats or FakeStatsClient(),
        today=lambda _timezone_name: today,
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


def test_cli_schedule_command_and_exit_codes(env, capsys, schedule_payload):
    """schedule runs; failed and rejected runs exit non-zero."""
    stats = FakeStatsClient(schedule=schedule_payload)
    assert main(["schedule", "--season", "2025-26"], _clients(stats=stats), env) == 0
    assert _count(env, Game) == 4
    assert "schedule: succeeded" in capsys.readouterr().out

    broken = FakeStatsClient(schedule=NbaClientError("blocked"))
    assert main(["schedule"], _clients(stats=broken), env) == 1
    assert "schedule: failed" in capsys.readouterr().out

    engine = create_db_engine(env["DATABASE_URL"])
    with Session(engine) as session:
        now = datetime.now(timezone.utc)
        acquire_lock(session, LOCK_NAME, "someone-else", timedelta(hours=1), now)
    engine.dispose()
    assert main(["schedule"], _clients(stats=stats), env) == 1
    assert "schedule: rejected" in capsys.readouterr().out


def test_cli_default_backfill_season_in_august(
    env, schedule_payload, player_log_payload, team_log_payload
):
    """In the offseason the default backfill is the season just played, not the one before."""
    stats = FakeStatsClient(schedule_payload, player_log_payload, team_log_payload)

    main(["backfill"], _clients(stats=stats, today=date(2026, 8, 15)), env)

    assert stats.requests[0] == ("schedule", "2025-26")


def test_cli_today_uses_the_configured_timezone(env):
    """main asks for today in APP_TIMEZONE, and today_in returns the date in that zone."""
    asked: list[str] = []
    clients = Clients(
        stats=FakeStatsClient,
        today=lambda name: asked.append(name) or TODAY,
    )

    main(["schedule"], clients, {**env, "APP_TIMEZONE": "Pacific/Auckland"})

    assert asked == ["Pacific/Auckland"]
    assert cli.today_in("Pacific/Kiritimati") == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()


@pytest.mark.parametrize("bad", ["2025", "25-26", "2025-27", "2025-2026", "abcd-ef"])
def test_cli_rejects_a_malformed_season(env, bad):
    """--season must look like 2025-26, with the second part following the first."""
    with pytest.raises(SystemExit) as stopped:
        main(["backfill", "--season", bad], _clients(), env)

    assert stopped.value.code == 2


def test_cli_accepts_a_season_that_crosses_the_century(env, capsys):
    """1999-00 is a valid label."""
    stats = FakeStatsClient(schedule=NbaClientError("not needed"))

    main(["schedule", "--season", "1999-00"], _clients(stats=stats), env)

    assert stats.requests == [("schedule", "1999-00")]
    assert "schedule: failed" in capsys.readouterr().out


def test_cli_reports_a_configuration_error_with_exit_code_2(env, capsys):
    """An invalid APP_TIMEZONE is reported and exits with code 2."""
    assert main(["schedule"], _clients(), {**env, "APP_TIMEZONE": "Not/AZone"}) == 2
    assert "configuration error" in capsys.readouterr().out


def test_cli_continues_with_other_seasons_when_one_fails(env, capsys, monkeypatch):
    """A database error on one season is reported and the next season still runs."""
    seasons: list[str] = []

    def fake_backfill(_session, _client, season, **_kwargs):
        seasons.append(season)
        if season == "2024-25":
            raise OperationalError("insert", {}, Exception("connection lost"))
        return RunResult("succeeded", message=f"{season} ok")

    monkeypatch.setattr(cli, "backfill_season", fake_backfill)

    code = main(["backfill", "--season", "2024-25", "--season", "2025-26"], _clients(), env)

    assert seasons == ["2024-25", "2025-26"]
    assert code == 1
    out = capsys.readouterr().out
    assert "backfill: failed" in out and "connection lost" in out
    assert "backfill: succeeded - 2025-26 ok" in out


def test_cli_reports_an_unexpected_error_and_runs_the_other_seasons(env, capsys, monkeypatch):
    """A crash in one season is reported as a failure; the rest still run and are summarised."""
    seasons: list[str] = []

    def fake_backfill(_session, _client, season, **_kwargs):
        seasons.append(season)
        if season == "2024-25":
            raise RuntimeError("boom")
        return RunResult("succeeded", message=f"{season} ok")

    monkeypatch.setattr(cli, "backfill_season", fake_backfill)

    code = main(["backfill", "--season", "2024-25", "--season", "2025-26"], _clients(), env)

    assert seasons == ["2024-25", "2025-26"]
    assert code == 1
    out = capsys.readouterr().out
    assert "backfill: failed - unexpected error" in out and "boom" in out
    assert "backfill: succeeded - 2025-26 ok" in out


def test_cli_reports_a_bad_database_url_with_exit_code_2(capsys):
    """A DATABASE_URL SQLAlchemy cannot parse is a clear message, not a traceback."""
    assert main(["schedule"], _clients(), {"DATABASE_URL": "not-a-url"}) == 2
    assert "database configuration error" in capsys.readouterr().out


def test_describe_database_hides_the_password():
    """The database shown at startup never includes the password."""
    shown = describe_database("postgresql://user:secret@host/db")

    assert "secret" not in shown
    assert shown == "postgresql+psycopg://user:***@host/db"
    assert describe_database("sqlite:///nbaproj.db") == "sqlite:///nbaproj.db"


def test_cli_logs_the_database_it_uses(env, caplog):
    """Running from the wrong folder shows up at once: the log names the database."""
    caplog.set_level(logging.INFO, logger="nbaproj.cli")

    main(["schedule"], _clients(), env)

    assert f"database: {env['DATABASE_URL']}" in [r.getMessage() for r in caplog.records]


def _type_passwords(monkeypatch, *answers: str) -> None:
    """Answer the password prompts (password, then its confirmation) without a terminal."""
    replies = iter(answers)
    monkeypatch.setattr(getpass_module, "getpass", lambda prompt="": next(replies))


def test_cli_hash_password_prints_a_verifiable_hash(monkeypatch, tmp_path, capsys):
    """The command prints only the hash, never the password, and needs no database."""
    monkeypatch.chdir(tmp_path)
    _type_passwords(monkeypatch, "my secret password", "my secret password")

    code = main(["hash-password"], env={})

    captured = capsys.readouterr()
    assert code == 0
    assert verify_password(captured.out.strip(), "my secret password") is True
    assert "my secret password" not in captured.out + captured.err
    assert not list(tmp_path.glob("*.db"))


@pytest.mark.parametrize(
    ("answers", "reason"),
    [(("one", "two"), "do not match"), (("", ""), "empty")],
)
def test_cli_hash_password_refuses_a_mismatch_or_an_empty_password(
    monkeypatch, capsys, answers, reason
):
    """A typo or an empty password gives exit code 2, an explanation, and no hash."""
    _type_passwords(monkeypatch, *answers)

    code = main(["hash-password"], env={})

    captured = capsys.readouterr()
    assert code == 2
    assert reason in captured.err
    assert "$argon2" not in captured.out
