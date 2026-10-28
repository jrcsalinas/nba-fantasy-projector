"""Tests for the daily ingestion run."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from nbaproj.db.locks import acquire_lock
from nbaproj.db.models import Game, GameLog, JobLock, RefreshLog, Team
from nbaproj.ingestion import refresh, runs
from nbaproj.ingestion.client import LiveNbaClient, NbaClientError, RetryPolicy
from nbaproj.ingestion.refresh import LOCK_NAME, ingest_final_games
from tests.ingestion.fakes import (
    FakeClient,
    boxscore_for,
    only_games,
    scoreboard_with_ids,
)


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


def test_ingest_skips_all_star_and_cup_games_without_crashing(session, scoreboard_payload):
    """Games with non-fantasy prefixes are skipped and the run still finishes."""
    client = FakeClient(
        scoreboard_with_ids(scoreboard_payload, "0032500001", "0062500001"), {}
    )

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("skipped", 0)
    assert not client.boxscore_requests
    assert session.scalars(select(RefreshLog.status)).one() == "skipped"


def test_ingest_loads_games_the_schedule_marked_final_without_logs(
    session, scoreboard_payload, boxscore_payload
):
    """A game stored as final but with no log rows still counts as new."""
    session.add_all(
        [
            Team(team_id=1610612747, tricode="LAL", name="Lakers", city="Los Angeles"),
            Team(team_id=1610612744, tricode="GSW", name="Warriors", city="Golden State"),
        ]
    )
    session.commit()
    session.add_all(
        [
            Game(
                game_id=REGULAR,
                season="2025-26",
                game_type="regular_season",
                tip_off_utc=NOW,
                home_team_id=1610612747,
                away_team_id=1610612744,
                status="final",
            ),
        ]
    )
    session.commit()
    client = FakeClient(
        only_games(scoreboard_payload, REGULAR), {REGULAR: boxscore_payload}
    )

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("succeeded", 1)
    assert _count(session, GameLog) == 5


def test_ingest_is_rejected_even_when_the_holder_has_the_same_job_name(
    session, scoreboard_payload
):
    """Two runs of the same job must not share a lock holder name."""
    acquire_lock(session, LOCK_NAME, "ingest_final_games", timedelta(hours=1), NOW)
    client = FakeClient(scoreboard_payload, {})

    result = _run(session, client)

    assert result.status == "rejected"
    assert not client.boxscore_requests


def test_ingest_loads_other_games_when_one_payload_is_malformed(
    session, scoreboard_payload, boxscore_payload
):
    """A null in one game's box score fails that game only."""
    bad = boxscore_for(boxscore_payload, REGULAR)
    bad["game"]["homeTeam"]["players"][0]["statistics"]["minutes"] = None
    client = FakeClient(
        scoreboard_payload,
        {REGULAR: bad, PLAYOFF: boxscore_for(boxscore_payload, PLAYOFF)},
    )

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("failed", 1)
    assert result.failed_game_ids == (REGULAR,)
    assert session.get(Game, PLAYOFF) is not None


def _break_scoreboard(payload: dict, how: str) -> None:
    game = payload["scoreboard"]["games"][0]
    if how == "status":
        game["gameStatus"] = 9
    else:
        del game["gameId"]


@pytest.mark.parametrize("how", ["status", "missing_key"])
def test_ingest_reports_failed_on_a_scoreboard_parse_error(
    session, scoreboard_payload, how
):
    """A changed scoreboard shape is a failed run, not an exception."""
    _break_scoreboard(scoreboard_payload, how)

    result = _run(session, FakeClient(scoreboard_payload, {}))

    assert result.status == "failed"
    assert "scoreboard" in result.message
    assert session.scalars(select(RefreshLog.status)).one() == "failed"


def test_ingest_reports_failed_on_a_database_error_while_checking_games(
    session, scoreboard_payload, monkeypatch
):
    """A database error while looking for new games is a failed run."""

    def broken(*_args):
        raise OperationalError("select", {}, Exception("connection lost"))

    monkeypatch.setattr(refresh, "_is_new_final_game", broken)

    result = _run(session, FakeClient(scoreboard_payload, {}))

    assert result.status == "failed"
    assert "connection lost" in result.message


