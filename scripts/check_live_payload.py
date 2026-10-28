"""Manual contract check: does the live NBA payload still carry what the parser reads?

Fetches one live scoreboard and one box score through the normal client (nba_api's
default behavior, with retries), then lists any field the parser needs that is
missing. Writes nothing. Exits 1 if a field is missing or a payload cannot be read.

Run from the project root:  .venv/Scripts/python.exe scripts/check_live_payload.py
"""

import sys

from nbaproj.ingestion.client import LiveNbaClient, NbaClientError
from nbaproj.ingestion.parsing import parse_boxscore, parse_scoreboard

TEAM_FIELDS = ["teamId", "teamName", "teamCity", "teamTricode"]
GAME_FIELDS = ["gameId", "gameStatus", "gameTimeUTC"]
PLAYER_FIELDS = ["personId", "name", "played", "statistics"]
PLAYER_STATS = [
    "minutes", "points", "reboundsTotal", "assists", "steals", "blocks",
    "threePointersMade", "fieldGoalsMade", "fieldGoalsAttempted",
    "freeThrowsMade", "freeThrowsAttempted", "turnovers",
]
TEAM_STATS = ["minutes", "fieldGoalsAttempted", "freeThrowsAttempted", "turnovers"]
STATUS_PREFERENCE = (3, 2)  # final, then live; a scheduled game has no box score yet


def _missing(container: dict, fields: list[str], where: str) -> list[str]:
    return [f"{where}.{field}" for field in fields if field not in container]


def check_scoreboard_game(game: dict) -> list[str]:
    """Missing fields in one scoreboard game."""
    problems = _missing(game, GAME_FIELDS, "game")
    for side in ("homeTeam", "awayTeam"):
        problems += _missing(game.get(side, {}), TEAM_FIELDS, f"game.{side}")
    return problems


def check_boxscore_game(game: dict) -> list[str]:
    """Missing fields in a box score game."""
    problems = _missing(game, GAME_FIELDS, "game")
    for side in ("homeTeam", "awayTeam"):
        team = game.get(side, {})
        where = f"game.{side}"
        problems += _missing(team, TEAM_FIELDS + ["players", "statistics"], where)
        problems += _missing(team.get("statistics", {}), TEAM_STATS, f"{where}.statistics")
        played = [p for p in team.get("players", []) if p.get("played") == "1"]
        for player in played[:1]:
            problems += _missing(player, PLAYER_FIELDS, f"{where}.players[]")
            problems += _missing(
                player.get("statistics", {}), PLAYER_STATS, f"{where}.players[].statistics"
            )
    return problems


def pick_game_id(games: list[dict]) -> str | None:
    """The id of a final game if there is one, else a live one, else None."""
    for status in STATUS_PREFERENCE:
        for game in games:
            if game.get("gameStatus") == status:
                return game["gameId"]
    return None


def main() -> int:
    """Run the check and return the process exit code."""
    client = LiveNbaClient()
    try:
        scoreboard = client.get_scoreboard()
    except NbaClientError as error:
        print(f"FAIL scoreboard: {error}")
        return 1

    games = scoreboard.get("scoreboard", {}).get("games", [])
    problems = [p for game in games for p in check_scoreboard_game(game)]
    print(f"scoreboard: {len(games)} games, {len(problems)} missing fields")
    if games:
        parse_scoreboard(scoreboard)
        print("scoreboard: parser accepted the payload")

    game_id = pick_game_id(games)
    if game_id is None:
        print("boxscore: no final or live game today, so no box score was checked")
    else:
        try:
            box = client.get_boxscore(game_id)
        except NbaClientError as error:
            print(f"FAIL boxscore {game_id}: {error}")
            return 1
        box_problems = check_boxscore_game(box.get("game", {}))
        print(f"boxscore {game_id}: {len(box_problems)} missing fields")
        problems += box_problems
        if not box_problems:
            parse_boxscore(box)
            print(f"boxscore {game_id}: parser accepted the payload")

    for problem in problems:
        print(f"  MISSING {problem}")
    print("RESULT:", "OK" if not problems else "FIELDS MISSING")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
