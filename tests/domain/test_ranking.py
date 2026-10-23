import math
from dataclasses import replace

import pytest

from nba_projector.domain.models import Category, PlayerAverages
from nba_projector.domain.ranking import rank_players

Z = 1.2247


def make_avg(player_id: int, **overrides: float) -> PlayerAverages:
    values: dict[str, float] = {
        "pts": 20.0,
        "reb": 5.0,
        "ast": 5.0,
        "stl": 1.0,
        "blk": 1.0,
        "fg3m": 2.0,
        "fgm": 5.0,
        "fga": 10.0,
        "ftm": 4.0,
        "fta": 5.0,
        "tov": 2.0,
    }
    values.update(overrides)
    return PlayerAverages(
        player_id=player_id,
        player_name=f"Player {player_id}",
        games=20,
        minutes_pg=30.0,
        **values,  # type: ignore[arg-type]
    )


def rank(pool: list[PlayerAverages], min_games: int = 1, min_minutes: float = 0.0):
    return {p.player_id: p for p in rank_players(pool, min_games, min_minutes)}


def test_rank_players_pts_z_scores_use_population_std() -> None:
    pool = [make_avg(1, pts=10), make_avg(2, pts=20), make_avg(3, pts=30)]

    result = rank(pool)

    assert result[1].z[Category.PTS] == pytest.approx(-Z, abs=1e-3)
    assert result[2].z[Category.PTS] == pytest.approx(0.0, abs=1e-3)
    assert result[3].z[Category.PTS] == pytest.approx(Z, abs=1e-3)
    assert result[3].total == pytest.approx(result[3].z[Category.PTS])
    assert [result[i].ranks["overall"] for i in (1, 2, 3)] == [3, 2, 1]


def test_rank_players_zero_std_category_gives_zero_z() -> None:
    pool = [make_avg(1, pts=10), make_avg(2, pts=20), make_avg(3, pts=30)]

    result = rank(pool)

    for player in result.values():
        for category in Category:
            assert not math.isnan(player.z[category])
            if category is not Category.PTS:
                assert player.z[category] == 0.0


def test_rank_players_tov_is_reversed() -> None:
    pool = [make_avg(1, tov=1), make_avg(2, tov=2), make_avg(3, tov=3)]

    result = rank(pool)

    assert result[1].z[Category.TOV] == pytest.approx(Z, abs=1e-3)
    assert result[2].z[Category.TOV] == pytest.approx(0.0, abs=1e-3)
    assert result[3].z[Category.TOV] == pytest.approx(-Z, abs=1e-3)


def test_rank_players_fg_pct_weights_by_attempts() -> None:
    pool = [
        make_avg(1, fgm=8, fga=10),
        make_avg(2, fgm=2, fga=10),
        make_avg(3, fgm=5, fga=10),
    ]

    result = rank(pool)

    assert result[1].z[Category.FG_PCT] == pytest.approx(Z, abs=1e-3)
    assert result[2].z[Category.FG_PCT] == pytest.approx(-Z, abs=1e-3)
    assert result[3].z[Category.FG_PCT] == pytest.approx(0.0, abs=1e-3)


def test_rank_players_zero_attempts_gives_zero_impact() -> None:
    pool = [
        make_avg(1, fgm=0, fga=0, ftm=0, fta=0),
        make_avg(2),
        make_avg(3, fgm=8, fga=10),
    ]

    result = rank(pool)

    for category in (Category.FG_PCT, Category.FT_PCT):
        assert math.isfinite(result[1].z[category])


def test_rank_players_filters_below_thresholds() -> None:
    low_games = replace(make_avg(1), games=2)
    low_minutes = replace(make_avg(2), minutes_pg=5.0)
    pool = [low_games, low_minutes, make_avg(3)]

    result = rank(pool, min_games=5, min_minutes=12.0)

    assert set(result) == {3}
    assert rank_players([], 5, 12.0) == []


def test_rank_players_category_rank_breaks_ties_by_overall_rank() -> None:
    # Players 1 and 2 tie on PTS; player 2 is better overall thanks to REB.
    pool = [
        make_avg(1, pts=20, reb=4),
        make_avg(2, pts=20, reb=8),
        make_avg(3, pts=10, reb=6),
    ]

    result = rank(pool)

    assert result[2].ranks["overall"] < result[1].ranks["overall"]
    assert result[2].ranks["pts"] < result[1].ranks["pts"]


def test_rank_players_attaches_the_averages_used_for_ranking() -> None:
    pool = [make_avg(1, pts=10), make_avg(2, pts=20), make_avg(3, pts=30)]

    result = rank(pool)

    assert result[2].averages == pool[1]
    assert result[2].games_played is None
