"""Manual contract check: do the stats-host payloads still carry what the parsers read?

Fetches one season's schedule and player and team game logs through the normal stats
client (nba_api's default behavior, with retries), then lists any field the parsers need
that is missing. Writes nothing. Exits 1 if a field is missing or a payload cannot be read.

Run from the project root (about one minute; the player log is ~26,000 rows):
    PYTHONPATH=src .venv/Scripts/python.exe scripts/check_stats_payload.py [SEASON]
"""

import sys
from datetime import date

from nbaproj.ingestion.backfill import build_backfill_lines, parse_result_set_rows
from nbaproj.ingestion.client import NbaClientError, StatsNbaClient
from nbaproj.ingestion.contract import missing_fields, report
from nbaproj.ingestion.schedule import parse_schedule, previous_season, season_for_date

GAME_FIELDS = ["gameId", "gameStatus", "gameDateTimeUTC"]
TEAM_FIELDS = ["teamId", "teamName", "teamCity", "teamTricode"]
PLAYER_LOG_FIELDS = [
    "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "GAME_ID", "MIN", "FGM", "FGA", "FG3M",
    "FTM", "FTA", "REB", "AST", "STL", "BLK", "TOV", "PTS",
]
TEAM_LOG_FIELDS = ["TEAM_ID", "GAME_ID", "MIN", "FGA", "FTA", "TOV"]


def check_schedule(payload: dict) -> list[str]:
    """Missing fields in the schedule payload (game fields, and both teams of each game)."""
    games = [g for day in payload["leagueSchedule"]["gameDates"] for g in day["games"]]
    problems: list[str] = []
    for game in games:
        problems += missing_fields(game, GAME_FIELDS, "game")
        for side in ("homeTeam", "awayTeam"):
            problems += missing_fields(game.get(side, {}), TEAM_FIELDS, f"game.{side}")
    return sorted(set(problems))


def check_game_log(payload: dict, fields: list[str], name: str) -> list[str]:
    """Missing headers in a game log payload's first result set."""
    headers = {header: None for header in payload["resultSets"][0]["headers"]}
    return missing_fields(headers, fields, name)


def main(argv: list[str]) -> int:
    """Run the check and return the process exit code."""
    season = argv[0] if argv else previous_season(season_for_date(date.today()))
    client = StatsNbaClient()
    try:
        schedule = client.get_schedule(season)
        players = client.get_player_game_log(season)
        teams = client.get_team_game_log(season)
    except NbaClientError as error:
        print(f"FAIL {error}")
        return 1

    problems = check_schedule(schedule)
    problems += check_game_log(players, PLAYER_LOG_FIELDS, "player_log")
    problems += check_game_log(teams, TEAM_LOG_FIELDS, "team_log")
    print(f"season {season}: {len(problems)} missing fields")
    if not problems:
        games = parse_schedule(schedule)
        by_game = build_backfill_lines(players, teams)
        lines = sum(len(v) for v in by_game.values())
        print(f"schedule: parser kept {len(games)} games")
        rows = len(parse_result_set_rows(players))
        print(f"player log: {rows} rows, {lines} lines in {len(by_game)} games")
        no_usage = sum(1 for v in by_game.values() for line in v if line.usage_pct is None)
        print(f"lines without usage (zero minutes or no team row): {no_usage}")
    return report(problems)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
