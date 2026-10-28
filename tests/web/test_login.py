"""Tests for login, logout, and the session."""

import pytest
from fastapi.testclient import TestClient

from nbaproj.web.app import create_app
from nbaproj.web.settings import load_web_settings

from tests.web.helpers import api_routes, csrf_token, log_in

OPEN_PATHS = {"/login", "/healthz"}
GENERIC_ERROR = "That password is not right."


def test_login_page_has_a_csrf_token(client):
    """The login form carries a token and a password field."""
    response = client.get("/login")

    assert response.status_code == 200
    assert len(csrf_token(response.text)) >= 32
    assert 'type="password"' in response.text


def test_right_password_logs_in_and_redirects_home(client):
    """The right password starts a session and lands on the home page."""
    response = log_in(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    home = client.get("/")
    assert home.status_code == 200
    assert "josh" in home.text


@pytest.mark.parametrize("password", ["wrong password", ""])
def test_wrong_or_empty_password_gives_the_same_generic_401(client, password):
    """Nothing tells a wrong password from an empty one, and nobody is signed in."""
    response = log_in(client, password)

    assert response.status_code == 401
    assert GENERIC_ERROR in response.text
    assert password == "" or password not in response.text
    assert client.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("secure", [True, False])
def test_session_cookie_flags(web_env, secure):
    """The session cookie is HttpOnly and SameSite=Lax, and Secure only when configured."""
    web_env["COOKIE_SECURE"] = "true" if secure else "false"
    app = create_app(load_web_settings(web_env))
    base_url = "https://testserver" if secure else "http://testserver"

    with TestClient(app, base_url=base_url) as client:
        cookie = log_in(client).headers["set-cookie"].lower()

    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert ("secure" in cookie.replace("samesite", "")) is secure


def test_login_replaces_the_previous_session(client):
    """Logging in issues a new token and a new session cookie, so nothing carries over."""
    before_token = csrf_token(client.get("/login").text)
    before_cookie = client.cookies.get("session")

    log_in(client)

    assert csrf_token(client.get("/").text) != before_token
    assert client.cookies.get("session") != before_cookie


def test_routes_require_login_except_login_and_healthz(app, client):
    """Every other route sends a browser to /login and answers an HTMX request with 401."""
    routes = api_routes(app.routes)
    protected = [(method, path) for method, path in routes if path not in OPEN_PATHS]
    assert ("GET", "/") in protected and ("POST", "/logout") in protected

    for method, path in protected:
        browser = client.request(method, path, follow_redirects=False)
        assert browser.status_code == 303, (method, path)
        assert browser.headers["location"] == "/login", (method, path)
        htmx = client.request(method, path, headers={"HX-Request": "true"}, follow_redirects=False)
        assert htmx.status_code == 401, (method, path)


def test_home_page_shows_the_owner_and_a_logout_button(client):
    """The signed-in page names the owner, has a logout form, and is never cached."""
    log_in(client)

    home = client.get("/")

    assert "Signed in as josh" in home.text
    assert 'action="/logout"' in home.text
    assert home.headers["cache-control"] == "no-store"


def test_logout_clears_the_session(client):
    """Posting to /logout ends the session."""
    log_in(client)
    token = csrf_token(client.get("/").text)

    response = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_page_sends_a_signed_in_visitor_home(client):
    """Someone already signed in has no use for the login form."""
    log_in(client)

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
