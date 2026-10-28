"""Tests for who may call POST /refresh: the cron's bearer token, or you with a CSRF token."""

import logging

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nbaproj.db.models import RefreshLog
from tests.web.helpers import csrf_token

TOKEN = "cron-token-" + "c" * 30


def _bearer(value: str = TOKEN, scheme: str = "Bearer") -> dict[str, str]:
    return {"Authorization": f"{scheme} {value}"}


def _runs(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(RefreshLog))


@pytest.mark.parametrize("scheme", ["Bearer", "bearer"])
def test_a_valid_bearer_token_may_refresh(make_client, scheme):
    """The cron needs no login and no CSRF token, only the token (scheme name any case)."""
    client = make_client(signed_in=False, REFRESH_TOKEN=TOKEN)

    response = client.post("/refresh", headers=_bearer(scheme=scheme))

    assert response.status_code == 202


def test_a_wrong_bearer_token_is_a_generic_401_and_starts_nothing(make_client, engine):
    """The answer says nothing about why, and never repeats either token."""
    client = make_client(signed_in=False, REFRESH_TOKEN=TOKEN)

    response = client.post("/refresh", headers=_bearer("wrong-" + "w" * 30))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in response.text and "wrong-" not in response.text
    assert _runs(engine) == 0


@pytest.mark.parametrize(
    "header",
    ["Bearer " + "x" * 40, "Bearer ", "Bearer", "Basic dXNlcjpwdw==", ""],
    ids=["a token", "empty token", "no token", "basic auth", "empty header"],
)
def test_a_bearer_token_is_refused_when_none_is_configured(make_client, engine, header):
    """With no REFRESH_TOKEN set nothing can match, not even an empty token."""
    client = make_client(signed_in=False)

    response = client.post("/refresh", headers={"Authorization": header})

    assert response.status_code == 401
    assert _runs(engine) == 0


def test_a_wrong_bearer_token_is_refused_even_with_a_valid_session(make_client, engine):
    """A header that is present and wrong is never ignored in favour of the cookie."""
    client = make_client(REFRESH_TOKEN=TOKEN)
    headers = {**_bearer("wrong-" + "w" * 30), "X-CSRF-Token": csrf_token(client.get("/").text)}

    response = client.post("/refresh", headers=headers)

    assert response.status_code == 401
    assert _runs(engine) == 0


def test_a_signed_in_session_with_a_csrf_token_may_refresh(make_client):
    """The button path: the login cookie plus the CSRF token, no bearer token needed."""
    client = make_client()

    response = client.post("/refresh", headers={"X-CSRF-Token": csrf_token(client.get("/").text)})

    assert response.status_code == 202


def test_a_signed_in_session_without_a_csrf_token_is_403(make_client, engine):
    """A cookie alone is not enough for an unsafe request."""
    client = make_client(REFRESH_TOKEN=TOKEN)

    response = client.post("/refresh", follow_redirects=False)

    assert response.status_code == 403
    assert _runs(engine) == 0


def test_a_signed_out_request_is_redirected_to_login_and_starts_nothing(make_client, engine):
    """No header and no session: a browser goes to the login page, HTMX gets a 401."""
    client = make_client(signed_in=False, REFRESH_TOKEN=TOKEN)

    browser = client.post("/refresh", follow_redirects=False)
    htmx = client.post("/refresh", headers={"HX-Request": "true"}, follow_redirects=False)

    assert browser.status_code == 303 and browser.headers["location"] == "/login"
    assert htmx.status_code == 401
    assert _runs(engine) == 0


def test_a_refused_bearer_token_is_logged_without_the_token(make_client, caplog):
    """A stranger guessing at /refresh leaves a trace: the address, never either token."""
    client = make_client(signed_in=False, REFRESH_TOKEN=TOKEN)
    wrong = "wrong-" + "w" * 30

    with caplog.at_level(logging.WARNING):
        client.post("/refresh", headers=_bearer(wrong))

    assert "Refresh refused: bad bearer token from testclient" in caplog.text
    assert TOKEN not in caplog.text and wrong not in caplog.text


def test_an_htmx_request_after_the_session_ends_is_sent_to_the_login_page(make_client):
    """HTMX swaps nothing on a 401, so a stale tab (or an expired session) says where to go."""
    client = make_client(signed_in=False)

    response = client.get("/refresh/status", headers={"HX-Request": "true"})

    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login"
