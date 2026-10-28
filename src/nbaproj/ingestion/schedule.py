"""The season schedule from the NBA stats host, cached in the ``game`` table."""

import logging
from collections.abc import Callable
from datetime import date, datetime

from sqlalchemy.orm import Session

from nbaproj.ingestion.client import StatsClient
from nbaproj.ingestion.loader import upsert_games
from nbaproj.ingestion.parsing import (
    ScoreboardGame,
    fantasy_game_type,
    parse_team,
    parse_utc,
    status_name,
)
from nbaproj.ingestion.runs import EXPECTED_ERRORS, Job, RunResult, utc_now

logger = logging.getLogger(__name__)

SCHEDULE_JOB = Job("schedule", RunResult)


def _has_real_teams(raw_game: dict) -> bool:
    """Playoff slots not yet decided carry a team id of 0."""
    return all(int(raw_game[side]["teamId"]) > 0 for side in ("homeTeam", "awayTeam"))


def parse_schedule(payload: dict) -> list[ScoreboardGame]:
    """Parse a ``ScheduleLeagueV2`` payload into the games that count for fantasy.

    Preseason, All-Star and Cup final games are dropped, and so are games whose
    teams are not both real NBA teams.
    """
    games = []
    for day in payload["leagueSchedule"]["gameDates"]:
        for raw in day["games"]:
            if fantasy_game_type(raw["gameId"]) is None or not _has_real_teams(raw):
                continue
            games.append(
                ScoreboardGame(
                    game_id=raw["gameId"],
                    status=status_name(raw),
                    tip_off_utc=parse_utc(raw["gameDateTimeUTC"]),
                    home=parse_team(raw["homeTeam"]),
                    away=parse_team(raw["awayTeam"]),
                )
            )
    return games


def store_schedule(session: Session, games: list[ScoreboardGame]) -> int:
    """Store schedule games and their teams. Returns the number of games.

    Idempotent: a second load updates tip-off times and statuses and adds nothing, and
    a ``final`` game is never moved back. The caller commits.
    """
    return upsert_games(session, games)


def refresh_schedule(session: Session, client: StatsClient, season: str) -> int:
    """Fetch a season's schedule, store it, and commit. Returns the number of games."""
    written = store_schedule(session, parse_schedule(client.get_schedule(season)))
    session.commit()
    return written


def season_for_date(day: date) -> str:
    """The season label for a date: a new season starts in October (``2026-27``)."""
    start_year = day.year if day.month >= 10 else day.year - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def previous_season(season: str) -> str:
    """The season label before ``season``: ``2026-27`` gives ``2025-26``."""
    start_year = int(season[:4]) - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def _run_schedule_refresh(session: Session, client: StatsClient, season: str) -> RunResult:
    try:
        written = refresh_schedule(session, client, season)
    except EXPECTED_ERRORS as error:
        session.rollback()
        logger.exception("Schedule refresh for %s failed", season)
        return RunResult(status="failed", message=f"{season}: {error}")
    return RunResult(
        status="succeeded", rows_upserted=written, message=f"{season}: {written} games"
    )


def run_schedule_refresh(
    session: Session,
    client: StatsClient,
    season: str,
    trigger: str,
    clock: Callable[[], datetime] = utc_now,
) -> RunResult:
    """Refresh a season's schedule under the shared lock and record it in the log."""
    return SCHEDULE_JOB.run(
        session, trigger, clock, lambda: _run_schedule_refresh(session, client, season)
    )
