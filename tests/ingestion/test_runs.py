"""Tests for the shared locked-run scaffolding."""

import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from nbaproj.db.locks import acquire_lock
from nbaproj.db.models import JobLock, RefreshLog, Team
from nbaproj.db.refresh_log import start_refresh
from nbaproj.ingestion import runs
from nbaproj.ingestion.parsing import PayloadError
from nbaproj.ingestion.runs import LOCK_NAME, Job, RunResult, guarded

NOW = datetime(2025, 10, 29, 5, 0, tzinfo=timezone.utc)
JOB = Job("test_job", RunResult)


def _clock() -> datetime:
    return NOW


def _run(session, work):
    return JOB.run(session, "cli", _clock, work)


def _db_error(message: str) -> OperationalError:
    return OperationalError("statement", {}, Exception(message))


def test_job_run_reaps_abandoned_rows_before_starting(session):
    """A run left `running` by a crashed process is failed at the start of the next run."""
    stale = start_refresh(session, trigger="cron", now=NOW - timedelta(hours=2))

    _run(session, lambda _lease: RunResult("succeeded"))

    assert stale.status == "failed"
    assert "abandoned" in stale.message


def test_job_marks_the_log_failed_when_acquire_lock_raises(session, monkeypatch):
    """A database error while taking the lock fails the log row and propagates."""

    def broken(*_args):
        raise _db_error("connection lost")

    monkeypatch.setattr(runs, "acquire_lock", broken)

    with pytest.raises(OperationalError):
        _run(session, lambda _lease: RunResult("succeeded"))

    log = session.scalars(select(RefreshLog)).one()
    assert log.status == "failed"
    assert "connection lost" in log.message


def test_a_failing_release_does_not_replace_a_good_result(session, monkeypatch):
    """Work finished and was logged; a lock release error must not turn that into a crash."""

    def broken(*_args):
        raise _db_error("release failed")

    monkeypatch.setattr(runs, "release_lock", broken)

    result = _run(session, lambda _lease: RunResult("succeeded", rows_upserted=3))

    assert (result.status, result.rows_upserted) == ("succeeded", 3)
    assert session.scalars(select(RefreshLog.status)).one() == "succeeded"


def test_a_failing_log_write_on_the_failure_path_does_not_mask_the_original_error(
    session, monkeypatch
):
    """If recording the failure also fails, the caller still sees the original error."""
    real_finish = runs.finish_refresh

    def finish(db_session, log, outcome, now):
        if outcome.status == "failed":
            raise _db_error("log write failed")
        return real_finish(db_session, log, outcome, now)

    monkeypatch.setattr(runs, "finish_refresh", finish)

    def work(_lease):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _run(session, work)

    assert session.scalar(select(func.count()).select_from(JobLock)) == 0


def test_job_logs_start_rejection_and_finish(session, caplog):
    """Each run says when it started and how it ended; a rejection is logged too."""
    caplog.set_level(logging.INFO, logger="nbaproj.ingestion.runs")

    _run(session, lambda _lease: RunResult("succeeded", rows_upserted=2))
    acquire_lock(session, LOCK_NAME, "someone-else", timedelta(hours=1), NOW)
    _run(session, lambda _lease: RunResult("succeeded"))

    messages = [record.getMessage() for record in caplog.records]
    assert any("test_job: started" in m for m in messages)
    finished = [m for m in messages if "test_job: finished" in m]
    assert len(finished) == 1
    assert "status=succeeded" in finished[0] and "rows=2" in finished[0]
    assert any("test_job: rejected" in m for m in messages)


def test_guarded_records_an_expected_error_and_rolls_back(session):
    """An expected error becomes a message, and the half-done work is rolled back."""
    session.add(Team(team_id=1, tricode="AAA", name="Alphas", city="Alpha"))

    with guarded(session, "loading the games") as guard:
        raise PayloadError("bad shape")

    assert guard.error == "loading the games: bad shape"
    assert not session.new


def test_guarded_leaves_no_error_when_the_work_succeeds(session):
    """No error is recorded when the block finishes normally."""
    with guarded(session, "loading the games") as guard:
        pass

    assert guard.error is None


def test_guarded_lets_unexpected_errors_through(session):
    """A bug is not turned into a failed result."""
    with pytest.raises(RuntimeError, match="boom"):
        with guarded(session, "loading the games"):
            raise RuntimeError("boom")


def test_a_failing_final_log_write_does_not_turn_a_good_result_into_an_error(
    session, monkeypatch
):
    """The work finished and committed; failing to log it must not look like a crash."""
    real_finish = runs.finish_refresh

    def finish(db_session, log, outcome, now):
        if outcome.status == "succeeded":
            raise _db_error("log write failed")
        return real_finish(db_session, log, outcome, now)

    monkeypatch.setattr(runs, "finish_refresh", finish)

    result = _run(session, lambda _lease: RunResult("succeeded", rows_upserted=3))

    assert (result.status, result.rows_upserted) == ("succeeded", 3)
    assert session.scalar(select(func.count()).select_from(JobLock)) == 0


def test_job_names_that_would_overflow_the_holder_column_are_rejected():
    """Postgres enforces the holder column length (SQLite does not), so check it up front."""
    longest_fitting = "x" * runs.MAX_JOB_NAME_LENGTH

    Job(longest_fitting, RunResult)
    with pytest.raises(ValueError, match="holder"):
        Job(longest_fitting + "x", RunResult)


def test_run_result_rejects_an_unknown_status():
    """A typo in a status must fail when the result is made, not after the work is done."""
    with pytest.raises(ValueError, match="status"):
        RunResult("sucess")


def test_a_failing_log_write_while_rejecting_does_not_escape(session, monkeypatch):
    """Being turned away by the lock is a normal result even if the log write fails."""
    acquire_lock(session, LOCK_NAME, "someone-else", timedelta(hours=1), NOW)

    def broken(*_args):
        raise _db_error("log write failed")

    monkeypatch.setattr(runs, "finish_refresh", broken)

    result = _run(session, lambda _lease: RunResult("succeeded"))

    assert result.status == "rejected"
