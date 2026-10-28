"""Tests for refresh run recording."""

from datetime import datetime, timedelta, timezone

import pytest

from nbaproj.db.models import RefreshLog
from nbaproj.db.refresh_log import (
    RefreshOutcome,
    finish_refresh,
    reap_abandoned,
    start_refresh,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def test_start_refresh_records_running_row(session):
    """Starting a refresh stores a running row with no finish time."""
    log = start_refresh(session, trigger="cron", now=NOW)

    row = session.get(RefreshLog, log.id)
    assert row.status == "running"
    assert row.trigger == "cron"
    assert row.started_at == NOW
    assert row.finished_at is None
    assert row.rows_upserted == 0


def test_start_refresh_rejects_unknown_trigger(session):
    """Only cron, ui, and cli are valid triggers."""
    with pytest.raises(ValueError, match="trigger"):
        start_refresh(session, trigger="carrier-pigeon", now=NOW)


def test_refresh_outcome_rejects_unknown_status():
    """Only the four final statuses are valid outcomes."""
    with pytest.raises(ValueError, match="status"):
        RefreshOutcome(status="bogus")


def test_finish_refresh_records_status_message_and_rows(session):
    """Finishing a refresh stores the outcome, a message, the row count, and the finish time."""
    log = start_refresh(session, trigger="ui", now=NOW)
    later = NOW + timedelta(minutes=2)

    outcome = RefreshOutcome(status="succeeded", message="3 games loaded", rows_upserted=42)

    finish_refresh(session, log, outcome, now=later)

    row = session.get(RefreshLog, log.id)
    assert row.status == "succeeded"
    assert row.message == "3 games loaded"
    assert row.rows_upserted == 42
    assert row.finished_at == later


def test_reap_abandoned_marks_old_running_rows_failed(session):
    """Running rows older than the limit are failed; recent and finished rows are not."""
    old = start_refresh(session, trigger="cron", now=NOW - timedelta(hours=2))
    recent = start_refresh(session, trigger="cron", now=NOW - timedelta(minutes=30))
    done = start_refresh(session, trigger="cli", now=NOW - timedelta(hours=3))
    finish_refresh(session, done, RefreshOutcome("succeeded"), NOW - timedelta(hours=2))

    reaped = reap_abandoned(session, NOW, older_than=timedelta(hours=1))

    assert reaped == 1
    assert (old.status, old.finished_at) == ("failed", NOW)
    assert "abandoned" in old.message
    assert recent.status == "running"
    assert done.status == "succeeded"
