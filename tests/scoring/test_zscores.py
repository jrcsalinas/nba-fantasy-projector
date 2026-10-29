"""Tests for population z-scores. Expected numbers are worked out by hand."""

import math

import pytest

from nbaproj.scoring.categories import CATEGORIES
from nbaproj.scoring.zscores import z_scores
from tests.scoring.conftest import make_stats

KEYS = [category.key for category in CATEGORIES]


def test_z_scores_use_the_population_mean_and_standard_deviation():
    """Points 10, 20, 30: mean 20, population standard deviation sqrt(200 / 3) = 8.1650."""
    stats = make_stats(p1={"pts": 10.0}, p2={"pts": 20.0}, p3={"pts": 30.0})

    z = z_scores(stats, min_games=1)

    assert z["pts"].tolist() == pytest.approx([-1.22474, 0.0, 1.22474], abs=1e-4)
    assert list(z.columns) == KEYS


def test_turnovers_are_negated_so_fewer_is_better():
    """Turnovers 1 and 3: mean 2, standard deviation 1; the player with fewer scores +1."""
    stats = make_stats(p1={"tov": 1.0}, p2={"tov": 3.0})

    z = z_scores(stats, min_games=1)

    assert z["tov"].tolist() == pytest.approx([1.0, -1.0])


def test_shooting_percentages_are_scored_by_impact_not_by_raw_percentage():
    """The 90% shooter made 0.5 shots above average a game, the 50% shooter made 2.5.

    Impacts 0.5 and 2.5: mean 1.5, standard deviation 1, so -1 and +1, though raw
    percentage would rank them the other way round. Free throws work the same way.
    """
    stats = make_stats(
        p1={"fg_pct": 0.9, "fg_impact": 0.5, "ft_pct": 0.95, "ft_impact": 0.2},
        p2={"fg_pct": 0.5, "fg_impact": 2.5, "ft_pct": 0.6, "ft_impact": 2.2},
    )

    z = z_scores(stats, min_games=1)

    assert z["fg"].tolist() == pytest.approx([-1.0, 1.0])
    assert z["ft"].tolist() == pytest.approx([-1.0, 1.0])


def test_a_category_nobody_varies_in_scores_zero():
    """Everyone averages 10 points: no standard deviation to divide by, so zero, not NaN."""
    stats = make_stats(p1={"pts": 10.0}, p2={"pts": 10.0}, p3={"pts": 10.0})

    z = z_scores(stats, min_games=1)

    assert z["pts"].tolist() == [0.0, 0.0, 0.0]
    assert not z.isna().any().any()


def test_players_under_the_minimum_games_get_no_score_and_do_not_shift_the_others():
    """A one-game 1000-point fluke is not scored and leaves the others' z-scores unchanged."""
    stats = make_stats(
        p1={"pts": 10.0}, p2={"pts": 20.0}, p3={"pts": 30.0}, p4={"pts": 1000.0, "games": 1}
    )

    z = z_scores(stats, min_games=5)

    assert z.loc[[1, 2, 3], "pts"].tolist() == pytest.approx([-1.22474, 0.0, 1.22474], abs=1e-4)
    assert z.loc[4].isna().all()


def test_an_empty_population_gives_an_empty_result():
    """No players: the nine columns and no rows."""
    z = z_scores(make_stats(), min_games=1)

    assert list(z.columns) == KEYS and z.empty


def test_nobody_with_enough_games_gives_all_missing_scores():
    """A player under the minimum is not scored: every column is NaN, none is zero."""
    z = z_scores(make_stats(p1={"pts": 5.0, "games": 1}), min_games=50)

    assert list(z.columns) == KEYS
    assert z.isna().all().all()
    assert math.isnan(z.loc[1, "pts"])
