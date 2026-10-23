"""Pure roster math: team category totals, weak categories, and waiver candidates."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from nba_projector.domain.models import Category, RankedPlayer


@dataclass(frozen=True)
class RosterPlayer:
    """A rostered player; ``player_id`` is the upstream id, ``name`` is for readability."""

    player_id: int
    name: str


@dataclass(frozen=True)
class Roster:
    """My team: active players plus those on the injured list."""

    active: tuple[RosterPlayer, ...]
    il: tuple[RosterPlayer, ...] = ()

    @property
    def ids(self) -> set[int]:
        """Every rostered ``player_id``, including IL."""
        return {p.player_id for p in (*self.active, *self.il)}


def team_totals(players: Sequence[RankedPlayer]) -> dict[Category, float]:
    """Sum each category's z-score across players.

    Args:
        players: The roster's ranked players (callers leave out IL players).

    Returns:
        Total z-score per category; all zeros for an empty roster. Higher is better, including
        TO, whose z-scores are already reversed.
    """
    return {cat: sum(p.z[cat] for p in players) for cat in Category}


def weakest_categories(totals: Mapping[Category, float], n: int = 3) -> list[Category]:
    """Return the ``n`` categories with the lowest totals, weakest first.

    Ties keep the order of ``Category``.

    Args:
        totals: Team z-score total per category.
        n: How many categories to return.
    """
    order = list(Category)
    return sorted(order, key=lambda cat: (totals[cat], order.index(cat)))[:n]


def waiver_candidates(
    snapshot: Sequence[RankedPlayer],
    roster_ids: Collection[int],
    category: Category,
    cutoff: int,
    limit: int,
) -> list[RankedPlayer]:
    """Pick likely-available players who are strongest in a category.

    A player counts as likely available when they are not on the roster and rank below
    ``cutoff`` overall; this is an estimate, not real league availability.

    Args:
        snapshot: Ranked players for one window.
        roster_ids: ``player_id`` of every player on my roster, including IL.
        category: The category to improve.
        cutoff: Overall rank at or above which a player is assumed to be rostered elsewhere.
        limit: Maximum candidates to return.

    Returns:
        Candidates ordered by that category's z-score, then overall rank, then ``player_id``.
    """
    pool = [p for p in snapshot if p.player_id not in roster_ids and p.ranks["overall"] > cutoff]
    pool.sort(key=lambda p: (-p.z[category], p.ranks["overall"], p.player_id))
    return pool[:limit]
