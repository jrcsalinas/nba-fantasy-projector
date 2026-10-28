"""Tests for the freshness banner shown on every signed-in page."""

import pytest
from sqlalchemy.exc import OperationalError

from tests.web.helpers import csrf_token, log_in
from tests.web.refresh.conftest import HOUR

PAGES = ["/", "/roster", "/roster/history"]


@pytest.mark.parametrize("path", PAGES)
def test_signed_in_pages_show_the_freshness_banner(make_client, seed_log, path):
    """Home, the roster and its history all say when the data was last checked."""
    seed_log("succeeded", 3 * HOUR)
    client = make_client()

    html = client.get(path).text

    assert 'id="freshness"' in html
    assert "Data checked Oct 29, 2026 09:01 UTC." in html


def test_the_banner_names_each_state(make_client, seed_log):
    """Never checked, failed (with what was last good), and running each read differently."""
    client = make_client()
    assert "No data check yet." in client.get("/").text

    seed_log("succeeded", 6 * HOUR)
    seed_log("failed", 1 * HOUR, "could not check the scoreboard: timed out")
    failed = client.get("/").text
    assert "The last refresh failed at Oct 29, 2026 11:01 UTC" in failed
    assert "could not check the scoreboard: timed out" in failed
    assert "Showing data from Oct 29, 2026 06:01 UTC." in failed

    seed_log("running", HOUR // 30)
    assert "Refreshing data..." in client.get("/").text


def test_a_stale_banner_warns(make_client, seed_log):
    """After a day without a check the banner says the data may be out of date."""
    seed_log("skipped", 30 * HOUR)

    html = make_client().get("/").text

    assert "Data may be out of date: last checked Oct 28, 2026 06:01 UTC." in html


def test_the_login_page_has_no_banner(make_client, seed_log):
    """Signed out, there is nothing to show and no script to load."""
    seed_log("succeeded", 3 * HOUR)

    html = make_client(signed_in=False).get("/login").text

    assert 'id="freshness"' not in html and "htmx" not in html


def test_the_banner_shows_times_in_the_configured_timezone(make_client, seed_log):
    """Stored UTC times are shown in APP_TIMEZONE, with the zone named."""
    seed_log("succeeded", 3 * HOUR)

    html = make_client(APP_TIMEZONE="America/New_York").get("/").text

    assert "Data checked Oct 29, 2026 05:01 EDT." in html


def test_the_banner_escapes_the_run_message(make_client, seed_log):
    """A failure message is data from outside, so it is escaped like everything else."""
    seed_log("failed", 1 * HOUR, "<script>alert(1)</script>")

    html = make_client().get("/").text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html


@pytest.mark.parametrize("path", PAGES)
def test_htmx_and_the_csrf_header_are_on_every_signed_in_page(make_client, path):
    """Any page can post with HTMX: the script and the token header are in the base page."""
    client = make_client()
    token = csrf_token(client.get("/").text)

    html = client.get(path).text

    assert '<script src="/static/htmx.min.js" defer></script>' in html
    assert f'hx-headers=\'{{"X-CSRF-Token": "{token}"}}\'' in html


def test_a_page_with_no_engine_still_works_and_shows_no_banner(client):
    """The app built without a database (as in the 2a tests) shows no banner and no error."""
    assert log_in(client).status_code == 303

    response = client.get("/")

    assert response.status_code == 200 and 'id="freshness"' not in response.text


def test_a_database_failure_does_not_break_the_page(make_client, monkeypatch):
    """If reading the log fails the page still renders, just without the banner."""

    def broken(*_args, **_kwargs):
        raise OperationalError("SELECT", {}, Exception("database is down"))

    client = make_client()
    monkeypatch.setattr("nbaproj.web.app.load_freshness", broken)

    response = client.get("/")

    assert response.status_code == 200 and 'id="freshness"' not in response.text
