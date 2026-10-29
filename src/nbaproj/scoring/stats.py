"""Per-player stats over a period, from game logs.

A game log frame has one row per player per game with the columns ``player_id``,
``game_day`` (a date), ``minutes``, ``pts``, ``reb``, ``ast``, ``stl``, ``blk``, ``tpm``,
``fgm``, ``fga``, ``ftm``, ``fta``, ``tov`` and ``usage_pct`` (NaN when unknown).
"""

from datetime import date

import pandas as pd

# Averaged per game as they are.
PER_GAME_COLUMNS = ("minutes", "pts", "reb", "ast", "stl", "blk", "tpm", "tov")
SHOT_COLUMNS = ("fgm", "fga", "ftm", "fta")
STAT_COLUMNS = [
    "games",
    *PER_GAME_COLUMNS,
    "fg_pct",
    "ft_pct",
    "fg_impact",
    "ft_impact",
    "usage",
]


def _league_percentage(made: pd.Series, attempted: pd.Series) -> float:
    total = attempted.sum()
    return float(made.sum() / total) if total else 0.0


def _pooled_percentage(made: pd.Series, attempted: pd.Series) -> pd.Series:
    """Made over attempted per player; NaN for a player with no attempts."""
    return made / attempted.where(attempted > 0)


def period_stats(logs: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """One row per player who played between ``start`` and ``end`` (both days included).

    Counting stats and minutes are per-game averages. ``fg_pct`` and ``ft_pct`` are pooled
    over attempts (made shots divided by attempts, not a mean of nightly percentages).
    ``fg_impact`` and ``ft_impact`` are made shots above what an average player would
    have made on the same attempts, per game; the average is pooled over every player's
    games in the period. ``usage`` is the mean over games that have a usage figure, and
    NaN if none does. The input frame is not modified.
    """
    period = logs[(logs["game_day"] >= start) & (logs["game_day"] <= end)]
    if period.empty:
        return pd.DataFrame(columns=STAT_COLUMNS, index=pd.Index([], name="player_id"))
    by_player = period.groupby("player_id")
    games = by_player.size()
    totals = by_player[list(SHOT_COLUMNS)].sum()
    stats = by_player[list(PER_GAME_COLUMNS)].mean()
    stats.insert(0, "games", games)
    stats["fg_pct"] = _pooled_percentage(totals["fgm"], totals["fga"])
    stats["ft_pct"] = _pooled_percentage(totals["ftm"], totals["fta"])
    league_fg = _league_percentage(period["fgm"], period["fga"])
    league_ft = _league_percentage(period["ftm"], period["fta"])
    stats["fg_impact"] = (totals["fgm"] - league_fg * totals["fga"]) / games
    stats["ft_impact"] = (totals["ftm"] - league_ft * totals["fta"]) / games
    stats["usage"] = by_player["usage_pct"].mean()
    return stats[STAT_COLUMNS]
