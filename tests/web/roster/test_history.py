"""Tests for the full history page."""

import re
from datetime import timedelta

from tests.web.roster.conftest import T0


def _position(html: str, text: str) -> int:
    assert text in html, f"{text!r} is not on the page"
    return html.index(text)


def test_history_page_lists_changes_newest_first_with_name_action_time_and_source(
    roster_client, seed
):
    """Each line says what happened, to whom, when (local time), and where it came from."""
    seed(
        ("add", 1, T0),
        ("add", 2, T0 + timedelta(minutes=1)),
        ("remove", 1, T0 + timedelta(minutes=2)),
    )

    response = roster_client.get("/roster/history")

    assert response.status_code == 200
    html = response.text
    newest_first = ("Removed Luka Dončić", "Added Jayson Tatum", "Added Luka Dončić")
    positions = [_position(html, line) for line in newest_first]
    assert positions == sorted(positions)
    assert "Oct 29, 2026 12:02 UTC" in html and "Oct 29, 2026 12:00 UTC" in html
    assert html.count(">manual<") == 3
    assert 'href="/roster"' in html


def test_history_page_shows_at_most_fifty_changes(roster_client, seed):
    """Sixty changes in the database, fifty on the page."""
    seed(*[("add" if n % 2 else "remove", 1, T0 + timedelta(minutes=n)) for n in range(1, 61)])

    html = roster_client.get("/roster/history").text

    assert len(re.findall(r'<li class="change">', html)) == 50


def test_history_page_only_shows_the_owners_changes(roster_client, seed):
    """Another owner's changes in the same database never appear."""
    seed(("add", 1, T0))
    seed(("add", 2, T0), owner="sam")

    html = roster_client.get("/roster/history").text

    assert "Luka Dončić" in html and "Jayson Tatum" not in html


def test_an_empty_history_says_so(roster_client):
    """With no changes the page explains instead of showing an empty list."""
    assert "No changes yet" in roster_client.get("/roster/history").text
