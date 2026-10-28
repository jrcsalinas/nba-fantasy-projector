"""Tests for the Refresh now button, the status partial, and the answers an HTMX click gets."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nbaproj.db.models import RefreshLog
from tests.web.helpers import csrf_token, log_in
from tests.web.refresh.conftest import HOUR

POLL = 'hx-trigger="every 3s"'
BUTTON = ">Refresh now</button>"


def _runs(engine) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(RefreshLog))


def _post_as_htmx(client):
    headers = {"HX-Request": "true", "X-CSRF-Token": csrf_token(client.get("/").text)}
    return client.post("/refresh", headers=headers)


@pytest.mark.parametrize(
    ("outcome", "message"),
    [
        ("started", "Refresh started."),
        ("recent", "Data was checked a moment ago, so no new refresh was started."),
        ("running", "A refresh is already running."),
    ],
)
def test_an_htmx_refresh_returns_the_banner_with_a_message_for_every_outcome(
    make_client, seed_log, seed_live_run, outcome, message
):
    """A click always gets a 200 with the banner; it polls unless the data was just checked."""
    if outcome == "recent":
        seed_log("succeeded", timedelta(minutes=5))
    if outcome == "running":
        seed_live_run(timedelta(minutes=1))

    response = _post_as_htmx(make_client())

    assert response.status_code == 200
    assert "<!doctype" not in response.text.lower()
    assert 'id="freshness"' in response.text and message in response.text
    assert (POLL in response.text) is (outcome != "recent")


def test_a_plain_form_refresh_redirects_to_the_home_page(make_client, engine):
    """Without JavaScript the button is an ordinary form post: it starts a run and goes home."""
    client = make_client()
    token = csrf_token(client.get("/").text)

    response = client.post("/refresh", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/"
    assert _runs(engine) == 1


def test_status_shows_running_with_a_polling_trigger(make_client, seed_live_run):
    """While a run is going the banner asks for itself again every three seconds."""
    seed_live_run(timedelta(minutes=1))

    response = make_client().get("/refresh/status")

    assert response.status_code == 200
    assert "<!doctype" not in response.text.lower()
    assert "Refreshing data..." in response.text
    assert 'hx-get="/refresh/status"' in response.text and POLL in response.text
    assert BUTTON not in response.text


def test_status_stops_polling_when_the_run_is_finished(make_client, seed_log):
    """A finished banner has no polling and offers the button again."""
    seed_log("succeeded", 2 * HOUR)

    response = make_client().get("/refresh/status")

    assert "Data checked" in response.text
    assert POLL not in response.text and "hx-get" not in response.text
    assert BUTTON in response.text


def test_status_works_when_the_app_has_no_database(client):
    """The status page reads through the same banner hook as every page: no database, no banner."""
    assert log_in(client).status_code == 303

    response = client.get("/refresh/status")

    assert response.status_code == 200 and "freshness" not in response.text


def test_status_needs_a_login(make_client):
    """Signed out, a browser goes to the login page and HTMX gets a 401."""
    client = make_client(signed_in=False)

    browser = client.get("/refresh/status", follow_redirects=False)
    htmx = client.get("/refresh/status", headers={"HX-Request": "true"}, follow_redirects=False)

    assert browser.status_code == 303 and browser.headers["location"] == "/login"
    assert htmx.status_code == 401


def test_the_refresh_now_button_is_wired_with_the_csrf_token(make_client):
    """Every signed-in page can start a refresh: an HTMX form and a plain form in one."""
    client = make_client()
    home = client.get("/").text
    token = csrf_token(home)

    assert 'action="/refresh"' in home and 'hx-post="/refresh"' in home
    assert 'hx-target="#freshness"' in home and 'hx-swap="outerHTML"' in home
    assert f'name="csrf_token" value="{token}"' in home
    assert BUTTON in home
