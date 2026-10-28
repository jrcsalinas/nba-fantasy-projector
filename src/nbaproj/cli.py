"""Command line for the laptop: backfill a season, refresh the schedule.

Run from the project root, for example::

    .venv/Scripts/python.exe -m nbaproj.cli backfill --season 2025-26

The database comes from ``DATABASE_URL`` (see ``nbaproj.config``). Create the tables
first with ``alembic upgrade head``.
"""

import argparse
import logging
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nbaproj.config import ConfigError, load_settings
from nbaproj.db.session import create_db_engine, normalize_database_url
from nbaproj.ingestion.backfill import backfill_season
from nbaproj.ingestion.client import StatsClient, StatsNbaClient
from nbaproj.ingestion.runs import RunResult, guarded
from nbaproj.ingestion.schedule import (
    last_completed_season,
    run_schedule_refresh,
    season_for_date,
)

logger = logging.getLogger(__name__)

TRIGGER = "cli"
FAILING_STATUSES = frozenset({"failed", "rejected"})


def describe_database(database_url: str) -> str:
    """The database URL as it will be used, with the password hidden.

    SQLite URLs have no password, so their path is shown as is (readable, not escaped).
    """
    url = make_url(normalize_database_url(database_url))
    if url.get_backend_name() == "sqlite":
        return f"sqlite:///{url.database}" if url.database else "sqlite://"
    return url.render_as_string(hide_password=True)


def today_in(timezone_name: str) -> date:
    """Today's date in the given timezone (``APP_TIMEZONE``), not the machine's."""
    return datetime.now(ZoneInfo(timezone_name)).date()


@dataclass(frozen=True)
class Clients:
    """The things the commands need from outside, so tests can replace them."""

    stats: Callable[[], StatsClient] = StatsNbaClient
    today: Callable[[str], date] = today_in


def _season(value: str) -> str:
    """argparse type: a season label such as 2025-26, whose second part follows the first."""
    match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if match is None or int(match[2]) != (int(match[1]) + 1) % 100:
        raise argparse.ArgumentTypeError(f"{value!r} is not a season like 2025-26")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nbaproj")
    commands = parser.add_subparsers(dest="command", required=True)
    backfill = commands.add_parser(
        "backfill", help="load a past regular season (default: last season)"
    )
    backfill.add_argument(
        "--season", action="append", type=_season, help="for example 2025-26; repeatable"
    )
    schedule = commands.add_parser(
        "schedule", help="cache a season's schedule (default: the current season)"
    )
    schedule.add_argument("--season", type=_season, help="for example 2026-27")
    return parser


def _attempt(name: str, run: Callable[[], RunResult], session: Session) -> tuple[str, RunResult]:
    """Run one job; a database or payload error becomes a failed result, not a traceback."""
    result = None
    try:
        with guarded(session, name) as guard:
            result = run()
    except Exception as error:  # pylint: disable=broad-exception-caught
        # Approved by Josh (2026-10-28): this is the one place that must catch everything.
        # A crash in one command or season must not lose the other results, so it becomes
        # a failed result (exit code 1) and the traceback goes to the log. Jobs that raise
        # have already marked their own refresh log row failed.
        logger.exception("%s crashed", name)
        with suppress(SQLAlchemyError):
            session.rollback()
        return name, RunResult(status="failed", message=f"unexpected error: {error!r}")
    if result is None:
        result = RunResult(status="failed", message=guard.error)
    return name, result


def _run_command(
    args: argparse.Namespace, session: Session, clients: Clients, today: date
) -> list[tuple[str, RunResult]]:
    stats = clients.stats()
    if args.command == "backfill":
        seasons = args.season or [last_completed_season(today)]
        return [
            _attempt(
                "backfill",
                lambda season=season: backfill_season(session, stats, season, trigger=TRIGGER),
                session,
            )
            for season in seasons
        ]
    season = args.season or season_for_date(today)
    return [
        _attempt(
            "schedule", lambda: run_schedule_refresh(session, stats, season, TRIGGER), session
        )
    ]


def main(
    argv: Sequence[str] | None = None,
    clients: Clients = Clients(),
    env: Mapping[str, str] | None = None,
) -> int:
    """Run a command and return the process exit code (1 if any run failed or was rejected)."""
    args = _build_parser().parse_args(argv)
    try:
        settings = load_settings(os.environ if env is None else env)
    except ConfigError as error:
        print(f"configuration error: {error}")
        return 2
    try:
        logger.info("database: %s", describe_database(settings.database_url))
        engine = create_db_engine(settings.database_url)
    except (SQLAlchemyError, ImportError) as error:
        print(f"database configuration error: {error}")
        return 2
    try:
        with Session(engine) as session:
            today = clients.today(settings.app_timezone)
            results = _run_command(args, session, clients, today)
    finally:
        engine.dispose()
    for name, result in results:
        print(f"{name}: {result.status} - {result.message}")
    return 1 if any(result.status in FAILING_STATUSES for _, result in results) else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
