"""Tests for the expiring job lock."""

from datetime import datetime, timedelta, timezone

from nbaproj.db.locks import acquire_lock, release_lock
from nbaproj.db.models import JobLock

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
TTL = timedelta(minutes=5)


def test_acquire_succeeds_when_lock_is_free(session):
    """A lock nobody holds can be taken, and the row records holder and expiry."""
    acquired = acquire_lock(session, "refresh", holder="cron-1", ttl=TTL, now=NOW)

    row = session.get(JobLock, "refresh")
    assert acquired is True
    assert row.holder == "cron-1"
    assert row.locked_at == NOW
    assert row.expires_at == NOW + TTL


def test_second_holder_is_blocked_while_lock_is_held(session):
    """While the lock is unexpired, another holder cannot take it."""
    acquire_lock(session, "refresh", holder="cron-1", ttl=TTL, now=NOW)

    acquired = acquire_lock(
        session, "refresh", holder="ui-1", ttl=TTL, now=NOW + timedelta(minutes=1)
    )

    assert acquired is False
    assert session.get(JobLock, "refresh").holder == "cron-1"


def test_expired_lock_can_be_taken_by_another_holder(session):
    """Once the time-to-live has passed, another holder can take the lock."""
    acquire_lock(session, "refresh", holder="cron-1", ttl=TTL, now=NOW)
    after_expiry = NOW + TTL + timedelta(seconds=1)

    acquired = acquire_lock(session, "refresh", holder="ui-1", ttl=TTL, now=after_expiry)

    row = session.get(JobLock, "refresh")
    assert acquired is True
    assert row.holder == "ui-1"
    assert row.expires_at == after_expiry + TTL


def test_release_only_works_for_the_holder(session):
    """Only the holder can release the lock; anyone else is refused."""
    acquire_lock(session, "refresh", holder="cron-1", ttl=TTL, now=NOW)

    assert release_lock(session, "refresh", holder="ui-1") is False
    assert session.get(JobLock, "refresh") is not None

    assert release_lock(session, "refresh", holder="cron-1") is True
    assert session.get(JobLock, "refresh") is None


def test_same_holder_can_extend_its_lock(session):
    """The holder can take its own lock again to push the expiry forward."""
    acquire_lock(session, "refresh", holder="cron-1", ttl=TTL, now=NOW)
    later = NOW + timedelta(minutes=2)

    acquired = acquire_lock(session, "refresh", holder="cron-1", ttl=TTL, now=later)

    assert acquired is True
    assert session.get(JobLock, "refresh").expires_at == later + TTL
