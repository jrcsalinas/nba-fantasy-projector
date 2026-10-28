"""Tests for the freshness read model behind the banner."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from nbaproj.web.freshness import load_freshness

from tests.web.helpers import add_live_run, add_log

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


@pytest.fixture(name="session")
def session_fixture(engine):
    """A session on the test database."""
    with Session(engine) as session:
        yield session


def _run(session: Session, status: str, ago: timedelta, message: str | None = None) -> None:
    add_log(session, NOW, status, ago, message)


def test_no_runs_means_never_checked(session):
    """An empty log is its own state, not an error and not "stale"."""
    freshness = load_freshness(session, NOW)

    assert freshness.state == "never"
    assert freshness.last_checked is None and freshness.last_loaded is None


def test_a_recent_success_is_ok_and_reports_when_new_games_were_last_loaded(session):
    """The check time and the load time come from the latest succeeded run."""
    _run(session, "succeeded", 3 * HOUR)

    freshness = load_freshness(session, NOW)

    assert freshness.state == "ok"
    assert freshness.last_checked == NOW - 3 * HOUR + timedelta(minutes=1)
    assert freshness.last_loaded == freshness.last_checked


def test_a_skipped_run_counts_as_a_check_but_not_as_new_data(session):
    """Looking at the scoreboard and finding nothing is a check; only a success loads games."""
    _run(session, "succeeded", 5 * HOUR)
    _run(session, "skipped", 1 * HOUR)

    freshness = load_freshness(session, NOW)

    assert freshness.state == "ok"
    assert freshness.last_checked == NOW - 1 * HOUR + timedelta(minutes=1)
    assert freshness.last_loaded == NOW - 5 * HOUR + timedelta(minutes=1)


def test_a_check_older_than_a_day_is_stale(session):
    """After 24 hours without a check the data may be out of date."""
    _run(session, "skipped", 25 * HOUR)

    assert load_freshness(session, NOW).state == "stale"


def test_a_failed_latest_run_is_failed_and_still_reports_the_last_good_check(session):
    """The banner can say the last run failed and still show when the data is from."""
    _run(session, "succeeded", 6 * HOUR)
    _run(session, "failed", 1 * HOUR, "could not check the scoreboard: timed out")

    freshness = load_freshness(session, NOW)

    assert freshness.state == "failed"
    assert freshness.failed_at == NOW - 1 * HOUR + timedelta(minutes=1)
    assert freshness.last_checked == NOW - 6 * HOUR + timedelta(minutes=1)


def test_a_later_success_clears_an_earlier_failure(session):
    """Only the latest run decides: a failure followed by a success is ok again."""
    _run(session, "failed", 3 * HOUR, "boom")
    _run(session, "succeeded", 1 * HOUR)

    assert load_freshness(session, NOW).state == "ok"


def test_a_run_still_in_progress_is_running(session):
    """A run that has started and not finished shows as running, with the last check kept."""
    _run(session, "succeeded", 6 * HOUR)
    add_live_run(session, NOW, timedelta(minutes=2))

    freshness = load_freshness(session, NOW)

    assert freshness.state == "running"
    assert freshness.last_checked == NOW - 6 * HOUR + timedelta(minutes=1)


@pytest.mark.parametrize("minutes", [30, 120])
def test_a_running_row_with_no_live_lock_is_a_dead_run(session, minutes):
    """A run whose process died stays "running" in the log; with no lock it is not running.

    Thirty minutes is past the lock's ten but inside the hour the log takes to mark it
    abandoned: the window in which the banner used to say "Refreshing" for a run that was gone.
    """
    _run(session, "succeeded", 6 * HOUR)
    _run(session, "running", timedelta(minutes=minutes))

    assert load_freshness(session, NOW).state == "ok"


def test_a_run_holding_a_live_lock_is_running_however_long_it_has_gone(session):
    """A long job (the backfill) renews its lock, so the lock, not the clock, says it is alive."""
    add_live_run(session, NOW, 3 * HOUR)

    assert load_freshness(session, NOW).state == "running"


def test_a_rejected_run_is_ignored(session):
    """A run that never started because the lock was held says nothing about the data."""
    _run(session, "succeeded", 3 * HOUR)
    _run(session, "rejected", 1 * HOUR, "another refresh holds the lock")

    assert load_freshness(session, NOW).state == "ok"
