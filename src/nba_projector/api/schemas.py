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


class RosterRow(BaseModel):
    """One of my players; unranked players have no rank, trend, or z-scores."""

    slot: Literal["active", "il"]
    ranked: bool
    rank: int | None
    trend: int | None
    is_new: bool
    player_id: int
    player_name: str
    total: float | None
    z: dict[str, float] | None


class RosterResponse(BaseModel):
    """My roster for one window, with team category totals over active ranked players."""

    window: Window
    sort: str
    as_of: date
    is_stale: bool
    rows: list[RosterRow]
    team: dict[str, float]
    weak_categories: list[str]


class WaiverCandidate(BaseModel):
    """A likely-available player and the category z-score that earns the suggestion."""

    player_id: int
    player_name: str
    overall_rank: int
    total: float
    category_z: float
    z: dict[str, float]


class WaiverGroup(BaseModel):
    """Candidates for one of my weak categories."""

    category: str
    team_total: float
    candidates: list[WaiverCandidate]


class WaiversResponse(BaseModel):
    """Waiver suggestions for my weakest categories."""

    window: Window
    as_of: date
    is_stale: bool
    cutoff: int
    availability_note: str
    suggestions: list[WaiverGroup]
