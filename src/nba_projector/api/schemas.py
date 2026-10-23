"""Response models for the rankings API."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from nba_projector.domain.models import SORT_KEYS, Window

SortKey = Literal[*SORT_KEYS]


class RankingRow(BaseModel):
    """One player's row for the selected window and sort key."""

    rank: int
    trend: int | None
    is_new: bool
    player_id: int
    player_name: str
    total: float
    z: dict[str, float]


class RankingsResponse(BaseModel):
    """Rankings for one window, as of the latest saved snapshot."""

    window: Window
    sort: str
    as_of: date
    is_stale: bool
    rows: list[RankingRow]
