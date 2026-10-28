"""Changing a roster: add and remove players, and keep the history.

The service flushes but never commits. The caller commits, so a change and its history
row succeed or fail together.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from nbaproj.db.models import Player, RosterChange, RosterEntry
from nbaproj.roster.directory import PlayerDirectory, default_directory, normalize_name

MANUAL = "manual"
YAHOO = "yahoo"
SOURCES = frozenset({MANUAL, YAHOO})
ADD = "add"
REMOVE = "remove"
DEFAULT_CHANGES_LIMIT = 50
MAX_OWNER_LENGTH = RosterEntry.owner.type.length


class RosterError(Exception):
    """Something a roster change cannot do."""


class UnknownPlayerError(RosterError):
    """The player id is not one the directory knows."""


@dataclass(frozen=True)
class ChangeResult:
    """What an add or remove did. ``changed`` is False when there was nothing to do."""

    changed: bool
    player_id: int
    name: str


@dataclass(frozen=True)
class RosterPlayer:
    """A player on the roster."""

    player_id: int
    name: str
    updated_at: datetime


@dataclass(frozen=True)
class ChangeRecord:
    """One line of the roster's history."""

    action: str
    player_id: int
    name: str
    changed_at: datetime
    source: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _check_source(source: str) -> None:
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {sorted(SOURCES)}")


class RosterService:
    """One owner's roster, read and changed through a database session."""

    def __init__(
        self,
        session: Session,
        owner: str,
        directory: PlayerDirectory | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ):
        if not owner or len(owner) > MAX_OWNER_LENGTH:
            raise ValueError(f"owner must be 1 to {MAX_OWNER_LENGTH} characters")
        self._session = session
        self._owner = owner
        self._directory = directory
        self._clock = clock

    def _directory_in_use(self) -> PlayerDirectory:
        return self._directory if self._directory is not None else default_directory()

    def add(self, player_id: int, source: str = MANUAL) -> ChangeResult:
        """Put a player on the roster, recording it. Does nothing if already there.

        A player we have no game log for yet gets a ``player`` row named by the directory;
        an existing row keeps its name. Raises ``UnknownPlayerError`` for an unknown id.
        """
        _check_source(source)
        match = self._directory_in_use().get(player_id)
        if match is None:
            raise UnknownPlayerError(f"no active player has id {player_id}")
        player = self._session.get(Player, player_id)
        if player is None:
            player = Player(player_id=player_id, full_name=match.name)
            self._session.add(player)
            self._session.flush()
        if self._session.get(RosterEntry, (self._owner, player_id)) is not None:
            return ChangeResult(False, player_id, player.full_name)
        now = self._clock()
        self._session.add(
            RosterEntry(owner=self._owner, player_id=player_id, updated_at=now, source=source)
        )
        self._session.add(
            RosterChange(
                owner=self._owner,
                player_id=player_id,
                action=ADD,
                changed_at=now,
                source=source,
            )
        )
        self._session.flush()
        return ChangeResult(True, player_id, player.full_name)

    def remove(self, player_id: int, source: str = MANUAL) -> ChangeResult:
        """Take a player off the roster, recording it. Does nothing if not on it."""
        _check_source(source)
        player = self._session.get(Player, player_id)
        name = player.full_name if player is not None else ""
        entry = self._session.get(RosterEntry, (self._owner, player_id))
        if entry is None:
            return ChangeResult(False, player_id, name)
        self._session.delete(entry)
        self._session.add(
            RosterChange(
                owner=self._owner,
                player_id=player_id,
                action=REMOVE,
                changed_at=self._clock(),
                source=source,
            )
        )
        self._session.flush()
        return ChangeResult(True, player_id, name)

    def players(self) -> list[RosterPlayer]:
        """The owner's roster, sorted by name (accents ignored)."""
        rows = self._session.execute(
            select(Player.player_id, Player.full_name, RosterEntry.updated_at)
            .join(RosterEntry, RosterEntry.player_id == Player.player_id)
            .where(RosterEntry.owner == self._owner)
        )
        listed = [RosterPlayer(player_id, name, updated_at) for player_id, name, updated_at in rows]
        return sorted(listed, key=lambda player: normalize_name(player.name))

    def changes(self, limit: int = DEFAULT_CHANGES_LIMIT) -> list[ChangeRecord]:
        """The owner's most recent roster changes, newest first."""
        rows = self._session.execute(
            select(
                RosterChange.action,
                RosterChange.player_id,
                Player.full_name,
                RosterChange.changed_at,
                RosterChange.source,
            )
            .join(Player, Player.player_id == RosterChange.player_id)
            .where(RosterChange.owner == self._owner)
            .order_by(RosterChange.changed_at.desc(), RosterChange.id.desc())
            .limit(limit)
        )
        return [ChangeRecord(*row) for row in rows]
