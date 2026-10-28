"""Loading box scores for finished games: the daily run."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nbaproj.db.models import GameLog
from nbaproj.ingestion.client import NbaClient, NbaClientError
from nbaproj.ingestion.loader import SOURCE_LIVE, store_boxscore
from nbaproj.ingestion.parsing import (
    PayloadError,
    ScoreboardGame,
    fantasy_game_type,
    parse_boxscore,
    parse_scoreboard,
)
from nbaproj.ingestion.runs import (
    EXPECTED_ERRORS,
    LOCK_NAME,
    Job,
    Lease,
    RunResult,
    guarded,
    utc_now,
)

logger = logging.getLogger(__name__)

__all__ = [
    "LOCK_NAME",
    "IngestResult",
    "ingest_final_games",
]

# Stop loading games after this many NBA client errors in a row (the host is down).
MAX_CONSECUTIVE_CLIENT_ERRORS = 5
# The refresh log message is clipped at 500 characters, so keep the list of ids short.
MAX_IDS_IN_MESSAGE = 10
ERROR_TEXT_LIMIT = 200


@dataclass(frozen=True)
class IngestResult(RunResult):
    """What an ingestion run did."""

    games_loaded: int = 0
    failed_game_ids: tuple[str, ...] = ()
    games_not_final: int = 0


@dataclass
class _Loaded:
    """Running totals while games are loaded one by one."""

    games: int = 0
    rows: int = 0
    not_final: int = 0
    failed: list[str] = field(default_factory=list)
    stopped: str | None = None
    not_attempted: int = 0
    first_error: str | None = None
    client_errors_in_a_row: int = 0

    def record_failure(self, game_id: str, error: Exception) -> None:
        """Note a failed game; only NBA client errors extend the streak that trips the breaker."""
        self.failed.append(game_id)
        self.first_error = self.first_error or str(error)
        is_client_error = isinstance(error, NbaClientError)
        self.client_errors_in_a_row = self.client_errors_in_a_row + 1 if is_client_error else 0

    def stop(self, reason: str, not_attempted: int) -> None:
        """Note why loading stopped early and how many games were left."""
        self.stopped = reason
        self.not_attempted = not_attempted


INGEST_JOB = Job("ingest_final_games", IngestResult)


def _has_live_lines(game_id):
    """SQL condition: the game has lines from the live feed (backfill lines do not count).

    Backfilled lines have whole-number minutes, so a game that only has those still
    needs its live box score.
    """
    return exists().where(GameLog.game_id == game_id, GameLog.source == SOURCE_LIVE)


def _is_new_final_game(session: Session, game: ScoreboardGame) -> bool:
    if game.status != "final" or fantasy_game_type(game.game_id) is None:
        return False
    return not session.scalar(select(_has_live_lines(game.game_id)))


def _lock_problem(session: Session, lease: Lease) -> str | None:
    """Why the lock cannot be held any longer, or None if it was renewed."""
    try:
        return None if lease.renew() else "lost the lock to another writer"
    except SQLAlchemyError as error:
        session.rollback()
        return f"could not renew the lock: {error}"


def _load_one(session: Session, client: NbaClient, game_id: str, loaded: _Loaded) -> None:
    box = parse_boxscore(client.get_boxscore(game_id))
    if box.game_id != game_id:
        raise PayloadError(f"asked for game {game_id} but the box score is for {box.game_id}")
    if box.status != "final":
        loaded.not_final += 1
        return
    if not box.lines:
        raise PayloadError(f"final box score for game {game_id} has no players")
    rows = store_boxscore(session, box)
    session.commit()
    loaded.rows += rows
    loaded.games += 1


def _load_games(
    session: Session, client: NbaClient, game_ids: list[str], lease: Lease
) -> _Loaded:
    """Load each game in its own transaction.

    Stops early if the lock is lost or the NBA host keeps failing. A box score that is
    not final yet is left alone.
    """
    loaded = _Loaded()
    for index, game_id in enumerate(game_ids):
        lock_problem = _lock_problem(session, lease)
        if lock_problem:
            loaded.stop(lock_problem, len(game_ids) - index)
            break
        try:
            _load_one(session, client, game_id, loaded)
            loaded.client_errors_in_a_row = 0
        except EXPECTED_ERRORS as error:
            session.rollback()
            logger.exception("Could not load game %s", game_id)
            loaded.record_failure(game_id, error)
        if loaded.client_errors_in_a_row >= MAX_CONSECUTIVE_CLIENT_ERRORS:
            reason = f"stopped after {loaded.client_errors_in_a_row} consecutive NBA errors"
            loaded.stop(reason, len(game_ids) - index - 1)
            break
    return loaded


def _failed_ids_text(failed: list[str]) -> str:
    shown = ", ".join(failed[:MAX_IDS_IN_MESSAGE])
    hidden = len(failed) - MAX_IDS_IN_MESSAGE
    return f"{shown} and {hidden} more" if hidden > 0 else shown


def _message(loaded: _Loaded) -> str:
    """Counts and reasons first, the (capped) list of game ids last, so a clip loses ids."""
    parts = [f"loaded {loaded.games} games"]
    if loaded.not_final:
        parts.append(f"{loaded.not_final} not final yet")
    if loaded.failed:
        parts.append(f"{len(loaded.failed)} failed")
    if loaded.stopped:
        parts += [loaded.stopped, f"{loaded.not_attempted} games not attempted"]
    if loaded.failed:
        parts += [
            f"first error: {(loaded.first_error or '')[:ERROR_TEXT_LIMIT]}",
            f"failed ids: {_failed_ids_text(loaded.failed)}",
        ]
    return "; ".join(parts)


def _result(loaded: _Loaded) -> IngestResult:
    if loaded.failed or loaded.stopped:
        status = "failed"
    elif loaded.games:
        status = "succeeded"
    else:
        status = "skipped"
    return IngestResult(
        status=status,
        games_loaded=loaded.games,
        rows_upserted=loaded.rows,
        failed_game_ids=tuple(loaded.failed),
        games_not_final=loaded.not_final,
        message=_message(loaded),
    )


def _run_ingest(session: Session, client: NbaClient, lease: Lease) -> IngestResult:
    new_ids: list[str] = []
    with guarded(session, "could not check the scoreboard") as guard:
        scoreboard = parse_scoreboard(client.get_scoreboard())
        new_ids = [g.game_id for g in scoreboard if _is_new_final_game(session, g)]
    if guard.error:
        return IngestResult(status="failed", message=guard.error)
    if not new_ids:
        return IngestResult(status="skipped", message="no new final games")
    return _result(_load_games(session, client, new_ids, lease))


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
    return INGEST_JOB.run(
        session, trigger, clock, lambda lease: _run_ingest(session, client, lease)
    )
