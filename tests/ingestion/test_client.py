"""Tests for the NBA client wrapper and its retry behavior."""

import inspect
import json

import pytest
import requests

from nbaproj.ingestion.client import (
    LIVE_RETRY,
    STATS_RETRY,
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


def test_client_pauses_after_each_successful_call():
    """Each successful request is followed by the configured pause, live and stats."""
    sleeps: list[float] = []
    policy = RetryPolicy(sleep=sleeps.append, pause=0.6)
    fetch = FlakyFetch(0, requests.ConnectionError("boom"), {"ok": True})

    live = LiveNbaClient(fetch_scoreboard=fetch, fetch_boxscore=fetch, retry=policy)
    live.get_scoreboard()
    live.get_boxscore("0022500001")
    StatsNbaClient(fetch_schedule=fetch, retry=policy).get_schedule("2025-26")

    assert sleeps == [0.6, 0.6, 0.6]


def test_the_pause_comes_after_a_retry_succeeds_not_instead_of_it():
    """A retried call waits its backoff first and pauses only once it has succeeded."""
    sleeps: list[float] = []
    fetch = FlakyFetch(1, requests.Timeout("slow"), {"ok": True})
    client = LiveNbaClient(
        fetch_scoreboard=fetch, retry=RetryPolicy(sleep=sleeps.append, pause=0.6)
    )

    client.get_scoreboard()

    assert sleeps == [1.0, 0.6]


def test_default_policy_has_no_pause_and_production_clients_do():
    """Tests never wait by default, but real clients are paced at 0.6 seconds."""
    assert RetryPolicy().pause == 0.0
    assert LIVE_RETRY.pause == 0.6
    assert STATS_RETRY.pause == 0.6
    assert inspect.signature(LiveNbaClient).parameters["retry"].default is LIVE_RETRY
    assert inspect.signature(StatsNbaClient).parameters["retry"].default is STATS_RETRY


def _live_call(fetch, policy):
    return LiveNbaClient(fetch_scoreboard=fetch, retry=policy).get_scoreboard()


def _stats_call(fetch, policy):
    return StatsNbaClient(fetch_schedule=fetch, retry=policy).get_schedule("2025-26")


NOT_JSON = json.JSONDecodeError("Expecting value", "<html>Access Denied</html>", 0)


@pytest.mark.parametrize("call", [_live_call, _stats_call], ids=["live", "stats"])
def test_client_retries_a_non_json_response_as_transient(call):
    """A block page or empty body (not JSON) is retried like any other transient failure."""
    sleeps: list[float] = []
    fetch = FlakyFetch(2, NOT_JSON, {"ok": True})

    assert call(fetch, RetryPolicy(sleep=sleeps.append)) == {"ok": True}
    assert fetch.calls == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize("call", [_live_call, _stats_call], ids=["live", "stats"])
def test_a_persistent_non_json_response_raises_nba_client_error(call):
    """After the last attempt a persistent non-JSON response is a clear NbaClientError."""
    fetch = FlakyFetch(99, NOT_JSON, {})

    with pytest.raises(NbaClientError, match="3 attempts") as caught:
        call(fetch, RetryPolicy(sleep=lambda _seconds: None))

    assert caught.value.__cause__ is NOT_JSON


MALFORMED_RESPONSE_ERRORS = [KeyError("players"), TypeError("bad"), AttributeError("bad")]


@pytest.mark.parametrize("error", MALFORMED_RESPONSE_ERRORS, ids=lambda e: type(e).__name__)
@pytest.mark.parametrize("call", [_live_call, _stats_call], ids=["live", "stats"])
def test_a_malformed_response_inside_the_endpoint_is_an_nba_client_error_without_retry(
    call, error
):
    """nba_api indexes into the response itself; an odd body is not worth retrying."""
    sleeps: list[float] = []
    fetch = FlakyFetch(99, error, {})

    with pytest.raises(NbaClientError, match="unexpected response") as caught:
        call(fetch, RetryPolicy(sleep=sleeps.append))

    assert fetch.calls == 1
    assert not sleeps
    assert caught.value.__cause__ is error


def test_an_unrelated_error_from_the_fetch_still_propagates():
    """Only the known shapes of a bad response are converted; anything else is a bug."""
    fetch = FlakyFetch(99, RuntimeError("bug"), {})

    with pytest.raises(RuntimeError, match="bug"):
        _live_call(fetch, RetryPolicy(sleep=lambda _seconds: None))

    assert fetch.calls == 1
