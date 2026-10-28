"""Shared fixtures for all tests."""

import pytest
from sqlalchemy.orm import Session

from nbaproj.db.models import Base
from nbaproj.db.session import create_db_engine


@pytest.fixture(name="session")
def session_fixture():
    """A session on a fresh in-memory SQLite database with all tables created."""
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()
