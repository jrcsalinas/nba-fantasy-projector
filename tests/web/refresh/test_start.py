"""Tests for starting a refresh: the decision, the background run, and its log."""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nbaproj.db.locks import acquire_lock
from nbaproj.db.models import GameLog, RefreshLog
from nbaproj.ingestion.client import NbaClientError
from nbaproj.ingestion.refresh import LOCK_NAME
from tests.ingestion.fakes import FakeClient, only_games
from tests.web.helpers import csrf_token
from tests.web.refresh.conftest import NOW

TOKEN = "cron-token-" + "c" * 30
REGULAR = "0022500001"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(name="nba")
def nba_fixture(scoreboard_payload, boxscore_payload):
    """A fake NBA client with one new final game."""
    return FakeClient(only_games(scoreboard_payload, REGULAR), {REGULAR: boxscore_payload})


@pytest.fixture(name="cron_client")
def cron_client_fixture(make_client, nba):
    """A signed-out client (the cron has no login) whose app uses the fake NBA client."""
    client = make_client(signed_in=False, REFRESH_TOKEN=TOKEN)
    client.app.state.nba_client = nba
    return client


def _runs(engine) -> list[tuple[str, str]]:
    """Every refresh log row as (trigger, status), oldest first."""
    with Session(engine) as session:
        rows = session.scalars(select(RefreshLog).order_by(RefreshLog.id))
        return [(row.trigger, row.status) for row in rows]


def _game_lines(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(GameLog))


def _after(client, minutes: int) -> None:
    """Move the app's clock ``minutes`` past NOW."""
    client.app.state.clock = lambda: NOW + timedelta(minutes=minutes)


def test_a_refresh_returns_202_and_the_run_is_logged_with_the_cron_trigger(cron_client, engine):
    """The answer is immediate and says a run started; the run leaves one log row."""
    response = cron_client.post("/refresh", headers=BEARER)

    assert response.status_code == 202
    assert response.json() == {"status": "started"}
    assert _runs(engine) == [("cron", "succeeded")]


def test_a_ui_refresh_is_logged_with_the_ui_trigger(make_client, engine, scoreboard_payload):
    """The button's run is labelled ``ui`` (here nothing is new, so it is skipped)."""
    client = make_client()
    client.app.state.nba_client = FakeClient(only_games(scoreboard_payload), {})
    token = csrf_token(client.get("/").text)

    response = client.post("/refresh", headers={"X-CSRF-Token": token})

    assert response.status_code == 202
    assert _runs(engine) == [("ui", "skipped")]


def test_a_new_final_game_is_loaded_by_the_background_run(cron_client, nba, engine):
    """The run is the real ingestion: the game's box score is fetched and stored."""
    cron_client.post("/refresh", headers=BEARER)

    assert nba.boxscore_requests == [REGULAR]
    assert _game_lines(engine) > 0


def test_a_second_refresh_within_the_interval_starts_nothing(cron_client, engine):
    """Ten minutes is the minimum between real runs: the second call is told it is recent."""
    cron_client.post("/refresh", headers=BEARER)
    _after(cron_client, 9)

    response = cron_client.post("/refresh", headers=BEARER)

    assert response.status_code == 200
    assert response.json() == {"status": "recent"}
    assert _runs(engine) == [("cron", "succeeded")]


@pytest.mark.parametrize("status", ["succeeded", "skipped", "running"])
@pytest.mark.parametrize("minutes", [9, 11])
def test_the_ten_minute_interval_counts_each_kind_of_recent_run(
    cron_client, seed_log, status, minutes
):
    """A run that is running, succeeded or skipped blocks a refresh for ten minutes (no lock)."""
    seed_log(status, timedelta(minutes=minutes))

    response = cron_client.post("/refresh", headers=BEARER)

    expected = (200, "recent") if minutes == 9 else (202, "started")
    assert (response.status_code, response.json()["status"]) == expected


def test_a_refresh_after_the_interval_starts_again(cron_client, engine):
    """Past the minimum interval the next call starts a new run."""
    cron_client.post("/refresh", headers=BEARER)
    _after(cron_client, 11)

    response = cron_client.post("/refresh", headers=BEARER)

    assert response.status_code == 202
    assert len(_runs(engine)) == 2


def test_a_failed_run_does_not_block_an_immediate_retry(cron_client, engine):
    """The cron's retry after a failure must go through, not be told "recent"."""
    cron_client.app.state.nba_client = FakeClient(NbaClientError("down"), {})
    first = cron_client.post("/refresh", headers=BEARER)
    cron_client.app.state.nba_client = FakeClient(NbaClientError("still down"), {})

    second = cron_client.post("/refresh", headers=BEARER)

    assert first.status_code == second.status_code == 202
    assert _runs(engine) == [("cron", "failed"), ("cron", "failed")]


def test_a_refresh_while_another_holds_the_lock_is_a_409_and_logs_nothing(cron_client, engine):
    """A laptop run (or an earlier refresh) holds the shared lock: nothing starts or is logged."""
    with Session(engine) as session:
        assert acquire_lock(session, LOCK_NAME, "laptop-run", timedelta(minutes=10), NOW)

    response = cron_client.post("/refresh", headers=BEARER)

    assert response.status_code == 409
    assert response.json() == {"status": "running"}
    assert _runs(engine) == []


def test_an_unexpected_error_in_the_background_run_is_logged_and_marks_the_run_failed(
    cron_client, engine, caplog
):
    """A bug in the run is logged with its traceback and never escapes into the server."""
    cron_client.app.state.nba_client = FakeClient(RuntimeError("boom"), {})

    with caplog.at_level(logging.ERROR):
        response = cron_client.post("/refresh", headers=BEARER)

    assert response.status_code == 202
    assert _runs(engine) == [("cron", "failed")]
    assert "Background refresh crashed" in caplog.text and "boom" in caplog.text


def test_a_failed_run_keeps_the_previous_data(cron_client, engine):
    """A failed refresh adds nothing and removes nothing."""
    cron_client.post("/refresh", headers=BEARER)
    before = _game_lines(engine)
    cron_client.app.state.nba_client = FakeClient(NbaClientError("down"), {})
    _after(cron_client, 11)

    cron_client.post("/refresh", headers=BEARER)

    assert _runs(engine)[-1] == ("cron", "failed")
    assert before > 0
    assert _game_lines(engine) == before
