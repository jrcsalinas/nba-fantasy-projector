"""Tests for the Alembic migrations."""

import io
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from nbaproj.db.models import Base

from tests.db.tables import CORE_TABLES

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@pytest.fixture(name="migration_setup")
def migration_setup_fixture(tmp_path):
    """An Alembic config and a SQLite engine pointing at a fresh temporary database."""
    database_url = f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    yield config, engine
    engine.dispose()


def _app_tables(engine):
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def test_migrations_upgrade_and_downgrade_on_empty_sqlite(migration_setup):
    """Upgrading builds the core tables, and downgrading removes them again."""
    config, engine = migration_setup

    command.upgrade(config, "head")
    assert _app_tables(engine) == CORE_TABLES

    command.downgrade(config, "base")
    assert _app_tables(engine) == set()


def test_migrated_schema_matches_the_models(migration_setup):
    """After upgrading to head, autogenerate finds nothing left to change."""
    config, engine = migration_setup
    command.upgrade(config, "head")

    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == []


def test_env_falls_back_to_database_url(tmp_path, monkeypatch):
    """With no URL in the Alembic config, env.py reads DATABASE_URL through the settings."""
    database_url = f"sqlite:///{(tmp_path / 'from_env.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(ALEMBIC_INI))
    assert config.get_main_option("sqlalchemy.url") in (None, "")

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        assert _app_tables(engine) == CORE_TABLES
    finally:
        engine.dispose()


def test_game_log_has_a_player_id_index_after_migration(migration_setup):
    """The migrated database has the same player_id index as the models."""
    config, engine = migration_setup
    command.upgrade(config, "head")

    indexes = inspect(engine).get_indexes("game_log")

    assert any(index["column_names"] == ["player_id"] for index in indexes)


def test_migration_renders_valid_postgres_ddl_for_the_roster_tables():
    """Postgres is not available here, so check the SQL the roster migration would send it."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", "postgresql://user:secret@host/db")
    config.output_buffer = io.StringIO()

    command.upgrade(config, "0002:0003", sql=True)

    ddl = config.output_buffer.getvalue()
    assert "CREATE TABLE roster (" in ddl
    assert "PRIMARY KEY (owner, player_id)" in ddl
    assert "FOREIGN KEY(player_id) REFERENCES player (player_id)" in ddl
    assert "CREATE TABLE roster_history (" in ddl
    assert "id SERIAL NOT NULL" in ddl
    index = "CREATE INDEX ix_roster_history_owner_changed_at ON roster_history"
    assert f"{index} (owner, changed_at)" in ddl
    assert "secret" not in ddl
