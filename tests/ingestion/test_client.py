"""Tests for the NBA client wrapper and its retry behavior."""

import pytest
import requests

from nbaproj.ingestion.client import (
    LiveNbaClient,
    NbaClientError,
    RetryPolicy,
    StatsNbaClient,
)


class FlakyFetch:
    """A fetch function that fails a set number of times before succeeding."""

    def __init__(self, failures: int, error: Exception, payload: dict):
        self.failures = failures
        self.error = error
        self.payload = payload
        self.calls = 0

    def __call__(self, *_args) -> dict:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return self.payload


def _client(fetch, sleeps: list[float]) -> LiveNbaClient:
    return LiveNbaClient(
        fetch_scoreboard=fetch,
        fetch_boxscore=fetch,
        retry=RetryPolicy(sleep=sleeps.append),
    )


def test_client_returns_payload_on_first_success():
    """A working call returns the payload with no waiting."""
    sleeps: list[float] = []
    fetch = FlakyFetch(0, requests.ConnectionError("boom"), {"ok": True})
    client = _client(fetch, sleeps)

    assert client.get_scoreboard() == {"ok": True}
    assert client.get_boxscore("0022500001") == {"ok": True}
    assert fetch.calls == 2
    assert not sleeps


def test_client_retries_transient_errors_with_backoff():
    """Transient network errors are retried, waiting 1s then 2s between attempts."""
    sleeps: list[float] = []
    fetch = FlakyFetch(2, requests.Timeout("slow"), {"ok": True})

    assert _client(fetch, sleeps).get_scoreboard() == {"ok": True}
    assert fetch.calls == 3
    assert sleeps == [1.0, 2.0]


def test_client_raises_nba_client_error_after_last_attempt():
    """After the last attempt a clear error is raised, chained to the cause."""
    sleeps: list[float] = []
    original = requests.ConnectionError("down")
    fetch = FlakyFetch(99, original, {})

    with pytest.raises(NbaClientError, match="3 attempts") as caught:
        _client(fetch, sleeps).get_boxscore("0022500001")

    assert caught.value.__cause__ is original
    assert fetch.calls == 3
    assert sleeps == [1.0, 2.0]


def test_client_does_not_retry_unexpected_errors():
    """A programming error is not a network error and propagates at once."""
    sleeps: list[float] = []
    fetch = FlakyFetch(99, KeyError("bug"), {})

    with pytest.raises(KeyError):
        _client(fetch, sleeps).get_scoreboard()

    assert fetch.calls == 1
    assert not sleeps


def test_client_with_zero_attempts_fails_clearly():
    """Misconfiguring zero attempts raises instead of returning nothing."""
    fetch = FlakyFetch(0, requests.ConnectionError("boom"), {"ok": True})
    client = LiveNbaClient(fetch_scoreboard=fetch, retry=RetryPolicy(attempts=0))

    with pytest.raises(NbaClientError, match="no attempts"):
        client.get_scoreboard()

    assert fetch.calls == 0


def test_stats_client_returns_schedule_and_game_logs():
    """Each stats call passes the season to its own fetch function."""
    seen = []

    def fetcher(name):
        def fetch(season):
            seen.append((name, season))
            return {"name": name}

        return fetch

    client = StatsNbaClient(
        fetch_schedule=fetcher("schedule"),
        fetch_player_log=fetcher("players"),
        fetch_team_log=fetcher("teams"),
    )

    assert client.get_schedule("2025-26") == {"name": "schedule"}
    assert client.get_player_game_log("2025-26") == {"name": "players"}
    assert client.get_team_game_log("2024-25") == {"name": "teams"}
    assert seen == [
        ("schedule", "2025-26"),
        ("players", "2025-26"),
        ("teams", "2024-25"),
    ]


def test_stats_client_retries_and_raises_like_the_live_client():
    """Transient errors are retried with backoff, then a clear error is raised."""
    sleeps: list[float] = []
    flaky = FlakyFetch(2, requests.Timeout("slow"), {"ok": True})
    client = StatsNbaClient(fetch_schedule=flaky, retry=RetryPolicy(sleep=sleeps.append))

    assert client.get_schedule("2025-26") == {"ok": True}
    assert sleeps == [1.0, 2.0]

    sleeps.clear()
    down = FlakyFetch(99, requests.ConnectionError("down"), {})
    client = StatsNbaClient(fetch_player_log=down, retry=RetryPolicy(sleep=sleeps.append))
    with pytest.raises(NbaClientError, match="3 attempts"):
        client.get_player_game_log("2025-26")
    assert down.calls == 3
    assert sleeps == [1.0, 2.0]
