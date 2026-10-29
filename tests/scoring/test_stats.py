"""Tests for per-player, per-period stats. Expected numbers are worked out by hand."""

import math

import pandas as pd
from pandas.testing import assert_frame_equal

from nbaproj.scoring.stats import period_stats
from tests.scoring.conftest import day, make_logs


def test_period_stats_average_counting_stats_per_game():
    """Two games: points 20 and 30 average 25; minutes 30 and 34 average 32; turnovers 1 and 4."""
    logs = make_logs(
        {"player_id": 1, "day": 1, "minutes": 30.0, "pts": 20, "reb": 5, "ast": 8, "stl": 1,
         "blk": 0, "tpm": 2, "tov": 1},
        {"player_id": 1, "day": 2, "minutes": 34.0, "pts": 30, "reb": 7, "ast": 4, "stl": 3,
         "blk": 2, "tpm": 4, "tov": 4},
    )

    stats = period_stats(logs, day(1), day(2)).loc[1]

    assert stats["games"] == 2
    assert stats["minutes"] == 32.0
    assert (stats["pts"], stats["reb"], stats["ast"]) == (25.0, 6.0, 6.0)
    assert (stats["stl"], stats["blk"], stats["tpm"], stats["tov"]) == (2.0, 1.0, 3.0, 2.5)


def test_period_stats_pool_shooting_percentages_over_attempts():
    """1-for-1 then 1-for-9 is 2 of 10 = 20%, not the 55% a mean of the two nights would say."""
    logs = make_logs(
        {"player_id": 1, "day": 1, "fgm": 1, "fga": 1, "ftm": 3, "fta": 3},
        {"player_id": 1, "day": 2, "fgm": 1, "fga": 9, "ftm": 1, "fta": 7},
    )

    stats = period_stats(logs, day(1), day(2)).loc[1]

    assert math.isclose(stats["fg_pct"], 0.2)
    assert math.isclose(stats["ft_pct"], 0.4)


def test_period_stats_compute_fg_and_ft_impact_against_the_pooled_league_percentage():
    """League FG is 8 of 20 = 40%: 6-for-10 is +2 makes above average, 2-for-10 is -2.

    League FT is 12 of 20 = 60%: 8-for-10 is +2, 4-for-10 is -2.
    """
    logs = make_logs(
        {"player_id": 1, "day": 1, "fgm": 6, "fga": 10, "ftm": 8, "fta": 10},
        {"player_id": 2, "day": 1, "fgm": 2, "fga": 10, "ftm": 4, "fta": 10},
    )

    stats = period_stats(logs, day(1), day(1))

    assert math.isclose(stats.loc[1, "fg_impact"], 2.0)
    assert math.isclose(stats.loc[2, "fg_impact"], -2.0)
    assert math.isclose(stats.loc[1, "ft_impact"], 2.0)
    assert math.isclose(stats.loc[2, "ft_impact"], -2.0)


def test_period_stats_ignore_games_outside_the_period():
    """Only days 2 to 4 (both ends included) count: points 20, 30, 40 average 30."""
    logs = make_logs(*[{"player_id": 1, "day": n, "pts": 10 * n} for n in range(1, 6)])

    stats = period_stats(logs, day(2), day(4)).loc[1]

    assert stats["games"] == 3
    assert stats["pts"] == 30.0


def test_period_stats_average_usage_over_games_that_have_it():
    """A game with no usage figure is skipped, not counted as zero: (0.20 + 0.30) / 2."""
    logs = make_logs(
        {"player_id": 1, "day": 1, "usage_pct": 0.20},
        {"player_id": 1, "day": 2},
        {"player_id": 1, "day": 3, "usage_pct": 0.30},
    )

    stats = period_stats(logs, day(1), day(3)).loc[1]

    assert stats["games"] == 3
    assert math.isclose(stats["usage"], 0.25)


def test_period_stats_leave_usage_unknown_when_no_game_has_it():
    """Unknown is not zero."""
    logs = make_logs({"player_id": 1, "day": 1}, {"player_id": 1, "day": 2})

    assert math.isnan(period_stats(logs, day(1), day(2)).loc[1, "usage"])


def test_a_player_with_no_games_in_the_period_is_absent():
    """Player 2 only played on day 9, outside the period, so there is no row for him."""
    logs = make_logs({"player_id": 1, "day": 1}, {"player_id": 2, "day": 9})

    stats = period_stats(logs, day(1), day(2))

    assert list(stats.index) == [1]
    assert period_stats(logs, day(20), day(21)).empty


def test_period_stats_do_not_modify_their_input():
    """The caller's frame is left exactly as it was."""
    logs = make_logs({"player_id": 1, "day": 1, "pts": 10}, {"player_id": 2, "day": 2, "pts": 5})
    before = logs.copy(deep=True)

    period_stats(logs, day(1), day(2))

    assert_frame_equal(logs, before)
    assert isinstance(logs, pd.DataFrame)
