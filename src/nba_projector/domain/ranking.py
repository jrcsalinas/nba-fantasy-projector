"""Pure 9-category z-score ranking."""

import math
from collections.abc import Callable, Mapping, Sequence

from nba_projector.domain.models import Category, PlayerAverages, RankedPlayer, Trend, Window

MIN_GAMES: dict[Window, int] = {Window.SEASON: 10, Window.LAST_15: 5, Window.LAST_30: 10}
MIN_MINUTES_PG = 12.0

_COUNTING = {
    Category.PTS: "pts",
    Category.REB: "reb",
    Category.AST: "ast",
    Category.STL: "stl",
    Category.BLK: "blk",
    Category.FG3M: "fg3m",
}


def _pool_pct(made: Sequence[float], attempted: Sequence[float]) -> float:
    total_attempted = sum(attempted)
    return sum(made) / total_attempted if total_attempted else 0.0


def _category_values(pool: Sequence[PlayerAverages]) -> dict[Category, list[float]]:
    """Raw per-player value per category; TO is negated so higher is always better."""
    fg_pct = _pool_pct([p.fgm for p in pool], [p.fga for p in pool])
    ft_pct = _pool_pct([p.ftm for p in pool], [p.fta for p in pool])
    values = {cat: [float(getattr(p, attr)) for p in pool] for cat, attr in _COUNTING.items()}
    values[Category.FG_PCT] = [p.fgm - fg_pct * p.fga for p in pool]
    values[Category.FT_PCT] = [p.ftm - ft_pct * p.fta for p in pool]
    values[Category.TOV] = [-p.tov for p in pool]
    return values


def _z_scores(values: Sequence[float]) -> list[float]:
    mean = math.fsum(values) / len(values)
    std = math.sqrt(math.fsum((v - mean) ** 2 for v in values) / len(values))
    if std == 0.0:
        return [0.0] * len(values)
    return [(v - mean) / std for v in values]


def _assign_ranks(
    ids: Sequence[int],
    score: Callable[[int], float],
    overall_rank: dict[int, int] | None = None,
) -> dict[int, int]:
    """Rank ids by ``score`` descending; ties go to overall rank, then player id."""
    ordered = sorted(ids, key=lambda i: (-score(i), overall_rank[i] if overall_rank else 0, i))
    return {player_id: position for position, player_id in enumerate(ordered, start=1)}


def compute_trends(
    today: Mapping[int, int], previous: Mapping[int, int] | None
) -> dict[int, Trend]:
    """Compare today's ranks with a previous snapshot's ranks.

    Args:
        today: ``player_id`` to rank today.
        previous: ``player_id`` to rank in the previous snapshot, or ``None`` if none exists.

    Returns:
        ``player_id`` to ``Trend``. Without a previous snapshot every change is ``None``;
        a player missing from it is flagged new.
    """
    if previous is None:
        return {pid: Trend(None, False) for pid in today}
    return {
        pid: Trend(previous[pid] - rank, False) if pid in previous else Trend(None, True)
        for pid, rank in today.items()
    }


def rank_players(
    averages: Sequence[PlayerAverages], min_games: int, min_minutes_pg: float
) -> list[RankedPlayer]:
    """Rank eligible players by the sum of nine category z-scores.

    Args:
        averages: Per-game averages, one per player.
        min_games: Minimum games played to be eligible.
        min_minutes_pg: Minimum minutes per game to be eligible.

    Returns:
        Ranked players sorted by overall rank; empty if nobody is eligible.
    """
    pool = [p for p in averages if p.games >= min_games and p.minutes_pg >= min_minutes_pg]
    if not pool:
        return []

    z_by_cat = {cat: _z_scores(vals) for cat, vals in _category_values(pool).items()}
    ids = [p.player_id for p in pool]
    index = {p.player_id: i for i, p in enumerate(pool)}
    totals = {pid: math.fsum(z_by_cat[cat][index[pid]] for cat in Category) for pid in ids}

    overall = _assign_ranks(ids, totals.__getitem__)
    cat_ranks = {
        cat: _assign_ranks(ids, lambda pid, c=cat: z_by_cat[c][index[pid]], overall)
        for cat in Category
    }

    ranked = [
        RankedPlayer(
            player_id=p.player_id,
            player_name=p.player_name,
            z={cat: z_by_cat[cat][index[p.player_id]] for cat in Category},
            total=totals[p.player_id],
            ranks={
                "overall": overall[p.player_id],
                **{cat.value: cat_ranks[cat][p.player_id] for cat in Category},
            },
        )
        for p in pool
    ]
    return sorted(ranked, key=lambda r: r.ranks["overall"])
