"""``nba_api`` implementation of ``DataProvider``.

Endpoints and fields used (verified against nba_api's installed version, fixtures recorded
2026-10-22):

- ``LeagueGameLog(player_or_team_abbreviation="P")``: ``resultSets[0]`` with columns
  ``PLAYER_ID, PLAYER_NAME, GAME_ID, GAME_DATE`` (ISO date), ``MIN, PTS, REB, AST, STL, BLK,
  FG3M, FGM, FGA, FTM, FTA, TOV``.
- ``ScheduleLeagueV2``: ``leagueSchedule.gameDates[].games[]`` with ``gameId``, ``gameStatus``
  (3 is final), ``gameDateEst`` (the US Eastern game date) and ``gameDateTimeUTC``.
  Only regular-season games (game id prefix ``002``) are returned.

``tests/fixtures/game_logs.json`` is trimmed real data plus one synthetic row (a second team
and game for the first player) to represent a traded player.
"""

import json
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from nba_api.stats.endpoints import leaguegamelog, scheduleleaguev2

from nba_projector.adapters.provider import ScheduledGame
from nba_projector.domain.models import GameLog
from nba_projector.domain.windows import season_for

logger = logging.getLogger(__name__)

_REGULAR_SEASON_PREFIX = "002"
_FINAL_STATUS = 3
_REQUIRED_COLUMNS = (
    "PLAYER_ID",
    "PLAYER_NAME",
    "GAME_ID",
    "GAME_DATE",
    "MIN",
    "PTS",
    "REB",
    "AST",
    "STL",
    "BLK",
    "FG3M",
    "FGM",
    "FGA",
    "FTM",
    "FTA",
    "TOV",
)
_INT_STATS = {
    "pts": "PTS",
    "reb": "REB",
    "ast": "AST",
    "stl": "STL",
    "blk": "BLK",
    "fg3m": "FG3M",
    "fgm": "FGM",
    "fga": "FGA",
    "ftm": "FTM",
    "fta": "FTA",
    "tov": "TOV",
}


class ProviderSchemaError(Exception):
    """The upstream response is missing structure this adapter depends on."""


