"""Tests for the idempotent box score loader."""

from sqlalchemy import func, select

from nbaproj.db.models import Game, GameLog, Player, Team
from nbaproj.ingestion.loader import store_boxscore
from nbaproj.ingestion.parsing import parse_boxscore


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_store_boxscore_inserts_game_players_and_lines(session, boxscore_payload):
    """One box score creates its teams, game, players and one log row per player."""
    rows = store_boxscore(session, parse_boxscore(boxscore_payload))

    assert rows == 5
    assert _count(session, Team) == 2
    assert _count(session, Player) == 5
    assert _count(session, Game) == 1
    assert _count(session, GameLog) == 5

    game = session.get(Game, "0022500001")
    assert game.season == "2025-26"
    assert game.game_type == "regular_season"
    assert game.status == "final"
    assert (game.home_team_id, game.away_team_id) == (1610612747, 1610612744)

    line = session.get(GameLog, ("0022500001", 1001))
    assert (line.pts, line.minutes, line.source) == (28, 36.0, "live")
    assert round(line.usage_pct, 2) == 30.91
    assert session.get(GameLog, ("0022500001", 2003)).usage_pct is None


def test_store_boxscore_twice_leaves_the_same_rows(session, boxscore_payload):
    """Loading the same box score again does not duplicate anything."""
    box = parse_boxscore(boxscore_payload)
    store_boxscore(session, box)
    store_boxscore(session, box)

    assert _count(session, Team) == 2
    assert _count(session, Player) == 5
    assert _count(session, Game) == 1
    assert _count(session, GameLog) == 5


def test_store_boxscore_updates_changed_stats(session, boxscore_payload):
    """A corrected box score overwrites the earlier stat line."""
    store_boxscore(session, parse_boxscore(boxscore_payload))
    home_players = boxscore_payload["game"]["homeTeam"]["players"]
    home_players[0]["statistics"]["points"] = 30

    store_boxscore(session, parse_boxscore(boxscore_payload))

    assert session.get(GameLog, ("0022500001", 1001)).pts == 30
    assert _count(session, GameLog) == 5
