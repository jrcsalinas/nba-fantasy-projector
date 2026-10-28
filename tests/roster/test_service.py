"""Tests for changing and reading a roster."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from nbaproj.db.models import Player, RosterChange, RosterEntry
from nbaproj.roster.directory import PlayerDirectory, default_directory
from nbaproj.roster.service import RosterService, UnknownPlayerError

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
DIRECTORY = PlayerDirectory(
    [(1, "Luka Dončić"), (2, "Jayson Tatum"), (3, "Nikola Jokić")]
)


class Clock:
    """A clock that tests can move forward, so changes get distinct, known times."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int = 1) -> None:
        """Move the clock forward."""
        self.now += timedelta(minutes=minutes)


@pytest.fixture(name="clock")
def clock_fixture() -> Clock:
    """The service's clock."""
    return Clock()


@pytest.fixture(name="service")
def service_fixture(session, clock) -> RosterService:
    """A roster service for the owner "josh" with a small directory and a movable clock."""
    return RosterService(session, "josh", DIRECTORY, clock=clock)


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_add_puts_the_player_on_the_roster_and_records_history(session, service):
    """The roster row and one history row are written, with the time and the source."""
    result = service.add(1)

    assert (result.changed, result.player_id, result.name) == (True, 1, "Luka Dončić")
    entry = session.get(RosterEntry, ("josh", 1))
    assert (entry.updated_at, entry.source) == (NOW, "manual")
    change = session.scalars(select(RosterChange)).one()
    assert (change.owner, change.player_id, change.action) == ("josh", 1, "add")
    assert (change.changed_at, change.source) == (NOW, "manual")


def test_add_creates_the_player_row_from_the_directory(session, service):
    """A player we have no game log for yet still gets a player row, named by the directory."""
    assert session.get(Player, 2) is None

    service.add(2)

    assert session.get(Player, 2).full_name == "Jayson Tatum"


def test_add_leaves_an_existing_player_name_alone(session, service):
    """The player row a game load made is not overwritten by the directory's spelling."""
    session.add(Player(player_id=1, full_name="Luka Doncic (from a box score)"))
    session.commit()

    result = service.add(1)

    assert session.get(Player, 1).full_name == "Luka Doncic (from a box score)"
    assert result.name == "Luka Doncic (from a box score)"


def test_adding_twice_changes_nothing_the_second_time(session, service):
    """A second add is a no-op that says so and writes no history."""
    first = service.add(3)
    second = service.add(3)

    assert (first.changed, second.changed) == (True, False)
    assert _count(session, RosterEntry) == 1
    assert _count(session, RosterChange) == 1


def test_adding_an_unknown_player_raises_and_writes_nothing(session, service):
    """An id the directory does not know is refused before anything is written."""
    with pytest.raises(UnknownPlayerError):
        service.add(999)

    assert (_count(session, Player), _count(session, RosterEntry)) == (0, 0)
    assert _count(session, RosterChange) == 0


@pytest.mark.parametrize("owner", ["", "x" * 65])
def test_service_rejects_a_bad_owner(session, owner):
    """An owner is 1 to 64 characters."""
    with pytest.raises(ValueError, match="owner"):
        RosterService(session, owner, DIRECTORY, clock=lambda: NOW)


def test_service_rejects_a_bad_source_and_writes_nothing(session, service):
    """Only manual and yahoo are known sources."""
    with pytest.raises(ValueError, match="source"):
        service.add(1, source="carrier-pigeon")

    assert _count(session, RosterEntry) == 0


def test_remove_deletes_the_row_and_records_history(session, service, clock):
    """Removing takes the player off the roster and appends a remove row."""
    service.add(1)
    clock.advance()

    result = service.remove(1)

    assert (result.changed, result.player_id, result.name) == (True, 1, "Luka Dončić")
    assert session.get(RosterEntry, ("josh", 1)) is None
    history = session.scalars(select(RosterChange).order_by(RosterChange.id)).all()
    assert [(c.action, c.changed_at) for c in history] == [
        ("add", NOW),
        ("remove", NOW + timedelta(minutes=1)),
    ]


def test_removing_someone_not_on_the_roster_changes_nothing(session, service):
    """A remove for a player who is not there is a no-op, even for an id nobody knows."""
    on_the_bench = service.remove(2)
    nobody = service.remove(999)

    assert (on_the_bench.changed, nobody.changed) == (False, False)
    assert _count(session, RosterChange) == 0


def test_history_outlives_the_removed_roster_row(session, service):
    """After a remove the roster row is gone but both history rows remain."""
    service.add(3)
    service.remove(3)

    assert _count(session, RosterEntry) == 0
    assert _count(session, RosterChange) == 2


def test_players_are_listed_by_name(service):
    """The roster reads back alphabetically, ignoring accents, with names and times."""
    service.add(3)
    service.add(2)
    service.add(1)

    listed = service.players()

    assert [p.name for p in listed] == ["Jayson Tatum", "Luka Dončić", "Nikola Jokić"]
    assert [p.player_id for p in listed] == [2, 1, 3]
    assert all(p.updated_at == NOW for p in listed)


def test_changes_are_listed_newest_first_with_names_and_a_limit(service, clock):
    """Recent changes come newest first, with the player's name, and can be limited."""
    service.add(1)
    clock.advance()
    service.add(2)
    clock.advance()
    service.remove(1)

    changes = service.changes()

    assert [(c.action, c.name) for c in changes] == [
        ("remove", "Luka Dončić"),
        ("add", "Jayson Tatum"),
        ("add", "Luka Dončić"),
    ]
    assert changes[0].changed_at == NOW + timedelta(minutes=2)
    assert [c.action for c in service.changes(limit=2)] == ["remove", "add"]


def test_one_owner_never_sees_another_owners_roster_or_history(session, service, clock):
    """Every read is scoped to the owner, even when two owners share a database."""
    other = RosterService(session, "sam", DIRECTORY, clock=clock)
    service.add(1)
    other.add(2)

    assert [p.name for p in service.players()] == ["Luka Dončić"]
    assert [p.name for p in other.players()] == ["Jayson Tatum"]
    assert [c.name for c in service.changes()] == ["Luka Dončić"]
    assert [c.name for c in other.changes()] == ["Jayson Tatum"]
    assert other.remove(1).changed is False


def test_service_uses_the_real_directory_and_the_real_clock_by_default(session):
    """With nothing injected the service uses nba_api's active list and the current UTC time."""
    [match] = default_directory().search("jokic")
    before = datetime.now(timezone.utc)

    result = RosterService(session, "josh").add(match.player_id)

    assert (result.changed, result.name) == (True, match.name)
    assert session.get(Player, match.player_id).full_name == match.name
    recorded = session.get(RosterEntry, ("josh", match.player_id)).updated_at
    assert before <= recorded <= datetime.now(timezone.utc)
