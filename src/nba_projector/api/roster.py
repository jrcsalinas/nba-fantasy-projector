"""Pure helpers that turn a roster and a snapshot into API rows and waiver suggestions."""

from collections.abc import Mapping, Sequence

from nba_projector.api.schemas import RosterRow, WaiverCandidate, WaiverGroup
from nba_projector.domain.models import Category, RankedPlayer, Trend
from nba_projector.domain.ranking import compute_trends
from nba_projector.domain.roster import (
    Roster,
    RosterPlayer,
    team_totals,
    waiver_candidates,
    weakest_categories,
)

AVAILABILITY_NOTE = (
    "Availability is estimated by an overall rank cutoff, not your league's actual free agents."
)


def _z_by_name(player: RankedPlayer) -> dict[str, float]:
    return {cat.value: value for cat, value in player.z.items()}


def _slot_rows(
    slot: str,
    members: Sequence[RosterPlayer],
    by_id: Mapping[int, RankedPlayer],
    trends: Mapping[int, Trend],
    sort: str,
) -> list[RosterRow]:
    ranked = sorted(
        (m for m in members if m.player_id in by_id),
        key=lambda m: (by_id[m.player_id].ranks[sort], m.player_id),
    )
    rows = [
        RosterRow(
            slot=slot,  # type: ignore[arg-type]
            ranked=True,
            rank=by_id[m.player_id].ranks[sort],
            trend=trends[m.player_id].change,
            is_new=trends[m.player_id].is_new,
            player_id=m.player_id,
            player_name=by_id[m.player_id].player_name,
            total=by_id[m.player_id].total,
            z=_z_by_name(by_id[m.player_id]),
        )
        for m in ranked
    ]
    rows.extend(
        RosterRow(
            slot=slot,  # type: ignore[arg-type]
            ranked=False,
            rank=None,
            trend=None,
            is_new=False,
            player_id=m.player_id,
            player_name=m.name,
            total=None,
            z=None,
        )
        for m in members
        if m.player_id not in by_id
    )
    return rows


def build_roster_rows(
    roster: Roster,
    players: Sequence[RankedPlayer],
    previous: Sequence[RankedPlayer] | None,
    sort: str,
) -> list[RosterRow]:
    """Rows for my players: active first, then IL; ranked ones by the ``sort`` rank.

    Rostered players missing from the snapshot are listed last in their slot, unranked.

    Args:
        roster: My roster.
        players: The latest snapshot.
        previous: The most recent earlier snapshot, or ``None``.
        sort: A key from ``SORT_KEYS``.
    """
    by_id = {p.player_id: p for p in players}
    trends = compute_trends(
        {pid: p.ranks[sort] for pid, p in by_id.items()},
        None if previous is None else {p.player_id: p.ranks[sort] for p in previous},
    )
    return [
        *_slot_rows("active", roster.active, by_id, trends, sort),
        *_slot_rows("il", roster.il, by_id, trends, sort),
    ]


def build_team_summary(
    roster: Roster, players: Sequence[RankedPlayer]
) -> tuple[dict[Category, float], list[Category]]:
    """Team category totals over active ranked players, and the three weakest categories.

    Returns empty results when none of my active players is ranked.
    """
    active_ids = {m.player_id for m in roster.active}
    active = [p for p in players if p.player_id in active_ids]
    if not active:
        return {}, []
    totals = team_totals(active)
    return totals, weakest_categories(totals)


def build_waivers(
    roster: Roster, players: Sequence[RankedPlayer], cutoff: int, limit: int
) -> list[WaiverGroup]:
    """Best likely-available players for each of my weakest categories."""
    totals, weak = build_team_summary(roster, players)
    return [
        WaiverGroup(
            category=cat.value,
            team_total=totals[cat],
            candidates=[
                WaiverCandidate(
                    player_id=p.player_id,
                    player_name=p.player_name,
                    overall_rank=p.ranks["overall"],
                    total=p.total,
                    category_z=p.z[cat],
                    z=_z_by_name(p),
                )
                for p in waiver_candidates(players, roster.ids, cat, cutoff, limit)
            ],
        )
        for cat in weak
    ]
