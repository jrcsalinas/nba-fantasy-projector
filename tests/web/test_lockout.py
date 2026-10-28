"""Tests for the login lockout."""

from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient

from nbaproj.web.app import create_app
from nbaproj.web.security import LoginLimiter
from nbaproj.web.settings import load_web_settings

from tests.web.conftest import TEST_PASSWORD
from tests.web.helpers import log_in

WRONG = "wrong password"
FIFTEEN_MINUTES = 15 * 60


class FakeClock:
    """A clock tests can move forward, so nothing ever sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by ``seconds``."""
        self.now += seconds


@pytest.fixture(name="clock")
def clock_fixture() -> FakeClock:
    """The limiter's clock."""
    return FakeClock()


@pytest.fixture(name="make_client")
def make_client_fixture(web_env, clock):
    """A factory for browsers (separate cookie jars) of one app, each with a client address."""
    app = create_app(load_web_settings(web_env), limiter=LoginLimiter(clock=clock))
    with ExitStack() as stack:

        def make(address: str = "10.0.0.1") -> TestClient:
            return stack.enter_context(TestClient(app, client=(address, 50000)))

        yield make


def _fail_times(client, times: int) -> None:
    for _ in range(times):
        assert log_in(client, WRONG).status_code == 401


def test_five_failures_lock_the_address_out_even_for_the_right_password(make_client):
    """After five wrong passwords even the right one is refused with 429."""
    client = make_client()
    _fail_times(client, 5)

    assert log_in(client, TEST_PASSWORD).status_code == 429


def test_lockout_ends_after_fifteen_minutes(make_client, clock):
    """The lockout lifts on time, and the right password then works."""
    client = make_client()
    _fail_times(client, 5)
    clock.advance(FIFTEEN_MINUTES - 1)
    assert log_in(client, TEST_PASSWORD).status_code == 429

    clock.advance(2)

    assert log_in(client, TEST_PASSWORD).status_code == 303


def test_success_resets_the_count(make_client):
    """Four failures, a success, and four more failures from the same address never lock it."""
    first_browser = make_client()
    _fail_times(first_browser, 4)
    assert log_in(first_browser, TEST_PASSWORD).status_code == 303

    second_browser = make_client()  # the same address, a fresh cookie jar

    _fail_times(second_browser, 4)


def test_failures_outside_the_window_do_not_count(make_client, clock):
    """Failures spread over more than fifteen minutes do not add up to a lockout."""
    client = make_client()
    _fail_times(client, 4)
    clock.advance(FIFTEEN_MINUTES + 1)

    _fail_times(client, 4)
    assert log_in(client, TEST_PASSWORD).status_code == 303


def test_another_address_is_unaffected(make_client):
    """One address being locked out does not lock out another."""
    attacker = make_client("203.0.113.9")
    _fail_times(attacker, 5)
    assert log_in(attacker, TEST_PASSWORD).status_code == 429

    owner = make_client("198.51.100.7")

    assert log_in(owner, TEST_PASSWORD).status_code == 303


def test_locked_out_response_has_retry_after_and_reveals_nothing(make_client):
    """During a lockout the answer is the same for any password and says how long to wait."""
    client = make_client()
    _fail_times(client, 5)

    right = log_in(client, TEST_PASSWORD)
    wrong = log_in(client, WRONG)

    assert right.status_code == wrong.status_code == 429
    assert right.headers["retry-after"] == wrong.headers["retry-after"]
    assert 0 < int(right.headers["retry-after"]) <= FIFTEEN_MINUTES
    assert "too many attempts" in right.text.lower()
    assert TEST_PASSWORD not in right.text and WRONG not in wrong.text


def test_limiter_memory_is_bounded():
    """Many different addresses cannot make the limiter grow without limit."""
    limiter = LoginLimiter(max_tracked=100)

    for number in range(500):
        limiter.record_failure(f"198.51.100.{number}")

    assert limiter.tracked_addresses() <= 100
