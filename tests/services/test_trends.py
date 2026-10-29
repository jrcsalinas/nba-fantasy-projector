"""Tests for the trends service. Expected numbers are worked out by hand."""

from datetime import date, datetime, timezone

import pytest

from nbaproj.services.trends import load_trends
from tests.services.conftest import JAN_1_NOON_UTC, three_players


def test_all_players_are_scored_but_only_roster_players_are_returned(seed, roster_of):
    """Player 1's trend needs players 2 and 3 as the population, yet only player 1 comes back."""
    three_players(seed)

    view = load_trends(seed.session, roster_of("josh", 1), "UTC", window_days=7)

    assert [row.player_id for row in view.rows] == [1]
    row = view.rows[0]
    points = next(c for c in row.trend.categories if c.key == "pts")
    assert points.z_change == pytest.approx(0.4193, abs=1e-3)
    assert points.change == pytest.approx(30.0 - 21.0)
    assert (row.trend.season_games, row.trend.window_games) == (10, 7)
    assert row.name == "Ada One" and view.has_data is True


@pytest.mark.parametrize(("days", "games_in_window"), [(7, 7), (3, 3), (30, 10)])
def test_the_windows_end_at_the_latest_game_day_in_the_data(seed, roster_of, days, games_in_window):
    """A game daily to January 10: the last 7 days are 4 to 10 (7 games), the last 3 are 8 to 10."""
    three_players(seed)

    view = load_trends(seed.session, roster_of("josh", 1), "UTC", window_days=days)

    assert view.last_game_day == date(2026, 1, 10)
    assert view.rows[0].trend.window_games == games_in_window


@pytest.mark.parametrize(
    ("zone", "last_day"), [("UTC", date(2026, 1, 11)), ("America/New_York", date(2026, 1, 10))]
)
def test_game_days_follow_the_configured_timezone(seed, roster_of, zone, last_day):
    """A game that tips off at 03:00 UTC on the 11th is the evening of the 10th in New York."""
    seed.daily(range(1, 10), lambda d: {1: {"pts": 10}, 2: {"pts": 20}})
    seed.game({1: {"pts": 10}, 2: {"pts": 20}}, datetime(2026, 1, 11, 3, 0, tzinfo=timezone.utc))

    view = load_trends(seed.session, roster_of("josh", 1), zone, window_days=7)

    assert view.last_game_day == last_day


def test_only_the_season_of_the_latest_game_is_used(seed, roster_of):
    """Ten games last season and six this season: the season has six games, not sixteen."""
    seed.daily(range(1, 11), lambda d: {1: {"pts": 5}, 2: {"pts": 5}}, season="2024-25")
    for day in range(1, 7):
        march = JAN_1_NOON_UTC.replace(month=3, day=day)
        seed.game({1: {"pts": 9}, 2: {"pts": 9}}, march, "2025-26")

    view = load_trends(seed.session, roster_of("josh", 1), "UTC", window_days=7)

    assert view.season == "2025-26"
    assert view.rows[0].trend.season_games == 6


def test_a_roster_player_with_no_games_is_listed_as_having_none(seed, roster_of):
    """Player 4 has no box score lines: he is on the page, with the reason, not dropped."""
    three_players(seed)

    view = load_trends(seed.session, roster_of("josh", 1, 4), "UTC", window_days=7)

    by_id = {row.player_id: row for row in view.rows}
    assert by_id[4].trend.has_trend is False
    assert by_id[4].trend.reason == "no games this season"
    assert by_id[1].trend.has_trend is True


def test_an_empty_roster_and_an_empty_database_give_empty_results(seed, roster_of):
    """No roster: no rows. No games: no data, and each roster player says he has no games."""
    nobody = load_trends(seed.session, roster_of("josh"), "UTC", window_days=14)
    no_data = load_trends(seed.session, roster_of("sam", 2), "UTC", window_days=14)

    assert not nobody.rows and nobody.has_data is False
    assert (no_data.has_data, no_data.season, no_data.last_game_day) == (False, None, None)
    assert [(r.player_id, r.trend.reason) for r in no_data.rows] == [(2, "no games this season")]


def test_the_roster_is_scoped_to_the_owner(seed, roster_of):
    """Josh holds player 1 and Sam holds player 2: each sees only his own player."""
    three_players(seed)
    josh, sam = roster_of("josh", 1), roster_of("sam", 2)

    josh_view = load_trends(seed.session, josh, "UTC", window_days=7)
    sam_view = load_trends(seed.session, sam, "UTC", window_days=7)

    assert [row.player_id for row in josh_view.rows] == [1]
    assert [row.player_id for row in sam_view.rows] == [2]
