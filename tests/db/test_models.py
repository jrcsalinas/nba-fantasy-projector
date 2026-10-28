"""Tests for the core database models."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from nbaproj.db.models import Base, Player, RosterChange, RosterEntry

from tests.db.tables import CORE_TABLES

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)



@pytest.fixture(name="engine")
def engine_fixture():
    """An empty in-memory SQLite engine, closed after the test."""
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


def test_models_create_all_core_tables(engine):
    """The models define exactly the core tables and the two roster tables."""
    Base.metadata.create_all(engine)

    assert set(inspect(engine).get_table_names()) == CORE_TABLES


def test_game_log_has_a_player_id_index_in_the_models(engine):
    """Per-player lookups (projections) must not scan the whole game log."""
    Base.metadata.create_all(engine)

    indexes = inspect(engine).get_indexes("game_log")

    assert any(index["column_names"] == ["player_id"] for index in indexes)


def _entry(owner: str, player_id: int) -> RosterEntry:
    return RosterEntry(owner=owner, player_id=player_id, updated_at=NOW, source="manual")


def test_roster_rejects_a_duplicate_player_and_an_unknown_player(session):
    """A player is on an owner's roster once, must exist, and may be on another owner's."""
    session.add(Player(player_id=1, full_name="Test Player"))
    session.commit()
    session.add_all([_entry("josh", 1), _entry("sam", 1)])
    session.commit()

    session.add(_entry("josh", 1))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.add(_entry("josh", 999))
    with pytest.raises(IntegrityError):
        session.commit()


def test_roster_history_rejects_an_unknown_player(session):
    """A history row must point at a real player."""
    session.add(
        RosterChange(owner="josh", player_id=999, action="add", changed_at=NOW, source="manual")
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_roster_history_has_an_owner_and_time_index(engine):
    """Listing an owner's recent changes must not scan the whole history."""
    Base.metadata.create_all(engine)

    indexes = inspect(engine).get_indexes("roster_history")

    assert any(index["column_names"] == ["owner", "changed_at"] for index in indexes)
