"""Tests for the roster history: append-only, and written in the caller's transaction."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from nbaproj.db.models import AppendOnlyError, Player, RosterChange, RosterEntry
from nbaproj.roster.directory import PlayerDirectory
from nbaproj.roster.service import RosterService

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(name="service")
def service_fixture(session) -> RosterService:
    """A roster service with one known player."""
    directory = PlayerDirectory([(1, "Luka Dončić")])
    return RosterService(session, "josh", directory, clock=lambda: NOW)


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_history_rows_cannot_be_updated_through_the_orm(session, service):
    """Changing a history row is refused, and the stored row stays as it was."""
    service.add(1)
    session.commit()
    change = session.scalars(select(RosterChange)).one()

    change.action = "remove"
    with pytest.raises(AppendOnlyError):
        session.commit()
    session.rollback()

    assert session.scalars(select(RosterChange.action)).one() == "add"


def test_history_rows_cannot_be_deleted_through_the_orm(session, service):
    """Deleting a history row is refused, and the row is still there."""
    service.add(1)
    session.commit()
    change = session.scalars(select(RosterChange)).one()

    session.delete(change)
    with pytest.raises(AppendOnlyError):
        session.commit()
    session.rollback()

    assert _count(session, RosterChange) == 1


def test_a_rollback_undoes_both_the_roster_change_and_its_history(session, service):
    """The service flushes but never commits, so the caller's rollback undoes everything."""
    service.add(1)
    assert _count(session, RosterEntry) == 1 and _count(session, RosterChange) == 1

    session.rollback()

    assert _count(session, RosterEntry) == 0
    assert _count(session, RosterChange) == 0
    assert _count(session, Player) == 0
