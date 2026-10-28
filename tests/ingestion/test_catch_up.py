"""Tests for catching up on past games that never got their box scores."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from nbaproj.db.locks import acquire_lock
from nbaproj.db.models import Game, GameLog, RefreshLog
from nbaproj.ingestion.loader import store_boxscore
from nbaproj.ingestion.parsing import parse_boxscore
from nbaproj.ingestion.refresh import catch_up_missed_games
from nbaproj.ingestion.runs import LOCK_NAME
from nbaproj.ingestion.schedule import parse_schedule, store_schedule
from tests.ingestion.fakes import FakeClient, boxscore_for

NOW = datetime(2025, 10, 23, 12, 0, tzinfo=timezone.utc)
REGULAR_1 = "0022500001"  # tipped off 2025-10-21 23:30Z, about 36 hours before NOW
REGULAR_2 = "0022500002"  # tipped off 2025-10-22 23:00Z, 13 hours before NOW
PLAY_IN = "0052500001"
PLAYOFF = "0042500102"


def _clock() -> datetime:
    return NOW


def _catch_up(session, client):
    return catch_up_missed_games(session, client, trigger="cli", clock=_clock)


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _schedule(session, schedule_payload) -> None:
    store_schedule(session, parse_schedule(schedule_payload))
    session.commit()


def _tip_off(session, game_id: str, when: datetime) -> None:
    session.get(Game, game_id).tip_off_utc = when
    session.commit()


def test_catch_up_loads_past_games_without_logs(
    session, schedule_payload, boxscore_payload
):
    """Both regular-season games in the past get their box scores loaded."""
    _schedule(session, schedule_payload)
    client = FakeClient(
        {},
        {
            REGULAR_1: boxscore_payload,
            REGULAR_2: boxscore_for(boxscore_payload, REGULAR_2),
        },
    )

    result = _catch_up(session, client)

    assert (result.status, result.games_loaded) == ("succeeded", 2)
    assert client.boxscore_requests == [REGULAR_1, REGULAR_2]
    assert session.scalars(select(Game.status).where(Game.game_id == REGULAR_2)).one() == "final"
    log = session.scalars(select(RefreshLog)).one()
    assert (log.trigger, log.status) == ("cli", "succeeded")


def test_catch_up_ignores_recent_old_loaded_and_uncountable_games(
    session, schedule_payload, boxscore_payload
):
    """Only a countable past game with no logs, inside the window, is requested."""
    _schedule(session, schedule_payload)
    store_boxscore(session, parse_boxscore(boxscore_payload))  # REGULAR_1 has logs
    session.commit()
    _tip_off(session, REGULAR_2, NOW - timedelta(hours=3))  # inside the 6-hour grace
    _tip_off(session, PLAY_IN, NOW - timedelta(days=20))  # older than 14 days
    _tip_off(session, PLAYOFF, NOW - timedelta(days=2))  # the one that is due
    session.add(
        Game(
            game_id="0012500003",
            season="2025-26",
            game_type="preseason",
            tip_off_utc=NOW - timedelta(days=1),
            home_team_id=1610612747,
            away_team_id=1610612744,
            status="scheduled",
        )
    )
    session.commit()
    client = FakeClient({}, {PLAYOFF: boxscore_for(boxscore_payload, PLAYOFF)})

    result = _catch_up(session, client)

    assert client.boxscore_requests == [PLAYOFF]
    assert (result.status, result.games_loaded) == ("succeeded", 1)
    assert _count(session, GameLog) == 10


def test_catch_up_skips_games_whose_box_score_is_not_final(
    session, schedule_payload, boxscore_payload
):
    """A box score that is not final yet is left for later and is not a failure."""
    _schedule(session, schedule_payload)
    _tip_off(session, REGULAR_2, NOW - timedelta(hours=3))
    live = boxscore_for(boxscore_payload, REGULAR_1)
    live["game"]["gameStatus"] = 2
    client = FakeClient({}, {REGULAR_1: live})

    result = _catch_up(session, client)

    assert client.boxscore_requests == [REGULAR_1]
    assert (result.status, result.games_loaded, result.failed_game_ids) == ("skipped", 0, ())
    assert "not final" in result.message
    assert _count(session, GameLog) == 0


def test_catch_up_reports_skipped_when_nothing_is_due_and_rejected_when_locked(
    session, schedule_payload
):
    """With nothing due the run is skipped; with the lock held it is rejected."""
    client = FakeClient({}, {})

    assert _catch_up(session, client).status == "skipped"

    _schedule(session, schedule_payload)
    acquire_lock(session, LOCK_NAME, "someone-else", timedelta(hours=1), NOW)
    assert _catch_up(session, client).status == "rejected"
    assert not client.boxscore_requests
    statuses = session.scalars(select(RefreshLog.status).order_by(RefreshLog.id)).all()
    assert statuses == ["skipped", "rejected"]
