"""The daily ingestion run: load newly finished games from the live feed."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nbaproj.db.locks import acquire_lock, release_lock
from nbaproj.db.models import Game
from nbaproj.db.refresh_log import RefreshOutcome, finish_refresh, start_refresh
from nbaproj.ingestion.client import NbaClient, NbaClientError
from nbaproj.ingestion.loader import store_boxscore
from nbaproj.ingestion.parsing import (
    ScoreboardGame,
    game_type_from_id,
    parse_boxscore,
    parse_scoreboard,
)

logger = logging.getLogger(__name__)

LOCK_NAME = "refresh"
LOCK_HOLDER = "ingest_final_games"
LOCK_TTL = timedelta(minutes=10)
SKIPPED_GAME_TYPES = frozenset({"preseason"})
GAME_ERRORS = (NbaClientError, ValueError, KeyError, SQLAlchemyError)


@dataclass(frozen=True)
class IngestResult:
    """What an ingestion run did."""

    status: str
    games_loaded: int = 0
    rows_upserted: int = 0
    failed_game_ids: tuple[str, ...] = ()
    message: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_new_final_game(session: Session, game: ScoreboardGame) -> bool:
    if game.status != "final" or game_type_from_id(game.game_id) in SKIPPED_GAME_TYPES:
        return False
    stored = session.get(Game, game.game_id)
    return stored is None or stored.status != "final"


def _load_games(
    session: Session, client: NbaClient, games: list[ScoreboardGame]
) -> tuple[int, int, list[str]]:
    """Load each game in its own transaction. Returns (games, rows, failed ids)."""
    loaded, rows, failed = 0, 0, []
    for game in games:
        try:
            rows += store_boxscore(session, parse_boxscore(client.get_boxscore(game.game_id)))
            session.commit()
            loaded += 1
        except GAME_ERRORS:
            session.rollback()
            logger.exception("Could not load game %s", game.game_id)
            failed.append(game.game_id)
    return loaded, rows, failed


def _run(session: Session, client: NbaClient) -> IngestResult:
    try:
        scoreboard = parse_scoreboard(client.get_scoreboard())
    except NbaClientError as error:
        logger.error("Scoreboard unavailable: %s", error)
        return IngestResult(status="failed", message=f"scoreboard unavailable: {error}")
    new_games = [g for g in scoreboard if _is_new_final_game(session, g)]
    if not new_games:
        return IngestResult(status="skipped", message="no new final games")
    loaded, rows, failed = _load_games(session, client, new_games)
    message = f"loaded {loaded} games"
    if failed:
        message += f"; failed: {', '.join(failed)}"
    return IngestResult(
        status="failed" if failed else "succeeded",
        games_loaded=loaded,
        rows_upserted=rows,
        failed_game_ids=tuple(failed),
        message=message,
    )


def ingest_final_games(
    session: Session,
    client: NbaClient,
    trigger: str,
    clock: Callable[[], datetime] = _utc_now,
) -> IngestResult:
    """Load every new final game from the live scoreboard and log the run.

    Takes the shared refresh lock, so a second run at the same time is rejected.
    Each game is committed on its own: one bad game fails the run but not the others.
    """
    log = start_refresh(session, trigger, clock())
    if not acquire_lock(session, LOCK_NAME, LOCK_HOLDER, LOCK_TTL, clock()):
        result = IngestResult(status="rejected", message="another refresh holds the lock")
        finish_refresh(session, log, RefreshOutcome("rejected", result.message), clock())
        return result
    try:
        result = _run(session, client)
        outcome = RefreshOutcome(result.status, result.message, result.rows_upserted)
        finish_refresh(session, log, outcome, clock())
    finally:
        release_lock(session, LOCK_NAME, LOCK_HOLDER)
    return result
