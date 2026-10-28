"""Loading box scores for finished games: the daily run and the catch-up run."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from nbaproj.db.models import Game, GameLog
from nbaproj.ingestion.client import NbaClient, NbaClientError
from nbaproj.ingestion.loader import store_boxscore
from nbaproj.ingestion.parsing import (
    ScoreboardGame,
    fantasy_game_type,
    parse_boxscore,
    parse_scoreboard,
)
from nbaproj.ingestion.runs import EXPECTED_ERRORS, LOCK_NAME, Job, RunResult, utc_now

logger = logging.getLogger(__name__)

__all__ = [
    "LOCK_NAME",
    "IngestResult",
    "catch_up_missed_games",
    "ingest_final_games",
]

# A game is due for catch-up once it tipped off this long ago (so it has finished) and
# until it is this old (older or postponed games are left to a manual backfill).
CATCH_UP_GRACE = timedelta(hours=6)
CATCH_UP_LOOKBACK = timedelta(days=14)


@dataclass(frozen=True)
class IngestResult(RunResult):
    """What an ingestion or catch-up run did."""

    games_loaded: int = 0
    failed_game_ids: tuple[str, ...] = ()
    games_not_final: int = 0


@dataclass
class _Loaded:
    games: int = 0
    rows: int = 0
    not_final: int = 0
    failed: list[str] = field(default_factory=list)


INGEST_JOB = Job("ingest_final_games", IngestResult)
CATCH_UP_JOB = Job("catch_up", IngestResult)


def _is_new_final_game(session: Session, game: ScoreboardGame) -> bool:
    if game.status != "final" or fantasy_game_type(game.game_id) is None:
        return False
    has_logs = session.scalar(select(exists().where(GameLog.game_id == game.game_id)))
    return not has_logs


def _load_games(session: Session, client: NbaClient, game_ids: list[str]) -> _Loaded:
    """Load each game in its own transaction. A box score not yet final is left alone."""
    loaded = _Loaded()
    for game_id in game_ids:
        try:
            box = parse_boxscore(client.get_boxscore(game_id))
            if box.status != "final":
                loaded.not_final += 1
                continue
            loaded.rows += store_boxscore(session, box)
            session.commit()
            loaded.games += 1
        except EXPECTED_ERRORS:
            session.rollback()
            logger.exception("Could not load game %s", game_id)
            loaded.failed.append(game_id)
    return loaded


def _result(loaded: _Loaded) -> IngestResult:
    if loaded.failed:
        status = "failed"
    elif loaded.games:
        status = "succeeded"
    else:
        status = "skipped"
    message = f"loaded {loaded.games} games"
    if loaded.not_final:
        message += f"; {loaded.not_final} not final yet"
    if loaded.failed:
        message += f"; failed: {', '.join(loaded.failed)}"
    return IngestResult(
        status=status,
        games_loaded=loaded.games,
        rows_upserted=loaded.rows,
        failed_game_ids=tuple(loaded.failed),
        games_not_final=loaded.not_final,
        message=message,
    )


def _run_ingest(session: Session, client: NbaClient) -> IngestResult:
    try:
        scoreboard = parse_scoreboard(client.get_scoreboard())
    except NbaClientError as error:
        logger.error("Scoreboard unavailable: %s", error)
        return IngestResult(status="failed", message=f"scoreboard unavailable: {error}")
    new_ids = [g.game_id for g in scoreboard if _is_new_final_game(session, g)]
    if not new_ids:
        return IngestResult(status="skipped", message="no new final games")
    return _result(_load_games(session, client, new_ids))


def ingest_final_games(
    session: Session,
    client: NbaClient,
    trigger: str,
    clock: Callable[[], datetime] = utc_now,
) -> IngestResult:
    """Load every new final game from the live scoreboard and log the run.

    Takes the shared refresh lock, so a second run at the same time is rejected.
    Each game is committed on its own: one bad game fails the run but not the others.
    """
    return INGEST_JOB.run(session, trigger, clock, lambda: _run_ingest(session, client))


def _due_game_ids(session: Session, now: datetime) -> list[str]:
    """Stored games that should be over by now, are recent, and have no logs yet."""
    due = session.scalars(
        select(Game.game_id)
        .where(
            Game.tip_off_utc <= now - CATCH_UP_GRACE,
            Game.tip_off_utc >= now - CATCH_UP_LOOKBACK,
            ~exists().where(GameLog.game_id == Game.game_id),
        )
        .order_by(Game.tip_off_utc, Game.game_id)
    )
    return [game_id for game_id in due if fantasy_game_type(game_id) is not None]


def _run_catch_up(
    session: Session, client: NbaClient, clock: Callable[[], datetime]
) -> IngestResult:
    due = _due_game_ids(session, clock())
    if not due:
        return IngestResult(status="skipped", message="no games due for catch-up")
    return _result(_load_games(session, client, due))


def catch_up_missed_games(
    session: Session,
    client: NbaClient,
    trigger: str,
    clock: Callable[[], datetime] = utc_now,
) -> IngestResult:
    """Load box scores for recent past games in the schedule that have no logs yet.

    Heals missed days (an outage, a cold start, starting mid-season). Runs under the
    same lock and refresh log as the daily run.
    """
    return CATCH_UP_JOB.run(
        session, trigger, clock, lambda: _run_catch_up(session, client, clock)
    )
