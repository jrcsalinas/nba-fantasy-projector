"""The season schedule from the NBA stats host, cached in the ``game`` table."""

from collections.abc import Callable
from datetime import date, datetime

from sqlalchemy.orm import Session

from nbaproj.ingestion.client import StatsClient
from nbaproj.ingestion.loader import upsert_games
from nbaproj.ingestion.parsing import (
    ScoreboardGame,
    fantasy_game_type,
    malformed_as_payload_error,
    parse_game_id,
    parse_team,
    parse_utc,
    status_name,
)
from nbaproj.ingestion.runs import Job, Lease, RunResult, guarded, utc_now

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
    with malformed_as_payload_error("schedule"):
        for day in payload["leagueSchedule"]["gameDates"]:
            for raw in day["games"]:
                game_id = parse_game_id(raw["gameId"])
                if fantasy_game_type(game_id) is None or not _has_real_teams(raw):
                    continue
                games.append(
                    ScoreboardGame(
                        game_id=game_id,
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


def season_for_date(day: date) -> str:
    """The season label for a date. A season starts on 1 July: ``2026-27`` from then on.

    From July the upcoming season's schedule is published, so that is the season to cache.
    """
    start_year = day.year if day.month >= 7 else day.year - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def previous_season(season: str) -> str:
    """The season label before ``season``: ``2026-27`` gives ``2025-26``."""
    start_year = int(season[:4]) - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def _run_schedule_refresh(
    session: Session, client: StatsClient, season: str, lease: Lease
) -> RunResult:
    written = 0
    with guarded(session, season) as guard:
        games = parse_schedule(client.get_schedule(season))
        if not games:
            return RunResult(
                status="failed",
                message=f"{season}: no games in the schedule (wrong season or an empty response)",
            )
        if not lease.renew():
            return RunResult(
                status="failed",
                message=f"{season}: lost the lock to another writer; nothing was written",
            )
        written = store_schedule(session, games)
        session.commit()
    if guard.error:
        return RunResult(status="failed", message=guard.error)
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
        session, trigger, clock, lambda lease: _run_schedule_refresh(session, client, season, lease)
    )


def last_completed_season(day: date) -> str:
    """The latest season that is certainly over: the one before ``season_for_date``.

    Before 1 July the current season is treated as still running, even in the last days
    after the finals, so the default errs on the safe (earlier) side.
    """
    return previous_season(season_for_date(day))