def test_rows_upserted_counts_only_committed_games(
    session, scoreboard_payload, boxscore_payload, monkeypatch
):
    """A game whose commit fails is rolled back and its rows are not counted."""
    real_commit, real_store = session.commit, refresh.store_boxscore
    state = {"fail_next_commit": False}

    def store(db_session, box):
        rows = real_store(db_session, box)
        state["fail_next_commit"] = box.game_id == PLAYOFF
        return rows

    def commit():
        if state["fail_next_commit"]:
            state["fail_next_commit"] = False
            raise OperationalError("commit", {}, Exception("disk full"))
        real_commit()

    monkeypatch.setattr(refresh, "store_boxscore", store)
    monkeypatch.setattr(session, "commit", commit)
    client = FakeClient(
        scoreboard_payload,
        {
            REGULAR: boxscore_payload,
            PLAYOFF: boxscore_for(boxscore_payload, PLAYOFF),
        },
    )

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("failed", 1)
    assert result.failed_game_ids == (PLAYOFF,)
    assert result.rows_upserted == 5


class LockThief(FakeClient):
    """A client that takes over the refresh lock right after serving a box score."""

    def __init__(self, scoreboard, boxscores, session):
        super().__init__(scoreboard, boxscores)
        self.session = session

    def get_boxscore(self, game_id: str) -> dict:
        payload = super().get_boxscore(game_id)
        self.session.execute(
            update(JobLock).values(holder="thief", expires_at=NOW + timedelta(hours=1))
        )
        self.session.commit()
        return payload


def test_load_stops_when_the_lock_is_lost(session, scoreboard_payload, boxscore_payload):
    """If another writer takes the lock mid-run, no further games are loaded."""
    client = LockThief(
        scoreboard_payload,
        {
            REGULAR: boxscore_payload,
            PLAYOFF: boxscore_for(boxscore_payload, PLAYOFF),
        },
        session,
    )

    result = _run(session, client)

    assert result.status == "failed"
    assert client.boxscore_requests == [REGULAR]
    assert result.games_loaded == 1
    assert "lost the lock" in result.message
    assert "1 games not attempted" in result.message
    assert session.get(JobLock, LOCK_NAME).holder == "thief"


EIGHT_GAMES = [f"00225000{n:02d}" for n in range(11, 19)]


def test_load_stops_after_five_consecutive_client_errors(session, scoreboard_payload):
    """A dead NBA host must not be hammered for every remaining game."""
    client = FakeClient(
        scoreboard_with_ids(scoreboard_payload, *EIGHT_GAMES),
        {game_id: NbaClientError("down") for game_id in EIGHT_GAMES},
    )

    result = _run(session, client)

    assert result.status == "failed"
    assert client.boxscore_requests == EIGHT_GAMES[:5]
    assert len(result.failed_game_ids) == 5
    assert "stopped after 5 consecutive" in result.message
    assert "3 games not attempted" in result.message


def test_a_success_resets_the_error_streak(session, scoreboard_payload, boxscore_payload):
    """Errors that are not consecutive never trip the breaker."""
    boxscores = {game_id: NbaClientError("blip") for game_id in EIGHT_GAMES}
    boxscores[EIGHT_GAMES[4]] = boxscore_for(boxscore_payload, EIGHT_GAMES[4])
    client = FakeClient(scoreboard_with_ids(scoreboard_payload, *EIGHT_GAMES), boxscores)

    result = _run(session, client)

    assert client.boxscore_requests == EIGHT_GAMES
    assert (result.games_loaded, len(result.failed_game_ids)) == (1, 7)
    assert "consecutive" not in result.message


def test_ingest_replaces_backfilled_lines_with_live_lines(
    session, backfilled, scoreboard_payload, boxscore_payload
):
    """A game that only has whole-minute backfill lines still gets its live box score."""
    assert backfilled is session
    assert session.get(GameLog, (REGULAR, 1002)).source == "backfill"
    client = FakeClient(
        only_games(scoreboard_payload, REGULAR), {REGULAR: boxscore_payload}
    )

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("succeeded", 1)
    line = session.get(GameLog, (REGULAR, 1002))
    assert (line.source, line.minutes) == ("live", 33.25)
    assert _count(session, GameLog) == 5


def test_ingest_treats_a_final_box_score_with_no_players_as_failed(
    session, scoreboard_payload, boxscore_payload
):
    """A final game without a single player is a data problem, not a successful load."""
    empty = boxscore_for(boxscore_payload, REGULAR)
    for side in ("homeTeam", "awayTeam"):
        for player in empty["game"][side]["players"]:
            player["played"] = "0"
    client = FakeClient(only_games(scoreboard_payload, REGULAR), {REGULAR: empty})

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("failed", 0)
    assert result.failed_game_ids == (REGULAR,)
    assert "no players" in result.message
    assert session.get(Game, REGULAR) is None
    assert _count(session, GameLog) == 0


