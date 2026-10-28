"""Pure parsers for the NBA live scoreboard and box score payloads."""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from nbaproj.db.models import Game, Player, Team
from nbaproj.ingestion.usage import Totals, usage_pct

logger = logging.getLogger(__name__)

_ISO_MINUTES = re.compile(r"^PT(?P<minutes>\d+)M(?P<seconds>\d+(?:\.\d+)?)S$")

# Postgres refuses values longer than their column (SQLite does not), so the parsers
# enforce the same limits and report an over-long value as a payload error up front.
GAME_ID_LENGTH = Game.game_id.type.length
TRICODE_LENGTH = Team.tricode.type.length
TEAM_NAME_LENGTH = Team.name.type.length
TEAM_CITY_LENGTH = Team.city.type.length
PLAYER_NAME_LENGTH = Player.full_name.type.length

_STATUSES = {1: "scheduled", 2: "live", 3: "final"}

# The feed sends "1" today; 1 and True are accepted too (True == 1) so a change in how
# the flag is typed does not silently drop every player.
PLAYED_VALUES = ("1", 1)

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
        raise PayloadError(f"Unknown game type prefix in game id {game_id!r}") from None


class PayloadError(ValueError):
    """An NBA payload is not shaped the way the parsers expect.

    Parsers raise this for every data problem, so callers can treat it as bad input for
    one record. Other exceptions (a KeyError from our own code, say) stay bugs.
    """


def parse_game_id(raw: object) -> str:
    """A game id: exactly ``GAME_ID_LENGTH`` ASCII digits, such as ``0022500001``."""
    if not (
        isinstance(raw, str)
        and len(raw) == GAME_ID_LENGTH
        and raw.isascii()
        and raw.isdigit()
    ):
        raise PayloadError(f"malformed game id: {raw!r}")
    return raw


def limited_text(value: object, limit: int, what: str) -> str:
    """Text that fits its database column, or a PayloadError saying what is wrong."""
    if not isinstance(value, str):
        raise PayloadError(f"{what} is not text: {value!r}")
    if len(value) > limit:
        raise PayloadError(f"{what} is too long ({len(value)} > {limit}): {value[:30]!r}")
    return value


@contextmanager
def malformed_as_payload_error(what: str) -> Iterator[None]:
    """Turn the errors a malformed payload causes while parsing into ``PayloadError``.

    A null, a missing key, or a wrongly typed field is a data problem for one record.
    """
    try:
        yield
    except PayloadError:
        raise
    except (TypeError, AttributeError, KeyError, ValueError) as error:
        raise PayloadError(f"malformed {what}: {type(error).__name__}: {error}") from error


def fantasy_game_type(game_id: str) -> str | None:
    """The game type if the game counts for fantasy, else None.

    Regular season, play-in and playoff games count. Preseason (001), All-Star (003),
    the Cup final (006) and any unknown prefix do not.
    """
    game_type = _GAME_TYPES.get(game_id[:3])
    return None if game_type in (None, "preseason") else game_type


def parse_minutes(raw: str) -> float:
    """Convert an ISO duration such as ``PT33M15.00S`` to decimal minutes."""
    match = _ISO_MINUTES.match(raw)
    if match is None:
        raise PayloadError(f"Not an ISO minutes duration: {raw!r}")
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


def parse_team(raw: dict) -> TeamInfo:
    """Read a team from a payload's teamId, teamTricode, teamName and teamCity."""
    return TeamInfo(
        team_id=int(raw["teamId"]),
        tricode=limited_text(raw["teamTricode"], TRICODE_LENGTH, "team tricode"),
        name=limited_text(raw["teamName"], TEAM_NAME_LENGTH, "team name"),
        city=limited_text(raw["teamCity"], TEAM_CITY_LENGTH, "team city"),
    )


def status_name(raw_game: dict) -> str:
    """Name a payload game's status: scheduled, live or final."""
    status_code = raw_game["gameStatus"]
    if status_code not in _STATUSES:
        raise PayloadError(f"Unknown game status {status_code!r} for {raw_game['gameId']}")
    return _STATUSES[status_code]


def parse_utc(raw: str) -> datetime:
    """Read an ISO timestamp ending in Z (or with an offset) as an aware datetime."""
    moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise PayloadError(f"timestamp {raw!r} has no timezone")
    return moment


def _parse_scoreboard_game(raw: dict) -> ScoreboardGame:
    with malformed_as_payload_error("scoreboard game"):
        return ScoreboardGame(
            game_id=parse_game_id(raw["gameId"]),
            status=status_name(raw),
            tip_off_utc=parse_utc(raw["gameTimeUTC"]),
            home=parse_team(raw["homeTeam"]),
            away=parse_team(raw["awayTeam"]),
        )


def _is_not_a_fantasy_game(raw: dict) -> bool:
    """True if the game has a valid id that is preseason, All-Star, Cup final, or unknown."""
    try:
        return fantasy_game_type(parse_game_id(raw.get("gameId"))) is None
    except PayloadError:
        return False


def parse_scoreboard(payload: dict) -> list[ScoreboardGame]:
    """Parse the live scoreboard payload into plain game records.

    A malformed game that would never be loaded (preseason, All-Star, Cup final) is
    logged and skipped, so it cannot hide the real games. A malformed game that would be
    loaded still raises.
    """
    games = []
    with malformed_as_payload_error("scoreboard"):
        raw_games = payload["scoreboard"]["games"]
    for raw in raw_games:
        try:
            games.append(_parse_scoreboard_game(raw))
        except PayloadError as error:
            if not _is_not_a_fantasy_game(raw):
                raise
            logger.warning("Skipping malformed non-fantasy game %s: %s", raw.get("gameId"), error)
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
        name=limited_text(raw["name"], PLAYER_NAME_LENGTH, "player name"),
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
        if raw["played"] in PLAYED_VALUES
    ]


def parse_boxscore(payload: dict) -> BoxScore:
    """Parse a live box score payload, keeping only players who played."""
    with malformed_as_payload_error("box score"):
        game = payload["game"]
        return BoxScore(
            game_id=parse_game_id(game["gameId"]),
            status=status_name(game),
            tip_off_utc=parse_utc(game["gameTimeUTC"]),
            home=parse_team(game["homeTeam"]),
            away=parse_team(game["awayTeam"]),
            lines=_parse_team_lines(game["homeTeam"]) + _parse_team_lines(game["awayTeam"]),
        )
