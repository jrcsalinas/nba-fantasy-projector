"""Fixtures for the service tests: a database seeded with games and box score lines."""

from datetime import datetime, timedelta, timezone

import pytest

from nbaproj.db.models import Game, GameLog, Player, Team
from nbaproj.roster.directory import PlayerDirectory
from nbaproj.roster.service import RosterService
from tests.scoring.conftest import ZERO_LINE

NAMES = {1: "Ada One", 2: "Ben Two", 3: "Cy Three", 4: "Di Four"}
DIRECTORY = PlayerDirectory(list(NAMES.items()))
JAN_1_NOON_UTC = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class Seeder:
    """Adds games and box score lines to a session."""

    def __init__(self, session):
        self.session = session
        self.session.add_all(
            [Team(team_id=n, tricode=f"T{n}", name=f"Team {n}", city=f"City {n}") for n in (1, 2)]
        )
        self.session.add_all([Player(player_id=n, full_name=name) for n, name in NAMES.items()])
        self.session.commit()
        self.games = 0

    def game(self, lines: dict[int, dict], tip_off: datetime, season: str = "2025-26") -> None:
        """One finished game: ``lines`` maps a player id to the stats that matter (rest zero)."""
        self.games += 1
        game_id = f"002{season[2:4]}{self.games:05d}"
        self.session.add(
            Game(
                game_id=game_id,
                season=season,
                game_type="regular",
                tip_off_utc=tip_off,
                home_team_id=1,
                away_team_id=2,
                status="final",
            )
        )
        self.session.flush()
        for player_id, stats in lines.items():
            row = {**ZERO_LINE, "minutes": 30.0, "usage_pct": None, **stats}
            self.session.add(
                GameLog(game_id=game_id, player_id=player_id, team_id=1, source="live", **row)
            )
        self.session.commit()

    def daily(self, days: range, lines_for_day, season: str = "2025-26") -> None:
        """A game on each of ``days`` (January 2026) at noon UTC, with ``lines_for_day(day)``."""
        for day in days:
            tip_off = JAN_1_NOON_UTC + timedelta(days=day - 1)
            self.game(lines_for_day(day), tip_off, season)


@pytest.fixture(name="seed")
def seed_fixture(session) -> Seeder:
    """A seeder on the shared in-memory database."""
    return Seeder(session)


@pytest.fixture(name="roster_of")
def roster_of_fixture(session):
    """``roster_of("josh", 1, 2)``: a roster service for that owner holding those players."""

    def make(owner: str, *player_ids: int) -> RosterService:
        service = RosterService(session, owner, DIRECTORY)
        for player_id in player_ids:
            service.add(player_id)
        session.commit()
        return service

    return make
