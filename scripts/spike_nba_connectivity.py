"""Throwaway spike: can we reach NBA data, and does it carry what the app needs?

Uses nba_api's normal clients and requests' default behavior. No header spoofing,
proxies, or other bot-protection workarounds. If a request is blocked, that is the
finding. Prints results to the terminal and writes nothing.
"""

import time

import requests
from nba_api.live.nba.endpoints import boxscore, scoreboard
from nba_api.stats.endpoints import leaguegamelog, scheduleleaguev2

TIMEOUT = 20
SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
HISTORICAL_GAMES = {"2024-25 opener": "0022400001", "2023-24 opener": "0022300001"}
NEEDED_PLAYER_STATS = [
    "points", "reboundsTotal", "assists", "steals", "blocks", "threePointersMade",
    "fieldGoalsMade", "fieldGoalsAttempted", "freeThrowsMade", "freeThrowsAttempted",
    "turnovers", "minutes",
]


def run(label, func):
    """Call func, print OK or FAIL with timing, and return its result or None."""
    start = time.time()
    try:
        result = func()
    except (requests.exceptions.RequestException, ValueError, KeyError) as exc:
        elapsed = time.time() - start
        print(f"FAIL  {label}  ({elapsed:.1f}s)  {type(exc).__name__}: {str(exc)[:120]}")
        return None
    print(f"OK    {label}  ({time.time() - start:.1f}s)")
    return result


def player_stat_keys(box):
    """Return the stat keys of the first player in a live box score dict."""
    players = box["game"]["homeTeam"]["players"]
    return set(players[0]["statistics"]) if players else set()


def check_schedule():
    """Fetch the static schedule with requests' default headers."""
    response = requests.get(SCHEDULE_URL, timeout=TIMEOUT)
    response.raise_for_status()
    dates = response.json()["leagueSchedule"]["gameDates"]
    return len(dates), dates[0]["gameDate"], dates[-1]["gameDate"]


def main():
    """Run each check and print a summary."""
    print("== live endpoints (cdn.nba.com) via nba_api")
    board = run("live scoreboard (today)",
                lambda: scoreboard.ScoreBoard(timeout=TIMEOUT).get_dict())
    if board:
        print(f"      games today: {len(board['scoreboard']['games'])}")

    for label, game_id in HISTORICAL_GAMES.items():
        box = run(f"live box score, {label} ({game_id})",
                  lambda gid=game_id: boxscore.BoxScore(gid, timeout=TIMEOUT).get_dict())
        if box:
            keys = player_stat_keys(box)
            missing = [k for k in NEEDED_PLAYER_STATS if k not in keys]
            print(f"      9-cat + minutes fields missing: {missing or 'none'}")
            usage_keys = sorted(k for k in keys if "usage" in k.lower())
            print(f"      usage-related keys present: {usage_keys or 'none'}")

    print("== static schedule (cdn.nba.com), plain requests")
    schedule = run("schedule JSON", check_schedule)
    if schedule:
        print(f"      game dates: {schedule[0]}, from {schedule[1]} to {schedule[2]}")

    print("== schedule via nba_api stats endpoint (stats.nba.com)")
    sched = run("league schedule, 2025-26",
                lambda: scheduleleaguev2.ScheduleLeagueV2(
                    season="2025-26", timeout=TIMEOUT).get_data_frames()[0])
    if sched is not None:
        print(f"      rows: {len(sched)}, columns: {list(sched.columns)[:14]}...")

    print("== stats endpoint (stats.nba.com) via nba_api, for the fallback path")
    logs = run("league player game log, 2024-25",
               lambda: leaguegamelog.LeagueGameLog(
                   season="2024-25", player_or_team_abbreviation="P", timeout=TIMEOUT
               ).get_data_frames()[0])
    if logs is not None:
        print(f"      rows: {len(logs)}, columns: {list(logs.columns)[:12]}...")


if __name__ == "__main__":
    main()
