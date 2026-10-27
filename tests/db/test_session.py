"""Tests for engine and session setup."""

import pytest
from sqlalchemy.exc import IntegrityError

from nbaproj.db.models import GameLog, Player, Team
from nbaproj.db.session import create_db_engine, normalize_database_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql://u:p@h/db?sslmode=require", "postgresql+psycopg://u:p@h/db?sslmode=require"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///nbaproj.db", "sqlite:///nbaproj.db"),
    ],
)
def test_normalize_database_url_handles_postgres_forms(raw, expected):
    """Hosted Postgres URLs are rewritten for the psycopg driver; others are untouched."""
    assert normalize_database_url(raw) == expected


def test_create_db_engine_uses_psycopg_for_postgres_urls():
    """A plain postgresql:// URL builds an engine on the psycopg driver without connecting."""
    engine = create_db_engine("postgresql://u:p@localhost/db")

    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"


def test_engine_rejects_game_log_row_with_unknown_game(session):
    """A game_log row pointing at a missing game is rejected on SQLite."""
    session.add_all([
        Team(team_id=1, tricode="BOS", name="Celtics", city="Boston"),
        Player(player_id=1, full_name="Test Player"),
    ])
    session.commit()
    session.add(GameLog(
        game_id="0000000000", player_id=1, team_id=1, minutes=30.0,
        pts=10, reb=5, ast=3, stl=1, blk=0, tpm=2, fgm=4, fga=9,
        ftm=0, fta=0, tov=1, source="live",
    ))

    with pytest.raises(IntegrityError):
        session.commit()
