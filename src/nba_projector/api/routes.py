"""Version 1 API routes."""

from datetime import date, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from nba_projector.adapters.repository import Repository
from nba_projector.api.rankings import build_rows, is_stale
from nba_projector.api.schemas import RankingsResponse, SortKey
from nba_projector.config import EASTERN, get_settings
from nba_projector.domain.models import Window

router = APIRouter(prefix="/api/v1")


class HelloResponse(BaseModel):
    """Response body for the hello endpoint."""

    message: str


@lru_cache
def get_repository() -> Repository:
    """Dependency: the repository for the configured database."""
    return Repository(get_settings().db_path)


def get_today() -> date:
    """Dependency: today's date in US Eastern time (overridable in tests)."""
    return datetime.now(EASTERN).date()


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
    dates = repo.snapshot_dates(window, limit=2)
    if dates:
        as_of = dates[0]
        previous = repo.load_snapshot(dates[1], window) if len(dates) > 1 else None
        rows = build_rows(repo.load_snapshot(as_of, window), previous, sort, limit)
    else:
        # A window nobody qualifies for stores no rows, so fall back to the newest snapshot of
        # any window: data exists, this window is just empty.
        latest = _latest_snapshot_date(repo)
        if latest is None:
            raise HTTPException(status_code=404, detail="No rankings available yet")
        as_of, rows = latest, []
    return RankingsResponse(
        window=window, sort=sort, as_of=as_of, is_stale=is_stale(as_of, today), rows=rows
    )


def _latest_snapshot_date(repo: Repository) -> date | None:
    dates = [d for w in Window for d in repo.snapshot_dates(w, limit=1)]
    return max(dates, default=None)
