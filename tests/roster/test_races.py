"""Tests for roster changes made by two sessions at once."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nbaproj.db.models import Base, RosterChange, RosterEntry
from nbaproj.db.session import create_db_engine
from nbaproj.roster.directory import PlayerDirectory
from nbaproj.roster.service import RosterService

DIRECTORY = PlayerDirectory([(1, "Luka Doncic")])


@pytest.fixture(name="engine")
def engine_fixture(tmp_path):
    """A SQLite file database, so two sessions can really see the same rows."""
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'races.db').as_posix()}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_two_sessions_removing_the_same_player_record_one_removal(engine):
    """Both requests read the row before either deletes it; only one removal is recorded."""
    with Session(engine) as setup:
        RosterService(setup, "josh", DIRECTORY).add(1)
        setup.commit()
    first, second = Session(engine), Session(engine)
    # Each request has already read the row. The sessions keep these objects only weakly,
    # so they are held here for as long as the test runs, as a request in flight holds them.
    already_read = [session.get(RosterEntry, ("josh", 1)) for session in (first, second)]
    assert all(entry is not None for entry in already_read)

    removed_by_first = RosterService(first, "josh", DIRECTORY).remove(1)
    first.commit()
    removed_by_second = RosterService(second, "josh", DIRECTORY).remove(1)
    second.commit()
    first.close()
    second.close()

    assert len(already_read) == 2
    assert removed_by_first.changed is True
    assert removed_by_second.changed is False
    with Session(engine) as check:
        actions = list(check.scalars(select(RosterChange.action).order_by(RosterChange.id)))
    assert actions == ["add", "remove"]