def call_with_retry[T](
    fn: Callable[[], T],
    max_retries: int,
    base_delay: float,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn``, retrying failures with exponential backoff.

    Args:
        fn: Zero-argument callable to run.
        max_retries: Retries after the first attempt (total attempts is ``max_retries + 1``).
        base_delay: Seconds before the first retry; doubles for each further retry.
        sleep: Sleep function, injectable for tests.

    Returns:
        Whatever ``fn`` returns.

    Raises:
        Exception: The last error once retries are exhausted.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == max_retries:
                raise
            delay = base_delay * 2**attempt
            logger.warning(
                "Call failed (%s); retry %d/%d in %.1fs", exc, attempt + 1, max_retries, delay
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _result_set(payload: Mapping[str, Any]) -> tuple[list[str], list[list[Any]]]:
    try:
        result_set = payload["resultSets"][0]
        return list(result_set["headers"]), list(result_set["rowSet"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderSchemaError("Game log payload has no resultSets[0] headers/rowSet") from exc


def parse_game_logs(payload: Mapping[str, Any], season: str) -> list[GameLog]:
    """Convert a ``LeagueGameLog`` payload into ``GameLog`` rows.

    Rows with a missing or non-numeric required value are skipped and counted in a warning.

    Args:
        payload: Raw ``get_dict()`` response.
        season: Season label to stamp on each log.

    Returns:
        Parsed game logs.

    Raises:
        ProviderSchemaError: If an expected column is absent.
    """
    headers, rows = _result_set(payload)
    missing = [c for c in _REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise ProviderSchemaError(f"Game log payload is missing columns: {', '.join(missing)}")
    index = {name: headers.index(name) for name in _REQUIRED_COLUMNS}

    logs: list[GameLog] = []
    skipped = 0
    for row in rows:
        try:
            logs.append(
                GameLog(
                    season=season,
                    game_id=str(row[index["GAME_ID"]]),
                    game_date=date.fromisoformat(row[index["GAME_DATE"]]),
                    player_id=int(row[index["PLAYER_ID"]]),
                    player_name=str(row[index["PLAYER_NAME"]]),
                    minutes=float(row[index["MIN"]]),
                    **{field: int(row[index[col]]) for field, col in _INT_STATS.items()},
                )
            )
        except (TypeError, ValueError):
            skipped += 1
    if skipped:
        logger.warning("Skipped %d of %d game log rows with missing values", skipped, len(rows))
    return logs


def parse_schedule(payload: Mapping[str, Any], game_date: date) -> list[ScheduledGame]:
    """Convert a ``ScheduleLeagueV2`` payload into the regular-season games on one Eastern date.

    Args:
        payload: Raw ``get_dict()`` response.
        game_date: US Eastern game date to keep.

    Returns:
        Scheduled games with timezone-aware UTC start times.

    Raises:
        ProviderSchemaError: If the payload structure is not as expected.
    """
    try:
        game_dates = payload["leagueSchedule"]["gameDates"]
        games: list[ScheduledGame] = []
        for day in game_dates:
            for game in day["games"]:
                if not game["gameId"].startswith(_REGULAR_SEASON_PREFIX):
                    continue
                if date.fromisoformat(game["gameDateEst"][:10]) != game_date:
                    continue
                start = datetime.fromisoformat(game["gameDateTimeUTC"].replace("Z", "+00:00"))
                games.append(
                    ScheduledGame(
                        game_id=game["gameId"],
                        start_utc=start.astimezone(UTC),
                        is_final=game["gameStatus"] == _FINAL_STATUS,
                    )
                )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderSchemaError(f"Unexpected schedule payload: {exc!r}") from exc
    return games


class NbaApiProvider:
    """``DataProvider`` backed by the unofficial ``nba_api`` package."""

    def __init__(
        self,
        raw_dir: Path,
        timeout: float = 30.0,
        max_retries: int = 3,
        throttle_seconds: float = 0.6,
    ) -> None:
        """Configure network behaviour.

        Args:
            raw_dir: Directory where each raw response is written (overwritten per key).
            timeout: Seconds before a single request times out.
            max_retries: Retries per call after the first attempt.
            throttle_seconds: Pause after every call to respect upstream rate limits.
        """
        self._raw_dir = raw_dir
        self._timeout = timeout
        self._max_retries = max_retries
        self._throttle_seconds = throttle_seconds

    def fetch_game_logs(self, season: str) -> list[GameLog]:
        """Fetch every player game log for a season and keep the raw response."""
        payload = self._call(
            lambda: leaguegamelog.LeagueGameLog(
                player_or_team_abbreviation="P", season=season, timeout=self._timeout
            ).get_dict()
        )
        self._save_raw(f"game_logs-{season}.json", payload)
        logs = parse_game_logs(payload, season)
        logger.info("Fetched %d game logs for %s", len(logs), season)
        return logs

    def fetch_schedule(self, game_date: date) -> list[ScheduledGame]:
        """Fetch the season schedule and return games on the given Eastern date."""
        season = season_for(game_date)
        payload = self._call(
            lambda: scheduleleaguev2.ScheduleLeagueV2(
                season=season, timeout=self._timeout
            ).get_dict()
        )
        self._save_raw(f"schedule-{game_date.isoformat()}.json", payload)
        games = parse_schedule(payload, game_date)
        logger.info("Found %d scheduled games on %s", len(games), game_date)
        return games

    def _call(self, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return call_with_retry(fn, self._max_retries, base_delay=2.0)
        finally:
            time.sleep(self._throttle_seconds)

    def _save_raw(self, name: str, payload: Mapping[str, Any]) -> None:
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        (self._raw_dir / name).write_text(json.dumps(payload))
