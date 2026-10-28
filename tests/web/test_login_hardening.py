"""Tests that the password check stays off the event loop and that security events are logged."""

import asyncio
import logging
import time
from collections import Counter

import httpx2
from fastapi.testclient import TestClient

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

    assert "Failed login from 'testclient'" in caplog.text
    assert WRONG_PASSWORD not in caplog.text and TEST_PASSWORD not in caplog.text


def test_a_login_refused_during_a_lockout_is_logged(client, caplog):
    """The in-memory counter nobody can read is not enough: the lockout leaves a log line."""
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            log_in(client, WRONG_PASSWORD)
        token = csrf_token(client.get("/login").text)
        refused = client.post("/login", data={"password": TEST_PASSWORD, "csrf_token": token})

    assert refused.status_code == 429
    assert "Login refused for locked-out address 'testclient'" in caplog.text
    assert TEST_PASSWORD not in caplog.text and WRONG_PASSWORD not in caplog.text


def test_many_wrong_guesses_at_once_are_still_limited_to_five(app, monkeypatch):
    """The lockout counts a guess before the slow check finishes, not after.

    Thirty guesses arrive together over one session; if each were counted only once its
    check had finished, all thirty would be checked before the first count landed.
    """

    def slow_wrong_password(_password_hash, _password):
        time.sleep(0.05)
        return False

    monkeypatch.setattr("nbaproj.web.auth.verify_password", slow_wrong_password)

    async def attack() -> Counter:
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            token = csrf_token((await client.get("/login")).text)
            form = {"password": WRONG_PASSWORD, "csrf_token": token}
            replies = await asyncio.gather(*[client.post("/login", data=form) for _ in range(30)])
        return Counter(reply.status_code for reply in replies)

    codes = asyncio.run(attack())

    assert codes[401] <= 5
    assert codes[401] + codes[429] == 30


def test_a_client_address_with_spaces_is_logged_quoted(app, caplog):
    """Behind a mis-set proxy the address is whatever the client claims, not trusted text."""
    forged = "1.2.3.4 Login succeeded for owner"

    with caplog.at_level(logging.WARNING), TestClient(app, client=(forged, 50000)) as client:
        log_in(client, WRONG_PASSWORD)

    assert f"Failed login from {forged!r}" in caplog.text
    assert f"from {forged} " not in caplog.text
