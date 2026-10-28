"""Tests for the expiring job lock."""

import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from nbaproj.db.locks import acquire_lock, release_lock
from nbaproj.db.models import Base, JobLock
from nbaproj.db.session import create_db_engine

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


def test_two_connections_racing_for_a_free_lock_produce_exactly_one_winner(tmp_path):
    """Two writers on separate connections cannot both take a free lock."""
    url = f"sqlite:///{(tmp_path / 'race.db').as_posix()}"
    setup_engine = create_db_engine(url)
    Base.metadata.create_all(setup_engine)
    setup_engine.dispose()
    start_together = threading.Barrier(2)
    outcomes: dict[str, bool] = {}

    def contend(holder: str) -> None:
        engine = create_db_engine(url)
        try:
            with Session(engine) as db_session:
                start_together.wait(timeout=10)
                outcomes[holder] = acquire_lock(db_session, "refresh", holder, TTL, NOW)
        finally:
            engine.dispose()

    threads = [threading.Thread(target=contend, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(outcomes.values()) == [False, True]
