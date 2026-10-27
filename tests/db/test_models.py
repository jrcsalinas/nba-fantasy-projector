"""Tests for the core database models."""

import pytest
from sqlalchemy import create_engine, inspect

from nbaproj.db.models import Base

CORE_TABLES = {"team", "player", "game", "game_log", "refresh_log", "job_lock"}


@pytest.fixture(name="engine")
def engine_fixture():
    """An empty in-memory SQLite engine, closed after the test."""
    engine = create_engine("sqlite://")
    yield engine
    engine.dispose()


def test_models_create_all_core_tables(engine):
    """The models define exactly the six core tables."""
    Base.metadata.create_all(engine)

    assert set(inspect(engine).get_table_names()) == CORE_TABLES
