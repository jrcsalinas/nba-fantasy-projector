"""Tests for the freshness read model behind the banner."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from nbaproj.db.models import RefreshLog
from nbaproj.web.freshness import MESSAGE_LIMIT, load_freshness

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


@pytest.fixture(name="session")
def session_fixture(engine):
    """A session on the test database."""
    with Session(engine) as session:
        yield session


def _run(session: Session, status: str, ago: timedelta, message: str | None = None) -> None:
    """Add a log row that began ``ago`` before NOW (finished a minute later unless running)."""
    started = NOW - ago
    finished = None if status == "running" else started + timedelta(minutes=1)
    session.add(
        RefreshLog(
            trigger="cron",
            started_at=started,
            finished_at=finished,
            status=status,
            message=message,
            rows_upserted=0,
        )
    )
    session.commit()


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
    assert freshness.message == "could not check the scoreboard: timed out"
    assert freshness.failed_at == NOW - 1 * HOUR + timedelta(minutes=1)
    assert freshness.last_checked == NOW - 6 * HOUR + timedelta(minutes=1)


def test_a_later_success_clears_an_earlier_failure(session):
    """Only the latest run decides: a failure followed by a success is ok again."""
    _run(session, "failed", 3 * HOUR, "boom")
    _run(session, "succeeded", 1 * HOUR)

    freshness = load_freshness(session, NOW)

    assert freshness.state == "ok" and freshness.message is None


def test_a_run_still_in_progress_is_running(session):
    """A run that has started and not finished shows as running, with the last check kept."""
    _run(session, "succeeded", 6 * HOUR)
    _run(session, "running", timedelta(minutes=2))

    freshness = load_freshness(session, NOW)

    assert freshness.state == "running"
    assert freshness.last_checked == NOW - 6 * HOUR + timedelta(minutes=1)


def test_a_run_that_began_over_an_hour_ago_is_not_shown_as_running(session):
    """A run whose process died stays "running" in the log until the next run reaps it."""
    _run(session, "running", 2 * HOUR)

    assert load_freshness(session, NOW).state == "never"


def test_a_rejected_run_is_ignored(session):
    """A run that never started because the lock was held says nothing about the data."""
    _run(session, "succeeded", 3 * HOUR)
    _run(session, "rejected", 1 * HOUR, "another refresh holds the lock")

    freshness = load_freshness(session, NOW)

    assert freshness.state == "ok" and freshness.message is None


def test_a_long_failure_message_is_clipped(session):
    """The banner never shows more than MESSAGE_LIMIT characters of a failure."""
    _run(session, "failed", 1 * HOUR, "x" * 500)

    assert len(load_freshness(session, NOW).message) == MESSAGE_LIMIT
