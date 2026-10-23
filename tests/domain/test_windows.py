from datetime import date

import pytest

from nba_projector.domain.models import GameLog, PlayerAverages, Window
from nba_projector.domain.windows import average_logs, blend_with_prior, season_for


def make_log(
    game_date: date,
    *,
    player_id: int = 1,
    game_id: str = "g1",
    season: str = "2026-27",
    pts: int = 10,
) -> GameLog:
    return GameLog(
        season=season,
        game_id=game_id,
        game_date=game_date,
        player_id=player_id,
        player_name=f"Player {player_id}",
        minutes=30.0,
        pts=pts,
        reb=5,
        ast=3,
        stl=1,
        blk=1,
        fg3m=2,
        fgm=4,
        fga=10,
        ftm=2,
        fta=2,
        tov=2,
    )


def make_avg(*, games: int, pts: float) -> PlayerAverages:
    return PlayerAverages(
        player_id=1,
        player_name="Player 1",
        games=games,
        minutes_pg=30.0,
        pts=pts,
        reb=5.0,
        ast=3.0,
        stl=1.0,
        blk=1.0,
        fg3m=2.0,
        fgm=4.0,
        fga=10.0,
        ftm=2.0,
        fta=2.0,
        tov=2.0,
    )


def test_season_for_october_returns_new_season() -> None:
    assert season_for(date(2026, 10, 16)) == "2026-27"


def test_season_for_march_returns_season_started_previous_year() -> None:
    assert season_for(date(2027, 3, 1)) == "2026-27"


def test_average_logs_l15_includes_day_minus_14_and_excludes_day_minus_15() -> None:
    logs = [
        make_log(date(2026, 11, 16), game_id="in"),
        make_log(date(2026, 11, 15), game_id="out"),
    ]

    result = average_logs(logs, Window.LAST_15, date(2026, 11, 30))

    assert result[1].games == 1


def test_average_logs_season_ignores_prior_season_logs() -> None:
    logs = [
        make_log(date(2026, 11, 1), game_id="new"),
        make_log(date(2026, 3, 1), game_id="old", season="2025-26", pts=40),
    ]

    result = average_logs(logs, Window.SEASON, date(2026, 11, 30))

    assert result[1].games == 1
    assert result[1].pts == 10.0


def test_average_logs_traded_player_is_one_row() -> None:
    logs = [
        make_log(date(2026, 11, 1), game_id="a"),
        make_log(date(2026, 11, 3), game_id="b"),
    ]

    result = average_logs(logs, Window.SEASON, date(2026, 11, 30))

    assert len(result) == 1
    assert result[1].games == 2


def test_blend_with_prior_shrinks_prior_weight_as_games_grow() -> None:
    current = make_avg(games=10, pts=20.0)
    prior = make_avg(games=60, pts=10.0)

    blended = blend_with_prior(current, prior)

    assert blended is not None
    assert blended.pts == pytest.approx(15.0)
    assert blended.games == 20

    full = make_avg(games=25, pts=20.0)
    assert blend_with_prior(full, prior) == full


def test_blend_with_prior_handles_missing_sides() -> None:
    prior = make_avg(games=60, pts=10.0)
    current = make_avg(games=10, pts=20.0)

    from_prior = blend_with_prior(None, prior)

    assert from_prior is not None
    assert from_prior.pts == 10.0
    assert from_prior.games == 20
    assert blend_with_prior(current, None) == current
    assert blend_with_prior(None, None) is None
