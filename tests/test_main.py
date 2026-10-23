from fastapi.testclient import TestClient

from nba_projector.main import app


def test_root_serves_dashboard_html() -> None:
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Rankings are statistical estimates, not guarantees." in response.text


def test_static_assets_are_served() -> None:
    client = TestClient(app)

    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_roster_page_serves_html_with_disclaimer() -> None:
    response = TestClient(app).get("/roster")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Rankings are statistical estimates, not guarantees." in response.text


def test_both_pages_link_to_each_other() -> None:
    client = TestClient(app)

    assert 'href="/roster"' in client.get("/").text
    assert 'href="/"' in client.get("/roster").text


def test_shared_and_roster_scripts_are_served() -> None:
    client = TestClient(app)

    assert client.get("/static/shared.js").status_code == 200
    assert client.get("/static/roster.js").status_code == 200
