"""Pure parsers for the NBA live scoreboard and box score payloads."""

import re
from dataclasses import dataclass
from datetime import datetime

from nbaproj.ingestion.usage import Totals, usage_pct

_ISO_MINUTES = re.compile(r"^PT(?P<minutes>\d+)M(?P<seconds>\d+(?:\.\d+)?)S$")

_STATUSES = {1: "scheduled", 2: "live", 3: "final"}

_GAME_TYPES = {
    "001": "preseason",
    "002": "regular_season",
    "004": "playoffs",
    "005": "play_in",
}


def game_type_from_id(game_id: str) -> str:
    """Read the game type from the three-digit prefix of an NBA game id."""
    try:
        return _GAME_TYPES[game_id[:3]]
    except KeyError:
        raise ValueError(f"Unknown game type prefix in game id {game_id!r}") from None


def parse_minutes(raw: str) -> float:
    """Convert an ISO duration such as ``PT33M15.00S`` to decimal minutes."""
    match = _ISO_MINUTES.match(raw)
    if match is None:
        raise ValueError(f"Not an ISO minutes duration: {raw!r}")
    return int(match["minutes"]) + float(match["seconds"]) / 60


@dataclass(frozen=True)
class TeamInfo:
    """A team as it appears in a game."""

    team_id: int
    tricode: str
    name: str
    city: str


@dataclass(frozen=True)
class ScoreboardGame:
    """One game from the live scoreboard."""

    game_id: str
    status: str
    tip_off_utc: datetime
    home: TeamInfo
    away: TeamInfo


def _parse_team(raw: dict) -> TeamInfo:
    return TeamInfo(
        team_id=int(raw["teamId"]),
        tricode=raw["teamTricode"],
        name=raw["teamName"],
        city=raw["teamCity"],
    )


def _status_name(raw_game: dict) -> str:
    status_code = raw_game["gameStatus"]
    if status_code not in _STATUSES:
        raise ValueError(f"Unknown game status {status_code!r} for {raw_game['gameId']}")
    return _STATUSES[status_code]


def _parse_utc(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def parse_scoreboard(payload: dict) -> list[ScoreboardGame]:
    """Parse the live scoreboard payload into plain game records."""
    games = []
    for raw in payload["scoreboard"]["games"]:
        games.append(
            ScoreboardGame(
                game_id=raw["gameId"],
                status=_status_name(raw),
                tip_off_utc=_parse_utc(raw["gameTimeUTC"]),
                home=_parse_team(raw["homeTeam"]),
                away=_parse_team(raw["awayTeam"]),
            )
        )
    return games


@dataclass(frozen=True)
class PlayerLine:
    """One player's box score line for a game they played in."""

    player_id: int
    name: str
    team_id: int
    minutes: float
    pts: int
    reb: int
    ast: int
    stl: int
    blk: int
    tpm: int
    fgm: int
    fga: int
    ftm: int
    fta: int
    tov: int
    usage_pct: float | None


@dataclass(frozen=True)
class BoxScore:
    """A parsed box score: the game, both teams, and the lines of players who played."""

    game_id: str
    status: str
    tip_off_utc: datetime
    home: TeamInfo
    away: TeamInfo
    lines: list[PlayerLine]


def _parse_team_totals(stats: dict) -> Totals:
    return Totals(
        minutes=parse_minutes(stats["minutes"]),
        fga=int(stats["fieldGoalsAttempted"]),
        fta=int(stats["freeThrowsAttempted"]),
        tov=int(stats["turnovers"]),
    )


def _parse_player(raw: dict, team_id: int, team: Totals) -> PlayerLine:
    stats = raw["statistics"]
    minutes = parse_minutes(stats["minutes"])
    fga = int(stats["fieldGoalsAttempted"])
    fta = int(stats["freeThrowsAttempted"])
    tov = int(stats["turnovers"])
    return PlayerLine(
        player_id=int(raw["personId"]),
        name=raw["name"],
        team_id=team_id,
        minutes=minutes,
        pts=int(stats["points"]),
        reb=int(stats["reboundsTotal"]),
        ast=int(stats["assists"]),
        stl=int(stats["steals"]),
        blk=int(stats["blocks"]),
        tpm=int(stats["threePointersMade"]),
        fgm=int(stats["fieldGoalsMade"]),
        fga=fga,
        ftm=int(stats["freeThrowsMade"]),
        fta=fta,
        tov=tov,
        usage_pct=usage_pct(Totals(minutes, fga, fta, tov), team),
    )


def _parse_team_lines(raw_team: dict) -> list[PlayerLine]:
    team_id = int(raw_team["teamId"])
    team = _parse_team_totals(raw_team["statistics"])
    return [
        _parse_player(raw, team_id, team)
        for raw in raw_team["players"]
        if raw["played"] == "1"
    ]


def parse_boxscore(payload: dict) -> BoxScore:
    """Parse a live box score payload, keeping only players who played."""
    game = payload["game"]
    return BoxScore(
        game_id=game["gameId"],
        status=_status_name(game),
        tip_off_utc=_parse_utc(game["gameTimeUTC"]),
        home=_parse_team(game["homeTeam"]),
        away=_parse_team(game["awayTeam"]),
        lines=_parse_team_lines(game["homeTeam"]) + _parse_team_lines(game["awayTeam"]),
    )
