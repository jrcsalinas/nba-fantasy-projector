"""Tests for the Alembic migrations."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

CORE_TABLES = {"team", "player", "game", "game_log", "refresh_log", "job_lock"}
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
