"""Tests for refresh run recording."""

from datetime import datetime, timedelta, timezone

import pytest

from nbaproj.db.models import RefreshLog
from nbaproj.db.refresh_log import RefreshOutcome, finish_refresh, start_refresh

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
