"""Tests for adding and removing players."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from nbaproj.db.models import RosterChange, RosterEntry
from nbaproj.roster.service import RosterService
from tests.web.helpers import csrf_token
from tests.web.roster.conftest import T0

DONCIC = "Luka Dončić"


def _token(client) -> str:
    """The session's CSRF token, from the home page (it always has a form)."""
    return csrf_token(client.get("/").text)


def _htmx_post(client, path: str, **form):
    """Post the way HTMX does: the CSRF token in a header, and the HX-Request header."""
    headers = {"HX-Request": "true", "X-CSRF-Token": _token(client)}
    return client.post(path, data=form, headers=headers, follow_redirects=False)


def _form_post(client, path: str, **form):
    """Post the way a plain HTML form does: the token as a hidden field, no HTMX."""
    form = {**form, "csrf_token": _token(client)}
    return client.post(path, data=form, follow_redirects=False)


def _count(engine, model) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_add_over_htmx_returns_the_updated_panel_and_clears_the_results(roster_client):
    """The response is just the panel (not a whole page) plus an empty search-results swap."""
    response = _htmx_post(roster_client, "/roster/add", player_id="1")

    assert response.status_code == 200
    html = response.text
    assert "<!doctype" not in html.lower()
    assert f"Added {DONCIC}." in html
    assert f"<span>{DONCIC}</span>" in html and ">Remove</button>" in html
    assert 'id="search-results"' in html and 'hx-swap-oob="true"' in html
    assert f"Added {DONCIC}\n" in html  # the recent-changes entry


def test_add_with_a_plain_form_redirects_to_the_roster(roster_client):
    """Without JavaScript the same form works: a redirect back to the roster page."""
    response = _form_post(roster_client, "/roster/add", player_id="2")

    assert response.status_code == 303
    assert response.headers["location"] == "/roster"
    assert "Jayson Tatum" in roster_client.get("/roster").text


def test_adding_someone_already_there_is_a_message_not_an_error(roster_client, seed, engine):
    """A second add says so and writes nothing."""
    seed(("add", 1, T0))

    response = _htmx_post(roster_client, "/roster/add", player_id="1")

    assert response.status_code == 200
    assert f"{DONCIC} is already on your roster." in response.text
    assert _count(engine, RosterChange) == 1


def test_an_unknown_player_id_is_a_generic_404(roster_client, engine):
    """An id the directory does not know is a plain not-found page that explains nothing."""
    response = _htmx_post(roster_client, "/roster/add", player_id="999")

    assert response.status_code == 404
    assert "999" not in response.text and "UnknownPlayer" not in response.text
    assert _count(engine, RosterEntry) == 0


@pytest.mark.parametrize("form", [{"player_id": "abc"}, {"player_id": ""}, {}])
def test_an_invalid_player_id_is_a_generic_400_not_a_validation_dump(roster_client, form):
    """A bad id gets a generic page, not a description of the app's internals."""
    response = _htmx_post(roster_client, "/roster/add", **form)

    assert response.status_code == 400
    assert "not valid" in response.text
    for leaked in ("int_parsing", "Input should be", '"detail"', '"loc"', '"msg"', "player_id"):
        assert leaked not in response.text


def test_a_simultaneous_duplicate_add_means_already_on_your_roster(roster_client, monkeypatch):
    """Two adds racing each other: the loser of the race is told it is already there."""

    def lose_the_race(*_args, **_kwargs):
        raise IntegrityError("INSERT INTO roster", {}, Exception("UNIQUE constraint failed"))

    monkeypatch.setattr(RosterService, "add", lose_the_race)

    response = _htmx_post(roster_client, "/roster/add", player_id="1")

    assert response.status_code == 200
    assert "already on your roster" in response.text


def test_remove_over_htmx_returns_the_updated_panel(roster_client, seed):
    """Removing returns the panel without that player, with a message and the history line."""
    seed(("add", 1, T0), ("add", 2, T0))

    response = _htmx_post(roster_client, "/roster/remove", player_id="1")

    assert response.status_code == 200
    html = response.text
    assert f"Removed {DONCIC}." in html
    assert f"<span>{DONCIC}</span>" not in html and "<span>Jayson Tatum</span>" in html
    assert f"Removed {DONCIC}\n" in html


def test_remove_with_a_plain_form_redirects_to_the_roster(roster_client, seed):
    """The no-JavaScript fallback for remove redirects back to the page."""
    seed(("add", 1, T0))

    response = _form_post(roster_client, "/roster/remove", player_id="1")

    assert response.status_code == 303
    assert response.headers["location"] == "/roster"
    assert "No players yet" in roster_client.get("/roster").text


@pytest.mark.parametrize("player_id", ["2", "999"])
def test_removing_someone_not_on_the_roster_is_a_message(roster_client, player_id, engine):
    """Removing a player who is not there (even an unknown id) says so and writes nothing."""
    response = _htmx_post(roster_client, "/roster/remove", player_id=player_id)

    assert response.status_code == 200
    assert "was not on your roster" in response.text
    assert _count(engine, RosterChange) == 0


def test_the_remove_button_asks_for_confirmation(roster_client, seed):
    """A stray tap on a phone cannot drop a player: HTMX asks first."""
    seed(("add", 1, T0))

    html = roster_client.get("/roster").text

    assert f'hx-confirm="Remove {DONCIC} from your roster?"' in html


@pytest.mark.parametrize("path", ["/roster/add", "/roster/remove"])
def test_add_and_remove_need_a_csrf_token(roster_client, seed, engine, path):
    """Without the token nothing changes, whatever the request looks like."""
    seed(("add", 1, T0))

    plain = roster_client.post(path, data={"player_id": "2"}, headers={"HX-Request": "true"})
    wrong = roster_client.post(
        path, data={"player_id": "2"}, headers={"HX-Request": "true", "X-CSRF-Token": "nope"}
    )

    assert plain.status_code == wrong.status_code == 403
    assert _count(engine, RosterEntry) == 1 and _count(engine, RosterChange) == 1


def test_changes_are_committed_and_visible_on_the_next_page_load(roster_client, engine):
    """The change is saved before the response is sent: the next request sees it."""
    _htmx_post(roster_client, "/roster/add", player_id="3")

    assert "Nikola Jokić" in roster_client.get("/roster").text
    assert _count(engine, RosterEntry) == 1 and _count(engine, RosterChange) == 1
