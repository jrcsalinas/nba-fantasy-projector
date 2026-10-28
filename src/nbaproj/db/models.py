"""SQLAlchemy models for the core tables."""

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from nbaproj.db.types import UtcDateTime


class Base(DeclarativeBase):
    """Declarative base for all models."""


class AppendOnlyError(RuntimeError):
    """Raised when code tries to change or delete a row that may only be added."""


class Team(Base):
    """An NBA team."""

    __tablename__ = "team"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    tricode: Mapped[str] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(String(64))
    city: Mapped[str] = mapped_column(String(64))


class Player(Base):
    """An NBA player, keyed by the NBA person id."""

    __tablename__ = "player"

    player_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    full_name: Mapped[str] = mapped_column(String(128))


class Game(Base):
    """A scheduled or finished game. Doubles as the schedule."""

    __tablename__ = "game"

    game_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    season: Mapped[str] = mapped_column(String(7))
    game_type: Mapped[str] = mapped_column(String(32))
    tip_off_utc: Mapped[datetime] = mapped_column(UtcDateTime)
    home_team_id: Mapped[int] = mapped_column(Integer, ForeignKey("team.team_id"))
    away_team_id: Mapped[int] = mapped_column(Integer, ForeignKey("team.team_id"))
    status: Mapped[str] = mapped_column(String(16))


class GameLog(Base):
    """One player's box score line for one game."""

    __tablename__ = "game_log"
    __table_args__ = (Index("ix_game_log_player_id", "player_id"),)

    game_id: Mapped[str] = mapped_column(
        String(10), ForeignKey("game.game_id"), primary_key=True
    )
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("player.player_id"), primary_key=True, autoincrement=False
    )
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("team.team_id"))
    minutes: Mapped[float] = mapped_column(Float)
    pts: Mapped[int] = mapped_column(Integer)
    reb: Mapped[int] = mapped_column(Integer)
    ast: Mapped[int] = mapped_column(Integer)
    stl: Mapped[int] = mapped_column(Integer)
    blk: Mapped[int] = mapped_column(Integer)
    tpm: Mapped[int] = mapped_column(Integer)
    fgm: Mapped[int] = mapped_column(Integer)
    fga: Mapped[int] = mapped_column(Integer)
    ftm: Mapped[int] = mapped_column(Integer)
    fta: Mapped[int] = mapped_column(Integer)
    tov: Mapped[int] = mapped_column(Integer)
    usage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16))


class RosterEntry(Base):
    """A player on an owner's roster. One row per owner and player."""

    __tablename__ = "roster"

    owner: Mapped[str] = mapped_column(String(64), primary_key=True)
    player_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("player.player_id"), primary_key=True, autoincrement=False
    )
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)
    source: Mapped[str] = mapped_column(String(16))


class RosterChange(Base):
    """One add or remove on a roster. History is only ever appended to."""

    __tablename__ = "roster_history"
    __table_args__ = (Index("ix_roster_history_owner_changed_at", "owner", "changed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    player_id: Mapped[int] = mapped_column(Integer, ForeignKey("player.player_id"))
    action: Mapped[str] = mapped_column(String(8))
    changed_at: Mapped[datetime] = mapped_column(UtcDateTime)
    source: Mapped[str] = mapped_column(String(16))


@event.listens_for(RosterChange, "before_update")
@event.listens_for(RosterChange, "before_delete")
def _history_is_append_only(_mapper, _connection, _target) -> None:
    """Refuse to change or delete a history row through the ORM.

    This guards the application's own code paths. A raw bulk SQL statement does not go
    through the ORM and is not stopped by it.
    """
    raise AppendOnlyError("roster_history rows can only be added, never changed or deleted")


class RefreshLog(Base):
    """A record of one refresh run."""

    __tablename__ = "refresh_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rows_upserted: Mapped[int] = mapped_column(Integer, default=0)


class JobLock(Base):
    """A named lock with an expiry, shared by all writers."""

    __tablename__ = "job_lock"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    holder: Mapped[str] = mapped_column(String(64))
    locked_at: Mapped[datetime] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