def test_an_unexpected_key_error_is_not_swallowed_as_bad_data(
    session, scoreboard_payload, boxscore_payload, monkeypatch
):
    """A KeyError from our own code is a bug: the run raises and the log row says failed."""

    def buggy(*_args):
        raise KeyError("bug")

    monkeypatch.setattr(refresh, "store_boxscore", buggy)
    client = FakeClient(
        only_games(scoreboard_payload, REGULAR), {REGULAR: boxscore_payload}
    )

    with pytest.raises(KeyError):
        _run(session, client)

    assert session.scalars(select(RefreshLog.status)).one() == "failed"


THIRTY_GAMES = [f"00225000{n:02d}" for n in range(21, 51)]


def test_failure_message_lists_counts_first_and_caps_the_ids(
    session, scoreboard_payload, boxscore_payload
):
    """With many failures the message leads with the counts and the reason, then 10 ids."""
    boxscores = {}
    for game_id in THIRTY_GAMES:
        bad = boxscore_for(boxscore_payload, game_id)
        bad["game"]["homeTeam"]["players"][0]["statistics"]["minutes"] = None
        boxscores[game_id] = bad
    client = FakeClient(scoreboard_with_ids(scoreboard_payload, *THIRTY_GAMES), boxscores)

    result = _run(session, client)

    assert result.status == "failed"
    assert len(result.failed_game_ids) == 30
    assert result.message.startswith("loaded 0 games; 30 failed; first error: ")
    assert result.message.index("first error") < result.message.index("failed ids")
    assert THIRTY_GAMES[9] in result.message and THIRTY_GAMES[10] not in result.message
    assert result.message.endswith("and 20 more")
    assert len(result.message) <= 500


def test_ingest_loads_other_games_when_nba_api_chokes_on_one(
    session, scoreboard_payload, boxscore_payload
):
    """nba_api raising KeyError('players') for one game fails that game, not the run."""
    boxscores = {
        REGULAR: KeyError("players"),
        PLAYOFF: boxscore_for(boxscore_payload, PLAYOFF),
    }

    def fetch_boxscore(game_id):
        payload = boxscores[game_id]
        if isinstance(payload, Exception):
            raise payload
        return payload

    client = LiveNbaClient(
        fetch_scoreboard=lambda: scoreboard_payload,
        fetch_boxscore=fetch_boxscore,
        retry=RetryPolicy(sleep=lambda _seconds: None),
    )

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("failed", 1)
    assert result.failed_game_ids == (REGULAR,)
    assert "unexpected response" in result.message
    assert session.get(Game, PLAYOFF) is not None


def test_a_lease_renewal_error_stops_the_run_instead_of_escaping(
    session, scoreboard_payload, boxscore_payload, monkeypatch
):
    """If the database fails while renewing the lock, no game is loaded and the run says why."""
    real_acquire, calls = runs.acquire_lock, {"count": 0}

    def flaky(*args):
        calls["count"] += 1
        if calls["count"] > 1:  # the first call takes the lock; later ones are renewals
            raise OperationalError("update", {}, Exception("connection lost"))
        return real_acquire(*args)

    monkeypatch.setattr(runs, "acquire_lock", flaky)
    client = FakeClient(
        only_games(scoreboard_payload, REGULAR), {REGULAR: boxscore_payload}
    )

    result = _run(session, client)

    assert result.status == "failed"
    assert "could not renew the lock" in result.message and "connection lost" in result.message
    assert not client.boxscore_requests


def test_a_box_score_for_another_game_is_a_payload_error(
    session, scoreboard_payload, boxscore_payload
):
    """Never store lines under a game id the feed did not actually return."""
    wrong = boxscore_for(boxscore_payload, PLAYOFF)
    client = FakeClient(only_games(scoreboard_payload, REGULAR), {REGULAR: wrong})

    result = _run(session, client)

    assert (result.status, result.games_loaded) == ("failed", 0)
    assert result.failed_game_ids == (REGULAR,)
    assert PLAYOFF in result.message
    assert session.get(Game, PLAYOFF) is None and session.get(Game, REGULAR) is None


def test_ingest_leaves_a_game_whose_box_score_is_not_final_yet(
    session, scoreboard_payload, boxscore_payload
):
    """The scoreboard can say final a moment before the box score does; try again later."""
    still_live = boxscore_for(boxscore_payload, REGULAR)
    still_live["game"]["gameStatus"] = 2
    client = FakeClient(only_games(scoreboard_payload, REGULAR), {REGULAR: still_live})

    result = _run(session, client)

    assert (result.status, result.games_loaded, result.failed_game_ids) == ("skipped", 0, ())
    assert "1 not final yet" in result.message
    assert _count(session, GameLog) == 0
