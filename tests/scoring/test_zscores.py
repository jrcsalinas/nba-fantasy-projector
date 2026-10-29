"""Tests for population z-scores. Expected numbers are worked out by hand."""

import math

import pandas as pd
import pytest

from nbaproj.scoring.categories import CATEGORIES
from nbaproj.scoring.stats import STAT_COLUMNS
from nbaproj.scoring.zscores import z_scores

KEYS = [category.key for category in CATEGORIES]


def make_stats(**players: dict) -> pd.DataFrame:
    """Period stats for players named ``p<id>``; whatever a player does not name is zero."""
    rows = {}
    for name, values in players.items():
        row = {column: 0.0 for column in STAT_COLUMNS}
        row.update({"games": 10, **values})
        rows[int(name.removeprefix("p"))] = row
    stats = pd.DataFrame.from_dict(rows, orient="index", columns=STAT_COLUMNS)
    stats.index.name = "player_id"
    return stats


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


@pytest.mark.parametrize("min_games", [1, 50])
def test_an_empty_population_gives_an_empty_result(min_games):
    """No players, or nobody with enough games: the nine columns and no scores."""
    nobody = make_stats()
    one_game = make_stats(p1={"pts": 5.0, "games": 1})

    z_empty = z_scores(nobody, min_games=min_games)
    z_short = z_scores(one_game, min_games=50)

    assert list(z_empty.columns) == KEYS and z_empty.empty
    assert list(z_short.columns) == KEYS and z_short.isna().all().all()
    assert math.isnan(z_short.loc[1, "pts"])
