"""Data source abstraction so ``nba_api`` can be swapped without touching domain code."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from nba_projector.domain.models import GameLog


@dataclass(frozen=True)
class ScheduledGame:
    """A scheduled regular-season game and whether it has finished."""

    game_id: str
    start_utc: datetime
    is_final: bool


class DataProvider(Protocol):
    """Source of game logs and schedules."""

    def fetch_game_logs(self, season: str) -> list[GameLog]:
        """Return every player game log for a season label such as ``"2026-27"``."""
        ...

    def fetch_schedule(self, game_date: date) -> list[ScheduledGame]:
        """Return games scheduled on a US Eastern game date."""
        ...
