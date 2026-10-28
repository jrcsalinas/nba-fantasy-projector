"""Tests for CSRF protection on every unsafe request."""

import pytest

from tests.web.conftest import TEST_PASSWORD
from tests.web.helpers import api_routes, csrf_token, log_in

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _still_signed_in(client) -> bool:
    return client.get("/", follow_redirects=False).status_code == 200


def test_post_without_a_token_is_403(client):
    """A signed-in POST with no token is refused and changes nothing."""
    log_in(client)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 403
    assert _still_signed_in(client)


@pytest.mark.parametrize("wrong", ["not-the-token", "x" * 43, "café"])
def test_post_with_a_wrong_token_is_403(client, wrong):
    """A token that is not the session's is refused, including one with non-ASCII text."""
    log_in(client)

    response = client.post("/logout", data={"csrf_token": wrong}, follow_redirects=False)

    assert response.status_code == 403
    assert _still_signed_in(client)


def test_token_in_the_form_field_or_the_header_is_accepted(client):
    """The form field works for plain forms, the header for HTMX requests."""
    log_in(client)
    token = csrf_token(client.get("/").text)
    by_header = client.post("/logout", headers={"X-CSRF-Token": token}, follow_redirects=False)
    assert by_header.status_code == 303

    log_in(client)
    token = csrf_token(client.get("/").text)
    by_field = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert by_field.status_code == 303


def test_get_requests_never_need_a_token(client):
    """Reading a page needs no token, signed in or not."""
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 200
    log_in(client)
    assert client.get("/").status_code == 200


def test_login_post_needs_the_pre_login_token(client):
    """The login form is protected too: no page visit first, no token, no login."""
    def post_login(**form):
        return client.post(
            "/login", data={"password": TEST_PASSWORD, **form}, follow_redirects=False
        )

    assert post_login().status_code == 403

    token = csrf_token(client.get("/login").text)
    assert post_login(csrf_token=token + "x").status_code == 403
    assert client.get("/", follow_redirects=False).status_code == 303

    assert post_login(csrf_token=token).status_code == 303


def test_every_unsafe_route_rejects_a_missing_token(app, client):
    """Checked route by route, so a route added later cannot forget CSRF protection."""
    unsafe = [(m, p) for m, p in api_routes(app.routes) if m in UNSAFE_METHODS]
    assert ("POST", "/login") in unsafe and ("POST", "/logout") in unsafe

    log_in(client)
    for method, path in unsafe:
        response = client.request(method, path, follow_redirects=False)
        assert response.status_code == 403, (method, path)
