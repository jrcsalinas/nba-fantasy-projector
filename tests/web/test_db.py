"""Tests for database access in the web app: one session per request, committed by the handler."""

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nbaproj.config import ConfigError
from nbaproj.db.models import Player
from nbaproj.web.app import create_app, create_app_from_env
from nbaproj.web.db import get_session
from nbaproj.web.settings import load_web_settings


@pytest.fixture(name="db_app")
def db_app_fixture(web_env, engine):
    """The app with a database and three throwaway routes for exercising the session."""
    app = create_app(load_web_settings(web_env), engine=engine)
    seen: list[Session] = []
    app.state.seen_sessions = seen

    @app.get("/t/add-and-commit")
    def add_and_commit(session: Session = Depends(get_session)):
        seen.append(session)
        session.add(Player(player_id=1, full_name="Committed"))
        session.commit()
        return {}

    @app.get("/t/add-without-commit")
    def add_without_commit(session: Session = Depends(get_session)):
        seen.append(session)
        session.add(Player(player_id=2, full_name="Never committed"))
        session.flush()
        return {}

    @app.get("/t/add-then-fail")
    def add_then_fail(session: Session = Depends(get_session)):
        session.add(Player(player_id=3, full_name="Failed request"))
        session.flush()
        raise RuntimeError("boom")

    @app.get("/t/names")
    def names(session: Session = Depends(get_session)):
        seen.append(session)
        return {"names": sorted(session.scalars(select(Player.full_name)))}

    return app


@pytest.fixture(name="db_client")
def db_client_fixture(db_app):
    """A client that shows a server error as the 500 page instead of raising it."""
    with TestClient(db_app, raise_server_exceptions=False) as client:
        yield client


def test_a_handler_that_commits_makes_its_change_visible_to_the_next_request(db_client):
    """What a handler commits is there for the next request."""
    db_client.get("/t/add-and-commit")

    assert db_client.get("/t/names").json() == {"names": ["Committed"]}


def test_an_uncommitted_change_is_rolled_back_when_the_request_ends(db_client):
    """A handler that forgets to commit changes nothing, because the session is rolled back."""
    db_client.get("/t/add-without-commit")

    assert db_client.get("/t/names").json() == {"names": []}


def test_an_error_in_a_handler_rolls_back_and_shows_the_generic_500_page(db_client):
    """A crash after a flush leaves nothing behind and shows no details."""
    response = db_client.get("/t/add-then-fail")

    assert response.status_code == 500
    assert "boom" not in response.text and "RuntimeError" not in response.text
    assert db_client.get("/t/names").json() == {"names": []}


def test_each_request_gets_its_own_session(db_app, db_client):
    """Sessions are not shared between requests, and none is left open afterwards."""
    db_client.get("/t/names")
    db_client.get("/t/names")

    first, second = db_app.state.seen_sessions
    assert first is not second
    assert not first.in_transaction() and not second.in_transaction()


def test_create_app_from_env_uses_database_url(monkeypatch, web_env, engine):
    """The app built from the environment talks to the database DATABASE_URL names."""
    for name, value in web_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", str(engine.url))
    app = create_app_from_env()

    @app.get("/t/count")
    def count(session: Session = Depends(get_session)):
        return {"n": session.scalar(select(func.count()).select_from(Player))}

    with TestClient(app) as client:
        assert client.get("/t/count").json() == {"n": 0}
        assert client.get("/healthz").status_code == 200


def test_the_engine_the_app_made_is_disposed_at_shutdown_but_an_injected_one_is_not(
    monkeypatch, web_env, engine
):
    """An app disposes only the engine it created itself."""
    for name, value in web_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", str(engine.url))
    owned = create_app_from_env()
    disposed: list[str] = []
    owned_engine = owned.state.engine
    real_dispose = owned_engine.dispose
    owned_engine.dispose = lambda: (disposed.append("owned"), real_dispose())

    with TestClient(owned):
        assert not disposed
    assert disposed == ["owned"]

    injected = create_app(load_web_settings(web_env), engine=engine)
    real_injected_dispose = engine.dispose
    engine.dispose = lambda: (disposed.append("injected"), real_injected_dispose())
    with TestClient(injected):
        pass
    assert disposed == ["owned"]


def test_create_app_from_env_rejects_a_bad_database_url_without_echoing_it(monkeypatch, web_env):
    """A URL SQLAlchemy cannot use stops startup, and the message never repeats the URL."""
    for name, value in web_env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DATABASE_URL", "not-a-url-with-secret-pw")

    with pytest.raises(ConfigError, match="DATABASE_URL") as raised:
        create_app_from_env()

    assert "secret-pw" not in str(raised.value)
