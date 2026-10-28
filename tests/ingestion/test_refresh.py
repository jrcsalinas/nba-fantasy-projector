"""Tests for the daily ingestion run."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from nbaproj.db.locks import acquire_lock
from nbaproj.db.models import Game, GameLog, JobLock, RefreshLog
from nbaproj.ingestion.client import NbaClientError
from nbaproj.ingestion.refresh import LOCK_NAME, ingest_final_games
from tests.ingestion.fakes import FakeClient, boxscore_for, only_games


NOW = datetime(2025, 10, 29, 5, 0, tzinfo=timezone.utc)
REGULAR = "0022500001"
PLAYOFF = "0042500101"


def _clock() -> datetime:
    return NOW


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _run(session, client):
    return ingest_final_games(session, client, trigger="cli", clock=_clock)


def test_ingest_loads_new_final_game_and_logs_success(
    session, scoreboard_payload, boxscore_payload
):
    """A new final game is loaded, the run is logged, and the lock is released."""
    client = FakeClient(
        only_games(scoreboard_payload, REGULAR), {REGULAR: boxscore_payload}
    )

    result = _run(session, client)

    assert (result.status, result.games_loaded, result.rows_upserted) == (
        "succeeded",
        1,
        5,
    )
    assert session.get(Game, REGULAR).status == "final"
    assert _count(session, GameLog) == 5
    log = session.scalars(select(RefreshLog)).one()
    assert (log.trigger, log.status, log.rows_upserted) == ("cli", "succeeded", 5)
    assert _count(session, JobLock) == 0


def test_ingest_skips_when_nothing_is_new(session, scoreboard_payload, boxscore_payload):
    """Games already stored as final are not fetched again."""
    client = FakeClient(
        only_games(scoreboard_payload, REGULAR), {REGULAR: boxscore_payload}
    )
    _run(session, client)

    second = _run(session, client)

    assert (second.status, second.games_loaded) == ("skipped", 0)
    assert client.boxscore_requests == [REGULAR]
    statuses = session.scalars(select(RefreshLog.status).order_by(RefreshLog.id)).all()
    assert statuses == ["succeeded", "skipped"]


def test_ingest_ignores_preseason_and_unfinished_games(
    session, scoreboard_payload, boxscore_payload
):
    """Only final regular-season, play-in or playoff games are loaded."""
    client = FakeClient(
        scoreboard_payload,
        {
            REGULAR: boxscore_payload,
            PLAYOFF: boxscore_for(boxscore_payload, PLAYOFF),
        },
    )

    result = _run(session, client)

    assert result.games_loaded == 2
    assert sorted(client.boxscore_requests) == [REGULAR, PLAYOFF]


def test_ingest_is_rejected_when_lock_is_held(session, scoreboard_payload):
    """A held lock rejects the run without fetching, and the other holder keeps it."""
    acquire_lock(session, LOCK_NAME, "someone-else", timedelta(hours=1), NOW)
    client = FakeClient(scoreboard_payload, {})

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("rejected", 0)
    assert not client.boxscore_requests
    assert session.scalars(select(RefreshLog.status)).one() == "rejected"
    assert session.get(JobLock, LOCK_NAME).holder == "someone-else"


def test_ingest_reports_failed_but_loads_other_games_and_releases_lock(
    session, scoreboard_payload, boxscore_payload
):
    """One bad game fails the run, but the other game is still loaded."""
    client = FakeClient(
        scoreboard_payload,
        {
            REGULAR: NbaClientError("down"),
            PLAYOFF: boxscore_for(boxscore_payload, PLAYOFF),
        },
    )

    result = _run(session, client)

    assert result.status == "failed"
    assert result.games_loaded == 1
    assert result.failed_game_ids == (REGULAR,)
    assert session.get(Game, PLAYOFF) is not None
    assert session.get(Game, REGULAR) is None
    log = session.scalars(select(RefreshLog)).one()
    assert log.status == "failed"
    assert REGULAR in log.message
    assert _count(session, JobLock) == 0


def test_ingest_reports_failed_when_scoreboard_is_unreachable(session):
    """If the scoreboard cannot be fetched the run fails and the lock is released."""
    client = FakeClient(NbaClientError("scoreboard down"), {})

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("failed", 0)
    assert "scoreboard down" in session.scalars(select(RefreshLog.message)).one()
    assert _count(session, JobLock) == 0
