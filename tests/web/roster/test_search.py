"""Tests for the type-ahead search results."""

import re

import pytest

from nbaproj.roster.directory import PlayerDirectory
from tests.web.roster.conftest import T0


def _search(client, query: str) -> str:
    response = client.get("/roster/search", params={"q": query})
    assert response.status_code == 200
    return response.text


def test_search_returns_matches_with_add_buttons(roster_client):
    """A match shows the name and a form that adds that player (id and CSRF token included)."""
    html = _search(roster_client, "doncic")

    assert "Luka Dončić" in html
    assert 'action="/roster/add"' in html and 'hx-post="/roster/add"' in html
    assert 'hx-target="#roster-panel"' in html
    assert re.findall(r'name="player_id" value="(\d+)"', html) == ["1"]
    assert 'name="csrf_token"' in html
    assert ">Add</button>" in html


def test_search_shows_on_your_roster_instead_of_add(roster_client, seed):
    """Someone already on the roster gets a note, not a button that does nothing."""
    seed(("add", 1, T0))

    html = _search(roster_client, "doncic")

    assert "Luka Dončić" in html
    assert "On your roster" in html
    assert ">Add</button>" not in html


@pytest.mark.parametrize("query", ["", "j", " ", "'"])
def test_search_hints_at_two_characters_instead_of_listing_the_league(roster_client, query):
    """A query that is too short explains why nothing is listed."""
    html = _search(roster_client, query)

    assert "at least 2" in html
    assert "Jayson" not in html and ">Add</button>" not in html


def test_search_says_when_nothing_matches(roster_client):
    """No match is stated plainly, and the query is shown so a typo is easy to see."""
    html = _search(roster_client, "zzzz")

    assert "No players found" in html and "zzzz" in html


def test_search_escapes_the_query_it_echoes(roster_client):
    """What was typed is shown as text, never run as markup."""
    html = _search(roster_client, "<script>alert(1)</script>")

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_search_caps_results_at_ten(make_client):
    """A common prefix lists at most ten players."""
    many = PlayerDirectory([(n, f"Jamal Player{n:02d}") for n in range(1, 26)])
    client = make_client(directory_override=many)

    html = _search(client, "jamal")

    assert html.count('class="match"') == 10
