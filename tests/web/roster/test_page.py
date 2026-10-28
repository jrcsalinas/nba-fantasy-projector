"""Tests for the roster page."""

import json
import re
from datetime import timedelta

from tests.web.helpers import csrf_token
from tests.web.roster.conftest import T0


def _position(html: str, text: str) -> int:
    assert text in html, f"{text!r} is not on the page"
    return html.index(text)


def test_roster_page_lists_players_sorted_by_name_with_remove_buttons(roster_client, seed):
    """Players read alphabetically (accents ignored), each with a Remove form."""
    seed(("add", 3, T0), ("add", 2, T0), ("add", 1, T0))

    html = roster_client.get("/roster").text

    order = [_position(html, name) for name in ("Jayson Tatum", "Luka Dončić", "Nikola Jokić")]
    assert order == sorted(order)
    assert html.count(">Remove</button>") == 3
    assert html.count('action="/roster/remove"') == 3
    assert re.findall(r'name="player_id" value="(\d+)"', html) == ["2", "1", "3"]


def test_an_empty_roster_says_so(roster_client):
    """With no players the page invites you to search instead of showing an empty list."""
    html = roster_client.get("/roster").text

    assert "No players yet" in html
    assert ">Remove</button>" not in html


def test_roster_page_shows_recent_changes_newest_first_and_a_history_link(roster_client, seed):
    """The last changes read newest first, in words, with a link to the full list."""
    seed(
        ("add", 1, T0),
        ("add", 2, T0 + timedelta(minutes=1)),
        ("remove", 1, T0 + timedelta(minutes=2)),
    )

    html = roster_client.get("/roster").text

    order = [
        _position(html, "Removed Luka Dončić"),
        _position(html, "Added Jayson Tatum"),
        _position(html, "Added Luka Dončić"),
    ]
    assert order == sorted(order)
    assert 'href="/roster/history"' in html


def test_roster_page_shows_at_most_ten_recent_changes(roster_client, seed):
    """Older changes stay on the history page."""
    seed(*[("add" if n % 2 == 0 else "remove", 1, T0 + timedelta(minutes=n)) for n in range(1, 15)])
    seed(("add", 2, T0 + timedelta(minutes=100)))

    html = roster_client.get("/roster").text

    assert len(re.findall(r"<li class=\"change\">", html)) == 10


def test_roster_page_times_are_in_the_configured_timezone(make_client, seed):
    """Stored UTC times are shown in APP_TIMEZONE, with the zone named."""
    seed(("add", 1, T0))

    utc_page = make_client().get("/roster").text
    auckland_page = make_client(APP_TIMEZONE="Pacific/Auckland").get("/roster").text

    assert "Oct 29, 2026 12:00 UTC" in utc_page
    assert "Oct 30, 2026 01:00 NZDT" in auckland_page


def test_roster_page_search_box_is_labelled_and_wired_for_htmx(roster_client):
    """The search input has a label, asks the server as you type, and HTMX is loaded."""
    html = roster_client.get("/roster").text

    assert '<label for="q">' in html and 'id="q"' in html
    assert 'hx-get="/roster/search"' in html
    assert 'hx-trigger="input changed delay:250ms, search"' in html
    assert 'hx-target="#search-results"' in html
    assert '<script src="/static/htmx.min.js"' in html
    assert 'role="status"' in html


def test_roster_page_sends_the_csrf_token_with_every_htmx_request(roster_client, seed):
    """The page's hx-headers carry the session token, the same one the plain forms use."""
    seed(("add", 1, T0))  # a player gives the page a Remove form with the hidden token field
    html = roster_client.get("/roster").text

    body_headers = re.search(r"hx-headers='([^']*)'", html)
    assert body_headers, "the page sets no hx-headers"
    assert json.loads(body_headers.group(1)) == {"X-CSRF-Token": csrf_token(html)}


def test_roster_page_escapes_player_names(roster_client, seed):
    """A name that is HTML is shown as text, never as markup."""
    seed(("add", 5, T0))

    html = roster_client.get("/roster").text

    assert "&lt;b&gt;Evil&lt;/b&gt; Name" in html
    assert "<b>Evil</b>" not in html


def test_home_page_links_to_the_roster(roster_client):
    """The start page leads to the roster."""
    assert 'href="/roster"' in roster_client.get("/").text
