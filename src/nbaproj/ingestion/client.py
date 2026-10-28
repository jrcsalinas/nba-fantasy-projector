"""Client for the NBA live endpoints, with retry and backoff.

Requests go through nba_api's default behavior. No custom headers, proxies, or
other workarounds: a blocked request is reported as a failure.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import requests
from nba_api.live.nba.endpoints import boxscore, scoreboard
from nba_api.stats.endpoints import leaguegamelog, scheduleleaguev2

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
STATS_TIMEOUT_SECONDS = 60
# A block page, rate-limit page, or empty body is not JSON: nba_api's ``get_dict()`` is a
# plain ``json.loads``, so it surfaces as a JSONDecodeError. That is the failure the NBA
# hosts produce most often, so it is retried like a network error.
TRANSIENT_ERRORS = (
    requests.RequestException,
    TimeoutError,
    ConnectionError,
    json.JSONDecodeError,
)


# The endpoint classes in nba_api index into the response themselves, so a response that is
# valid JSON but not the expected shape (no "players", an error object) raises one of these
# from inside the call. That is the library choking on the NBA's data, not a bug of ours,
# and retrying the same body is pointless, so it is reported once as a client error.
MALFORMED_RESPONSE_ERRORS = (KeyError, TypeError, AttributeError)


class NbaClientError(RuntimeError):
    """The NBA endpoint could not be reached after every attempt."""


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to try a call, how to wait between tries, and how to pace calls.

    ``pause`` is a wait after each successful call, so a loop over many games does not
    hit the NBA hosts back to back. It is 0 here so tests never wait; the production
    clients use ``LIVE_RETRY`` and ``STATS_RETRY``.
    """

    attempts: int = 3
    base_delay: float = 1.0
    sleep: Callable[[float], None] = time.sleep
    pause: float = 0.0


PAUSE_SECONDS = 0.6
LIVE_RETRY = RetryPolicy(pause=PAUSE_SECONDS)
STATS_RETRY = RetryPolicy(pause=PAUSE_SECONDS)


def call_with_retry(label: str, fetch: Callable[[], dict], policy: RetryPolicy) -> dict:
    """Call ``fetch``, retrying transient network errors with exponential backoff."""
    for attempt in range(1, policy.attempts + 1):
        try:
            payload = fetch()
        except TRANSIENT_ERRORS as error:
            if attempt == policy.attempts:
                raise NbaClientError(
                    f"{label}: failed after {policy.attempts} attempts: {error}"
                ) from error
            delay = policy.base_delay * 2 ** (attempt - 1)
            logger.warning(
                "%s: attempt %d failed (%s), retrying in %.1fs", label, attempt, error, delay
            )
            policy.sleep(delay)
            continue
        except MALFORMED_RESPONSE_ERRORS as error:
            raise NbaClientError(
                f"{label}: unexpected response shape from the NBA endpoint: "
                f"{type(error).__name__}: {error}"
            ) from error
        if policy.pause:
            policy.sleep(policy.pause)
        return payload
    raise NbaClientError(f"{label}: no attempts were made")


class NbaClient(Protocol):
    """What the loader needs from an NBA data source."""

    def get_scoreboard(self) -> dict:
        """Today's live scoreboard payload."""

    def get_boxscore(self, game_id: str) -> dict:
        """The box score payload for one game."""


class StatsClient(Protocol):
    """What schedule and backfill need from the NBA stats host."""

    def get_schedule(self, season: str) -> dict:
        """The league schedule payload for a season such as ``2025-26``."""

    def get_player_game_log(self, season: str) -> dict:
        """Every player's game lines for a regular season, in resultSets format."""

    def get_team_game_log(self, season: str) -> dict:
        """Every team's game totals for a regular season, in resultSets format."""


def _fetch_live_scoreboard() -> dict:
    return scoreboard.ScoreBoard(timeout=REQUEST_TIMEOUT_SECONDS).get_dict()


def _fetch_live_boxscore(game_id: str) -> dict:
    return boxscore.BoxScore(game_id, timeout=REQUEST_TIMEOUT_SECONDS).get_dict()


class LiveNbaClient:
    """Fetches live payloads, retrying transient network errors with backoff."""

    def __init__(
        self,
        fetch_scoreboard: Callable[[], dict] = _fetch_live_scoreboard,
        fetch_boxscore: Callable[[str], dict] = _fetch_live_boxscore,
        retry: RetryPolicy = LIVE_RETRY,
    ):
        self._fetch_scoreboard = fetch_scoreboard
        self._fetch_boxscore = fetch_boxscore
        self._retry = retry

    def get_scoreboard(self) -> dict:
        """Today's live scoreboard payload."""
        return call_with_retry("scoreboard", self._fetch_scoreboard, self._retry)

    def get_boxscore(self, game_id: str) -> dict:
        """The box score payload for one game."""
        return call_with_retry(
            f"boxscore {game_id}", lambda: self._fetch_boxscore(game_id), self._retry
        )


def _fetch_schedule(season: str) -> dict:
    return scheduleleaguev2.ScheduleLeagueV2(
        season=season, timeout=STATS_TIMEOUT_SECONDS
    ).get_dict()


def _fetch_game_log(season: str, scope: str) -> dict:
    return leaguegamelog.LeagueGameLog(
        season=season, player_or_team_abbreviation=scope, timeout=STATS_TIMEOUT_SECONDS
    ).get_dict()


def _fetch_player_log(season: str) -> dict:
    return _fetch_game_log(season, "P")


def _fetch_team_log(season: str) -> dict:
    return _fetch_game_log(season, "T")


class StatsNbaClient:
    """Fetches the schedule and game logs from the stats host, with retry and backoff."""

    def __init__(
        self,
        fetch_schedule: Callable[[str], dict] = _fetch_schedule,
        fetch_player_log: Callable[[str], dict] = _fetch_player_log,
        fetch_team_log: Callable[[str], dict] = _fetch_team_log,
        retry: RetryPolicy = STATS_RETRY,
    ):
        self._fetch_schedule = fetch_schedule
        self._fetch_player_log = fetch_player_log
        self._fetch_team_log = fetch_team_log
        self._retry = retry

    def get_schedule(self, season: str) -> dict:
        """The league schedule payload for a season such as ``2025-26``."""
        return call_with_retry(
            f"schedule {season}", lambda: self._fetch_schedule(season), self._retry
        )

    def get_player_game_log(self, season: str) -> dict:
        """Every player's game lines for a regular season, in resultSets format."""
        return call_with_retry(
            f"player game log {season}", lambda: self._fetch_player_log(season), self._retry
        )

    def get_team_game_log(self, season: str) -> dict:
        """Every team's game totals for a regular season, in resultSets format."""
        return call_with_retry(
            f"team game log {season}", lambda: self._fetch_team_log(season), self._retry
        )
