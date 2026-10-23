"""Shared domain types for the 9-category ranking."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Category(StrEnum):
    """The nine fantasy categories."""

    PTS = "pts"
    REB = "reb"
    AST = "ast"
    STL = "stl"
    BLK = "blk"
    FG3M = "fg3m"
    FG_PCT = "fg_pct"
    FT_PCT = "ft_pct"
    TOV = "tov"


class Window(StrEnum):
    """Stat windows a ranking can be computed over."""

    SEASON = "season"
    LAST_15 = "l15"
    LAST_30 = "l30"


SORT_KEYS: tuple[str, ...] = ("overall", *[c.value for c in Category])


@dataclass(frozen=True)
class GameLog:
    """One player's box score line for one game."""

    season: str
    game_id: str
    game_date: date
    player_id: int
    player_name: str
    minutes: float
    pts: int
    reb: int
    ast: int
    stl: int
    blk: int
    fg3m: int
    fgm: int
    fga: int
    ftm: int
    fta: int
    tov: int


@dataclass(frozen=True)
class PlayerAverages:
    """Per-game averages for one player over a window."""

    player_id: int
    player_name: str
    games: int
    minutes_pg: float
    pts: float
    reb: float
    ast: float
    stl: float
    blk: float
    fg3m: float
    fgm: float
    fga: float
    ftm: float
    fta: float
    tov: float


@dataclass(frozen=True)
class RankedPlayer:
    """A player's category z-scores, total, and ranks within one window.

    ``averages`` are the per-game stats the ranking used (for the Season window, blended with
    last season until 20 games). ``games_played`` is the games actually played in the window,
    which differs from ``averages.games`` when prior-season stats are blended in.
    """

    player_id: int
    player_name: str
    z: dict[Category, float]
    total: float
    ranks: dict[str, int]
    averages: PlayerAverages | None = None
    games_played: int | None = None


@dataclass(frozen=True)
class Trend:
    """Rank movement versus a previous snapshot; positive ``change`` means moved up."""

    change: int | None
    is_new: bool
