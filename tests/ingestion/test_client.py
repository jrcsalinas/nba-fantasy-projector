"""Tests for the NBA client wrapper and its retry behavior."""

import pytest
import requests

from nbaproj.ingestion.client import LiveNbaClient, NbaClientError


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
        sleep=sleeps.append,
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
    client = LiveNbaClient(fetch_scoreboard=fetch, attempts=0)

    with pytest.raises(NbaClientError, match="no attempts"):
        client.get_scoreboard()

    assert fetch.calls == 0
