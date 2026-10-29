"""Trends: how a player's recent form compares with his own season.

For each category a player's trend is ``z(window) - z(season)``: how many standard
deviations his recent form sits above or below the average player, minus the same for
his whole season. The plain per-game change comes with it, so a reason can read like
"AST +2.1". Everything here is pure: frames in, results out.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from nbaproj.scoring.categories import CATEGORIES
from nbaproj.scoring.zscores import z_scores

# A player counts in a period's population, and can be scored, with at least this many games.
MIN_SEASON_GAMES = 5
MIN_WINDOW_GAMES = 2
# Fewer window games than this and the trend is given but flagged as a low sample.
LOW_SAMPLE_GAMES = 3
# The overall trend (the sum over the nine categories) at or beyond these is Hot or Cold.
# First guesses, to be checked against real data and tuned there.
HOT_AT = 2.0
COLD_AT = -2.0
# What is worth a word in a reason sentence.
MIN_MOVER_Z = 0.25
BIG_MINUTES_CHANGE = 3.0
MOVERS_SHOWN = 2
HOT, COLD, STEADY = "Hot", "Cold", "Steady"
PERCENTAGE_KEYS = frozenset(c.key for c in CATEGORIES if c.shown.endswith("_pct"))


@dataclass(frozen=True)
class CategoryTrend:
    """One category: the change in standard deviations, and the plain per-game change.

    ``change`` is None when it cannot be known (no attempts in a period); for FG% and FT%
    it is a fraction (0.04 is four percentage points).
    """

    key: str
    label: str
    z_change: float
    change: float | None = None


@dataclass(frozen=True)
class PlayerTrend:
    """A player's trend, or why there is none (``has_trend`` False and ``reason`` says why)."""

    player_id: int
    season_games: int
    window_games: int
    has_trend: bool
    reason: str
    low_sample: bool = False
    categories: tuple[CategoryTrend, ...] = ()
    minutes_change: float | None = None
    usage_change: float | None = None
    overall: float | None = None
    label: str | None = None


def trend_label(overall: float) -> str:
    """Hot at or above ``HOT_AT``, Cold at or below ``COLD_AT``, otherwise Steady."""
    if overall >= HOT_AT:
        return HOT
    if overall <= COLD_AT:
        return COLD
    return STEADY


def _games_text(count: int) -> str:
    return f"{count} game" if count == 1 else f"{count} games"


def _change_text(category: CategoryTrend, change: float) -> str:
    if category.key in PERCENTAGE_KEYS:
        return f"{category.label} {change * 100:+.1f} pts"
    return f"{category.label} {change:+.1f}"


def build_reason(
    categories: Sequence[CategoryTrend], minutes_change: float | None, window_days: int
) -> str:
    """One plain sentence: the two biggest category movers and a big minutes change.

    Movers are ranked by how far they moved in standard deviations (turnovers count the
    right way round) and shown with their plain change; moves under ``MIN_MOVER_Z`` and a
    minutes change under ``BIG_MINUTES_CHANGE`` are left out.
    """
    movers = [
        (c, c.change) for c in categories if abs(c.z_change) >= MIN_MOVER_Z and c.change is not None
    ]
    movers.sort(key=lambda mover: abs(mover[0].z_change), reverse=True)
    shown = ", ".join(_change_text(c, change) for c, change in movers[:MOVERS_SHOWN])
    period = f"over the last {window_days} days"
    big_minutes = minutes_change is not None and abs(minutes_change) >= BIG_MINUTES_CHANGE
    if shown and big_minutes:
        return f"{shown} {period}; minutes {minutes_change:+.1f}"
    if shown:
        return f"{shown} {period}"
    if big_minutes:
        return f"Minutes {minutes_change:+.1f} {period}"
    return f"No notable change {period}"


def _missing_reason(season_games: int, window_games: int, window_days: int) -> str | None:
    """Why there is nothing to compare, or None if there is."""
    if season_games == 0:
        return "no games this season"
    if season_games < MIN_SEASON_GAMES:
        return f"only {_games_text(season_games)} this season"
    if window_games == 0:
        return f"no games in the last {window_days} days"
    if window_games < MIN_WINDOW_GAMES:
        return f"only {_games_text(window_games)} in the last {window_days} days"
    return None


def _games(stats: pd.DataFrame, player_id: int) -> int:
    return int(stats.loc[player_id, "games"]) if player_id in stats.index else 0


def _difference(window_value: float, season_value: float) -> float | None:
    """Window minus season, or None if either is unknown."""
    if pd.isna(window_value) or pd.isna(season_value):
        return None
    return float(window_value - season_value)


@dataclass(frozen=True)
class _Periods:
    """The two periods being compared: their stats, their z-scores, and the window length."""

    season: pd.DataFrame
    window: pd.DataFrame
    z_season: pd.DataFrame
    z_window: pd.DataFrame
    window_days: int


def _player_trend(player_id: int, periods: _Periods) -> PlayerTrend:
    season, window, window_days = periods.season, periods.window, periods.window_days
    z_season, z_window = periods.z_season, periods.z_window
    season_games, window_games = _games(season, player_id), _games(window, player_id)
    missing = _missing_reason(season_games, window_games, window_days)
    if missing:
        return PlayerTrend(player_id, season_games, window_games, has_trend=False, reason=missing)
    categories = tuple(
        CategoryTrend(
            key=c.key,
            label=c.label,
            z_change=float(z_window.loc[player_id, c.key] - z_season.loc[player_id, c.key]),
            change=_difference(window.loc[player_id, c.shown], season.loc[player_id, c.shown]),
        )
        for c in CATEGORIES
    )
    minutes_change = _difference(
        window.loc[player_id, "minutes"], season.loc[player_id, "minutes"]
    )
    overall = sum(c.z_change for c in categories)
    return PlayerTrend(
        player_id=player_id,
        season_games=season_games,
        window_games=window_games,
        has_trend=True,
        reason=build_reason(categories, minutes_change, window_days),
        low_sample=window_games < LOW_SAMPLE_GAMES,
        categories=categories,
        minutes_change=minutes_change,
        usage_change=_difference(window.loc[player_id, "usage"], season.loc[player_id, "usage"]),
        overall=overall,
        label=trend_label(overall),
    )


def score_trends(
    season: pd.DataFrame, window: pd.DataFrame, window_days: int, player_ids: Sequence[int]
) -> list[PlayerTrend]:
    """The trend of each player in ``player_ids``, in that order.

    ``season`` and ``window`` are period stats (see ``stats.period_stats``) for everyone
    who played; they define the populations the z-scores are measured against. A player
    with too few games in either period gets no trend and a plain reason. Inputs are not
    modified.
    """
    periods = _Periods(
        season,
        window,
        z_scores(season, MIN_SEASON_GAMES),
        z_scores(window, MIN_WINDOW_GAMES),
        window_days,
    )
    return [_player_trend(player_id, periods) for player_id in player_ids]
