"""Tests that the refresh token opens nothing but /refresh and never leaks."""

import logging

from tests.web.helpers import api_routes, csrf_token

TOKEN = "cron-token-" + "c" * 30
BEARER = {"Authorization": f"Bearer {TOKEN}"}
OPEN = {("GET", "/login"), ("POST", "/login"), ("GET", "/healthz"), ("POST", "/refresh")}


def test_refresh_is_the_only_route_that_does_its_own_authentication(make_client):
    """Sent to any other route, even the valid token is not a login: a browser is turned away."""
    client = make_client(signed_in=False, REFRESH_TOKEN=TOKEN)
    others = [route for route in api_routes(client.app.routes) if route not in OPEN]
    assert ("GET", "/refresh/status") in others and ("GET", "/roster") in others

    for method, path in others:
        response = client.request(method, path, headers=BEARER, follow_redirects=False)
        assert response.status_code == 303, (method, path)
        assert response.headers["location"] == "/login", (method, path)
    assert client.post("/refresh", headers=BEARER).status_code == 202


def test_the_token_never_appears_in_any_response_or_log(make_client, caplog):
    """Whatever is asked of the app, neither the pages nor the logs ever contain the token."""
    client = make_client(REFRESH_TOKEN=TOKEN)
    token = csrf_token(client.get("/").text)
    seen = []

    with caplog.at_level(logging.DEBUG):
        seen.append(client.post("/refresh", headers=BEARER))
        seen.append(client.post("/refresh", headers={"Authorization": "Bearer wrong-" + "w" * 30}))
        seen.append(client.post("/refresh", headers={"X-CSRF-Token": token}))
        seen.append(client.post("/refresh", headers={"Authorization": f"Basic {TOKEN}"}))
        pages = ("/", "/roster", "/roster/history", "/refresh/status")
        seen += [client.get(path) for path in pages]
        seen.append(client.get("/no/such/page"))

    for response in seen:
        assert TOKEN not in response.text
        assert TOKEN not in str(response.headers)
    assert TOKEN not in caplog.text
