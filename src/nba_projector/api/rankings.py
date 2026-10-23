"""Pure helpers that turn saved snapshots into API rows."""

from collections.abc import Sequence
from datetime import date

from nba_projector.api.schemas import RankingRow
from nba_projector.domain.models import RankedPlayer
from nba_projector.domain.ranking import compute_trends


def build_rows(
    players: Sequence[RankedPlayer],
    previous: Sequence[RankedPlayer] | None,
    sort: str,
    limit: int,
) -> list[RankingRow]:
    """Order players by the ``sort`` rank and attach each one's trend for that rank.

    Args:
        players: The latest snapshot.
        previous: The most recent earlier snapshot, or ``None`` if there is none.
        sort: A key from ``SORT_KEYS``.
        limit: Maximum rows to return.

    Returns:
        Rows ordered by the ``sort`` rank.
    """
    trends = compute_trends(
        {p.player_id: p.ranks[sort] for p in players},
        None if previous is None else {p.player_id: p.ranks[sort] for p in previous},
    )
    ordered = sorted(players, key=lambda p: (p.ranks[sort], p.player_id))[:limit]
    return [
        RankingRow(
            rank=p.ranks[sort],
            trend=trends[p.player_id].change,
            is_new=trends[p.player_id].is_new,
            player_id=p.player_id,
            player_name=p.player_name,
            total=p.total,
            z={cat.value: value for cat, value in p.z.items()},
        )
        for p in ordered
    ]


def is_stale(as_of: date, today: date) -> bool:
    """Return True when the snapshot is more than one day older than ``today``."""
    return (today - as_of).days > 1
