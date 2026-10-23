"""Version 1 API routes."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from nba_projector.adapters.repository import Repository
from nba_projector.api.dependencies import get_repository, get_today
from nba_projector.api.rankings import build_rows, is_stale
from nba_projector.api.schemas import RankingsResponse, SortKey
from nba_projector.api.snapshots import load_latest
from nba_projector.domain.models import Window

router = APIRouter(prefix="/api/v1")


class HelloResponse(BaseModel):
    """Response body for the hello endpoint."""

    message: str


@router.get("/hello", response_model=HelloResponse, summary="Hello world")
def hello() -> HelloResponse:
    """Return a greeting to confirm the API is running."""
    return HelloResponse(message="Hello, NBA Fantasy!")


@router.get("/rankings", response_model=RankingsResponse, summary="9-cat player rankings")
def get_rankings(
    repo: Annotated[Repository, Depends(get_repository)],
    today: Annotated[date, Depends(get_today)],
    window: Window = Window.SEASON,
    sort: SortKey = "overall",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> RankingsResponse:
    """Return the latest saved ranking for a window, ordered by the chosen sort key.

    Trend compares each player's rank for the same sort key with the most recent earlier
    snapshot of the same window.
    """
    latest = load_latest(repo, window)
    return RankingsResponse(
        window=window,
        sort=sort,
        as_of=latest.as_of,
        is_stale=is_stale(latest.as_of, today),
        rows=build_rows(latest.players, latest.previous, sort, limit),
    )
