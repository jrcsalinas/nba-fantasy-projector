"""Command line for the laptop: backfill a season, refresh the schedule, catch up.

Run from the project root, for example::

    .venv/Scripts/python.exe -m nbaproj.cli backfill --season 2025-26

The database comes from ``DATABASE_URL`` (see ``nbaproj.config``). Create the tables
first with ``alembic upgrade head``.
"""

import argparse
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from nbaproj.config import ConfigError, load_settings
from nbaproj.db.session import create_db_engine
from nbaproj.ingestion.backfill import backfill_season
from nbaproj.ingestion.client import LiveNbaClient, NbaClient, StatsClient, StatsNbaClient
from nbaproj.ingestion.refresh import catch_up_missed_games
from nbaproj.ingestion.runs import RunResult
from nbaproj.ingestion.schedule import previous_season, run_schedule_refresh, season_for_date

TRIGGER = "cli"
FAILING_STATUSES = frozenset({"failed", "rejected"})


@dataclass(frozen=True)
class Clients:
    """The things the commands need from outside, so tests can replace them."""

    stats: Callable[[], StatsClient] = StatsNbaClient
    live: Callable[[], NbaClient] = LiveNbaClient
    today: Callable[[], date] = date.today


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nbaproj")
    commands = parser.add_subparsers(dest="command", required=True)
    backfill = commands.add_parser(
        "backfill", help="load a past regular season (default: last season)"
    )
    backfill.add_argument("--season", action="append", help="for example 2025-26; repeatable")
    schedule = commands.add_parser(
        "schedule", help="cache a season's schedule (default: the current season)"
    )
    schedule.add_argument("--season", help="for example 2026-27")
    commands.add_parser("catch-up", help="load box scores for recent games with no logs")
    return parser


def _run_command(
    args: argparse.Namespace, session: Session, clients: Clients
) -> list[tuple[str, RunResult]]:
    current = season_for_date(clients.today())
    if args.command == "backfill":
        stats = clients.stats()
        seasons = args.season or [previous_season(current)]
        return [
            ("backfill", backfill_season(session, stats, season, trigger=TRIGGER))
            for season in seasons
        ]
    if args.command == "schedule":
        season = args.season or current
        return [("schedule", run_schedule_refresh(session, clients.stats(), season, TRIGGER))]
    return [("catch-up", catch_up_missed_games(session, clients.live(), trigger=TRIGGER))]


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
    engine = create_db_engine(settings.database_url)
    try:
        with Session(engine) as session:
            results = _run_command(args, session, clients)
    finally:
        engine.dispose()
    for name, result in results:
        print(f"{name}: {result.status} - {result.message}")
    return 1 if any(result.status in FAILING_STATUSES for _, result in results) else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
