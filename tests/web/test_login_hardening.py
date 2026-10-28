"""Tests that the password check stays off the event loop and that security events are logged."""

import asyncio
import logging

from tests.web.conftest import TEST_PASSWORD
from tests.web.helpers import csrf_token, log_in

WRONG_PASSWORD = "not the password, honest"


def test_the_password_check_does_not_run_on_the_event_loop(client, monkeypatch):
    """Argon2 is slow and memory-hungry: on the loop it would stall every other request."""
    seen = []

    def spy(_password_hash, password):
        try:
            asyncio.get_running_loop()
            seen.append("on the event loop")
        except RuntimeError:
            seen.append("in a worker thread")
        return password == TEST_PASSWORD

    monkeypatch.setattr("nbaproj.web.auth.verify_password", spy)

    assert log_in(client).status_code == 303
    assert seen == ["in a worker thread"]


def test_a_failed_login_is_logged_without_the_password(client, caplog):
    """The log says who failed (the address), never what they typed."""
    with caplog.at_level(logging.WARNING):
        log_in(client, WRONG_PASSWORD)

    assert "Failed login from testclient" in caplog.text
    assert WRONG_PASSWORD not in caplog.text and TEST_PASSWORD not in caplog.text


def test_a_login_refused_during_a_lockout_is_logged(client, caplog):
    """The in-memory counter nobody can read is not enough: the lockout leaves a log line."""
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            log_in(client, WRONG_PASSWORD)
        token = csrf_token(client.get("/login").text)
        refused = client.post("/login", data={"password": TEST_PASSWORD, "csrf_token": token})

    assert refused.status_code == 429
    assert "Login refused for locked-out address testclient" in caplog.text
    assert TEST_PASSWORD not in caplog.text and WRONG_PASSWORD not in caplog.text
