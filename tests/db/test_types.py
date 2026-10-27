"""Tests for custom column types."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, insert, select
from sqlalchemy.exc import StatementError

from nbaproj.db.types import UtcDateTime

METADATA = MetaData()
EVENTS = Table(
    "events",
    METADATA,
    Column("id", Integer, primary_key=True),
    Column("happened_at", UtcDateTime),
)


@pytest.fixture(name="engine")
def engine_fixture():
    """An in-memory SQLite engine with the events table, closed after the test."""
    engine = create_engine("sqlite://")
    METADATA.create_all(engine)
    yield engine
    engine.dispose()


def test_utc_datetime_round_trips_as_aware_utc(engine):
    """A value in another timezone comes back as the same instant in UTC."""
    manila = timezone(timedelta(hours=8))
    original = datetime(2026, 10, 27, 20, 0, tzinfo=manila)

    with engine.begin() as conn:
        conn.execute(insert(EVENTS).values(id=1, happened_at=original))
        stored = conn.execute(select(EVENTS.c.happened_at)).scalar_one()

    assert stored == original
    assert stored.utcoffset() == timedelta(0)


def test_utc_datetime_passes_none_through(engine):
    """A missing value stays missing."""
    with engine.begin() as conn:
        conn.execute(insert(EVENTS).values(id=1, happened_at=None))
        stored = conn.execute(select(EVENTS.c.happened_at)).scalar_one()

    assert stored is None


def test_utc_datetime_rejects_naive_values(engine):
    """A naive datetime is refused, so local times cannot sneak in."""
    with pytest.raises(StatementError, match="naive"):
        with engine.begin() as conn:
            conn.execute(insert(EVENTS).values(id=1, happened_at=datetime(2026, 10, 27, 12, 0)))
