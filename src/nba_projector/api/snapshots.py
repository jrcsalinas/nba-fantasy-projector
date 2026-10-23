"""Loading the latest saved snapshot for a window."""

from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException

from nba_projector.adapters.repository import Repository
from nba_projector.domain.models import RankedPlayer, Window


@dataclass(frozen=True)
class LatestSnapshot:
    """The newest snapshot for a window and the one before it, if any."""

    as_of: date
    players: list[RankedPlayer]
    previous: list[RankedPlayer] | None


def load_latest(repo: Repository, window: Window) -> LatestSnapshot:
    """Load a window's newest snapshot and its predecessor.

    A window nobody qualifies for stores no rows, so it falls back to the newest snapshot date
    of any window with no players: data exists, this window is just empty.

    Raises:
        HTTPException: 404 if no snapshot of any window exists yet.
    """
    dates = repo.snapshot_dates(window, limit=2)
    if dates:
        previous = repo.load_snapshot(dates[1], window) if len(dates) > 1 else None
        return LatestSnapshot(dates[0], repo.load_snapshot(dates[0], window), previous)

    newest = max((d for w in Window for d in repo.snapshot_dates(w, limit=1)), default=None)
    if newest is None:
        raise HTTPException(status_code=404, detail="No rankings available yet")
    return LatestSnapshot(newest, [], None)
