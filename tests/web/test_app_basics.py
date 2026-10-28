"""Tests for the app skeleton: health check, security headers, and generic error pages."""

import pytest
from fastapi.testclient import TestClient

from nbaproj.config import ConfigError
from nbaproj.web.app import create_app_from_env

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
}


@pytest.fixture(name="client")
def client_fixture(app):
    """A test client that returns error pages instead of re-raising server errors."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_healthz_is_open_and_needs_no_database(client, monkeypatch):
    """The health check answers without a login, even if the database setting is broken."""
    monkeypatch.setenv("DATABASE_URL", "not-a-url")

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_security_headers_are_on_every_response(app, client):
    """Success, not-found, and server-error responses all carry the security headers."""

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    for path, status in (("/healthz", 200), ("/no-such-page", 404), ("/boom", 500)):
        response = client.get(path)
        assert response.status_code == status
        for name, value in SECURITY_HEADERS.items():
            assert response.headers.get(name) == value, (path, name)


def test_unknown_path_is_a_generic_404(client):
    """The not-found page says so, without repeating the requested path."""
    response = client.get("/definitely-not-a-page")

    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "not found" in response.text.lower()
    assert "definitely-not-a-page" not in response.text


def test_unhandled_error_is_a_generic_500_without_details(app, client):
    """A crash shows a generic page: no message, exception name, traceback, or path."""

    @app.get("/explode")
    def explode():
        raise RuntimeError("secret-detail-123")

    response = client.get("/explode")

    assert response.status_code == 500
    assert "something went wrong" in response.text.lower()
    for leaked in ("secret-detail-123", "RuntimeError", "Traceback", "/explode", "File \""):
        assert leaked not in response.text


def test_create_app_from_env_builds_the_app_from_the_process_environment(monkeypatch, web_env):
    """This is what uvicorn's --factory calls: no arguments, settings from the environment."""
    for name, value in web_env.items():
        monkeypatch.setenv(name, value)

    with TestClient(create_app_from_env()) as client:
        assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("missing", ["SESSION_SECRET", "PASSWORD_HASH"])
def test_create_app_from_env_fails_clearly_when_a_secret_is_missing(monkeypatch, web_env, missing):
    """The app does not start without its secrets, and says which one is missing."""
    for name, value in web_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing)

    with pytest.raises(ConfigError, match=missing):
        create_app_from_env()
