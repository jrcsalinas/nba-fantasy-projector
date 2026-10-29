"""Tests for the trends page. The seeded games are the three-player data of the service tests:
player 1's points trend over the last 7 days is +9.0 a game (30 against 21), +0.4 standard
deviations, which is Steady."""

import pytest
from sqlalchemy.orm import Session

from tests.services.conftest import three_players
from tests.web.helpers import add_log
from tests.web.refresh.conftest import HOUR, NOW

WINDOW_LINKS = ("/trends?window=7", "/trends?window=14", "/trends?window=30")


def test_trends_page_shows_a_card_for_each_roster_player(make_client, seed, roster_of):
    """A player with a trend gets his numbers and a sentence; one without gets the reason."""
    three_players(seed)
    roster_of("josh", 1, 4)

    html = make_client().get("/trends?window=7").text

    assert "Ada One" in html and "Di Four" in html and "Cy Three" not in html
    assert not any(word in html for word in ("Hot", "Cold", "Steady"))  # too few players to label
    ada = html.split("<h2>Ada One")[1].split("</section>")[0]
    assert "PTS +9.0 over the last 7 days" in ada
    assert "7 games in the last 7 days" in ada and "10 this season" in ada
    assert "+0.4" in ada
    di = html.split("<h2>Di Four")[1].split("</section>")[0]
    assert "No trend: no games this season" in di


def test_the_default_window_is_14_days_and_the_links_choose_7_14_and_30(
    make_client, seed, roster_of
):
    """Three plain links (no JavaScript needed); the one being shown is marked as current."""
    three_players(seed)
    roster_of("josh", 1)
    client = make_client()

    default = client.get("/trends").text
    seven = client.get("/trends?window=7").text

    for link in WINDOW_LINKS:
        assert f'href="{link}"' in default
    assert 'href="/trends?window=14" aria-current="page"' in default
    assert 'href="/trends?window=7" aria-current="page"' in seven
    assert "over the last 14 days" in default


@pytest.mark.parametrize("window", ["5", "0", "-7", "abc", "7.5", ""])
def test_any_other_window_is_the_generic_400(make_client, window):
    """Only 7, 14 and 30 are windows; anything else is the plain "not valid" page."""
    response = make_client().get(f"/trends?window={window}")

    assert response.status_code == 400
    assert "not valid" in response.text
    for leaked in ("literal_error", "Input should be"):
        assert leaked not in response.text


def test_trends_page_escapes_player_names(make_client, seed, roster_of):
    """A name that is HTML is shown as text, never as markup."""
    three_players(seed)
    roster_of("josh", 5)

    html = make_client().get("/trends").text

    assert "&lt;b&gt;Evil&lt;/b&gt; Name" in html
    assert "<b>Evil</b>" not in html


def test_trends_page_needs_a_login(make_client):
    """Signed out, a browser goes to the login page and HTMX gets a 401."""
    client = make_client(signed_in=False)

    browser = client.get("/trends", follow_redirects=False)
    htmx = client.get("/trends", headers={"HX-Request": "true"}, follow_redirects=False)

    assert browser.status_code == 303 and browser.headers["location"] == "/login"
    assert htmx.status_code == 401


def test_trends_page_empty_states(make_client, seed, roster_of):
    """No roster, no games, and a roster with games each say what to do next."""
    client = make_client()
    assert "Add players to your roster first" in client.get("/trends").text

    roster_of("josh", 1)
    no_games = client.get("/trends").text
    assert "No game data yet" in no_games and "Ada One" not in no_games

    three_players(seed)
    assert "No game data yet" not in client.get("/trends").text


def test_home_and_roster_pages_link_to_trends(make_client):
    """You can reach the page from the two pages you already use."""
    client = make_client()

    assert 'href="/trends"' in client.get("/").text
    assert 'href="/trends"' in client.get("/roster").text


def test_the_freshness_banner_shows_on_the_trends_page(make_client, engine):
    """Like every signed-in page, it says when the data was last checked."""
    with Session(engine) as session:
        add_log(session, NOW, "succeeded", 3 * HOUR)

    response = make_client().get("/trends")

    assert response.status_code == 200
    assert 'id="freshness"' in response.text and "Data checked" in response.text
