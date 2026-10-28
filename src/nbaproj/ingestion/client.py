"""Client for the NBA live endpoints, with retry and backoff.

Requests go through nba_api's default behavior. No custom headers, proxies, or
other workarounds: a blocked request is reported as a failure.
"""

import logging
import time
from collections.abc import Callable
from typing import Protocol

import requests
from nba_api.live.nba.endpoints import boxscore, scoreboard

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 20
TRANSIENT_ERRORS = (requests.RequestException, TimeoutError, ConnectionError)


class NbaClientError(RuntimeError):
    """The NBA endpoint could not be reached after every attempt."""


class NbaClient(Protocol):
    """What the loader needs from an NBA data source."""

    def get_scoreboard(self) -> dict:
        """Today's live scoreboard payload."""

    def get_boxscore(self, game_id: str) -> dict:
        """The box score payload for one game."""


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
        sleep: Callable[[float], None] = time.sleep,
        attempts: int = 3,
        base_delay: float = 1.0,
    ):
        self._fetch_scoreboard = fetch_scoreboard
        self._fetch_boxscore = fetch_boxscore
        self._sleep = sleep
        self._attempts = attempts
        self._base_delay = base_delay

    def get_scoreboard(self) -> dict:
        """Today's live scoreboard payload."""
        return self._with_retry("scoreboard", self._fetch_scoreboard)

    def get_boxscore(self, game_id: str) -> dict:
        """The box score payload for one game."""
        return self._with_retry(
            f"boxscore {game_id}", lambda: self._fetch_boxscore(game_id)
        )

    def _with_retry(self, label: str, fetch: Callable[[], dict]) -> dict:
        for attempt in range(1, self._attempts + 1):
            try:
                return fetch()
            except TRANSIENT_ERRORS as error:
                if attempt == self._attempts:
                    raise NbaClientError(
                        f"{label}: failed after {self._attempts} attempts: {error}"
                    ) from error
                delay = self._base_delay * 2 ** (attempt - 1)
                logger.warning(
                    "%s: attempt %d failed (%s), retrying in %.1fs",
                    label,
                    attempt,
                    error,
                    delay,
                )
                self._sleep(delay)
        raise NbaClientError(f"{label}: no attempts were made")
