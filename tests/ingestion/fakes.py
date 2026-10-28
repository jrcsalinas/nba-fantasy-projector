"""Test doubles and payload helpers for ingestion tests."""

import json


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
