"""Test doubles and payload helpers for ingestion tests."""

import json
from datetime import datetime, timezone

from sqlalchemy import update

from nbaproj.db.models import JobLock
from nbaproj.ingestion.backfill import build_backfill_lines, store_backfill
from nbaproj.ingestion.schedule import parse_schedule, store_schedule


class FakeClient:
    """An NbaClient that serves canned payloads and records the box score requests."""

    def __init__(self, scoreboard: dict | Exception, boxscores: dict):
        self.scoreboard = scoreboard
        self.boxscores = boxscores
        self.boxscore_requests: list[str] = []

    def get_scoreboard(self) -> dict:
        """Return the canned scoreboard, or raise it if it is an error."""
        if isinstance(self.scoreboard, Exception):
            raise self.scoreboard
        return self.scoreboard

    def get_boxscore(self, game_id: str) -> dict:
        """Return the canned box score for the game, or raise it if it is an error."""
        self.boxscore_requests.append(game_id)
        payload = self.boxscores[game_id]
        if isinstance(payload, Exception):
            raise payload
        return payload


def boxscore_for(payload: dict, game_id: str) -> dict:
    """A copy of a box score payload under a different game id."""
    copy = json.loads(json.dumps(payload))
    copy["game"]["gameId"] = game_id
    return copy


def only_games(scoreboard: dict, *game_ids: str) -> dict:
    """A copy of the scoreboard payload keeping only the given games."""
    copy = json.loads(json.dumps(scoreboard))
    copy["scoreboard"]["games"] = [
        g for g in copy["scoreboard"]["games"] if g["gameId"] in game_ids
    ]
    return copy


def scoreboard_with_ids(scoreboard: dict, *game_ids: str) -> dict:
    """A scoreboard of final games with the given ids, copied from the first game."""
    copy = json.loads(json.dumps(scoreboard))
    template = copy["scoreboard"]["games"][0]
    copy["scoreboard"]["games"] = [
        {**template, "gameId": game_id, "gameStatus": 3} for game_id in game_ids
    ]
    return copy


class FakeStatsClient:
    """A StatsClient that serves canned payloads, or raises the ones that are errors."""

    def __init__(self, schedule=None, player_log=None, team_log=None):
        self.schedule = schedule
        self.player_log = player_log
        self.team_log = team_log
        self.requests: list[tuple[str, str]] = []

    def _serve(self, name: str, season: str, payload):
        self.requests.append((name, season))
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get_schedule(self, season: str) -> dict:
        """Return the canned schedule."""
        return self._serve("schedule", season, self.schedule)

    def get_player_game_log(self, season: str) -> dict:
        """Return the canned player game log."""
        return self._serve("players", season, self.player_log)

    def get_team_game_log(self, season: str) -> dict:
        """Return the canned team game log."""
        return self._serve("teams", season, self.team_log)


def backfill_games(session, schedule: dict, player_log: dict, team_log: dict) -> None:
    """Store the schedule and the backfilled lines, as a season backfill would."""
    store_schedule(session, parse_schedule(schedule))
    store_backfill(session, build_backfill_lines(player_log, team_log))
    session.commit()


class LockStealingStatsClient(FakeStatsClient):
    """A stats client that lets another writer take the refresh lock during every fetch."""

    def __init__(self, session, schedule=None, player_log=None, team_log=None):
        super().__init__(schedule, player_log, team_log)
        self.session = session

    def _serve(self, name: str, season: str, payload):
        self.session.execute(
            update(JobLock).values(
                holder="thief", expires_at=datetime(2100, 1, 1, tzinfo=timezone.utc)
            )
        )
        self.session.commit()
        return super()._serve(name, season, payload)
