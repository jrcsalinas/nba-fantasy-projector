"""Routes for my roster and waiver suggestions."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from nba_projector.adapters.repository import Repository
from nba_projector.api.dependencies import get_repository, get_roster, get_today
from nba_projector.api.rankings import is_stale
from nba_projector.api.roster import (
    AVAILABILITY_NOTE,
    build_roster_rows,
    build_team_summary,
    build_waivers,
)
from nba_projector.api.schemas import RosterResponse, SortKey, WaiversResponse
from nba_projector.api.snapshots import load_latest
from nba_projector.domain.models import Window
from nba_projector.domain.roster import Roster

router = APIRouter(prefix="/api/v1/roster")


@router.get("", response_model=RosterResponse, summary="My roster in the 9-cat rankings")
def get_my_roster(
    repo: Annotated[Repository, Depends(get_repository)],
    today: Annotated[date, Depends(get_today)],
    roster: Annotated[Roster, Depends(get_roster)],
    window: Window = Window.SEASON,
    sort: SortKey = "overall",
) -> RosterResponse:
    """Return my players with their ranks and trends, plus team totals per category.

    Team totals cover active players that are ranked in this window; IL players are excluded.
    """
    latest = load_latest(repo, window)
    totals, weak = build_team_summary(roster, latest.players)
    return RosterResponse(
        window=window,
        sort=sort,
        as_of=latest.as_of,
        is_stale=is_stale(latest.as_of, today),
        rows=build_roster_rows(roster, latest.players, latest.previous, sort),
        team={cat.value: value for cat, value in totals.items()},
        weak_categories=[cat.value for cat in weak],
    )


@router.get("/waivers", response_model=WaiversResponse, summary="Waiver suggestions")
def get_waiver_suggestions(
    repo: Annotated[Repository, Depends(get_repository)],
    today: Annotated[date, Depends(get_today)],
    roster: Annotated[Roster, Depends(get_roster)],
    window: Window = Window.SEASON,
    cutoff: Annotated[int, Query(ge=1, le=500)] = 150,
    limit: Annotated[int, Query(ge=1, le=25)] = 5,
) -> WaiversResponse:
    """Suggest likely-available players for my three weakest categories.

    A player counts as likely available when ranked below ``cutoff`` overall and not on my
    roster. This is an estimate, not the league's actual free agents.
    """
    latest = load_latest(repo, window)
    return WaiversResponse(
        window=window,
        as_of=latest.as_of,
        is_stale=is_stale(latest.as_of, today),
        cutoff=cutoff,
        availability_note=AVAILABILITY_NOTE,
        suggestions=build_waivers(roster, latest.players, cutoff, limit),
    )
