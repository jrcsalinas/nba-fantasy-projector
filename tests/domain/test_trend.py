from nba_projector.domain.models import Trend
from nba_projector.domain.ranking import compute_trends


def test_compute_trends_without_previous_returns_dash_for_all() -> None:
    result = compute_trends({1: 1, 2: 2}, None)

    assert result == {1: Trend(None, False), 2: Trend(None, False)}


def test_compute_trends_player_moving_up() -> None:
    assert compute_trends({1: 2}, {1: 5})[1] == Trend(3, False)


def test_compute_trends_player_moving_down() -> None:
    assert compute_trends({1: 5}, {1: 2})[1] == Trend(-3, False)


def test_compute_trends_unchanged_rank() -> None:
    assert compute_trends({1: 4}, {1: 4})[1] == Trend(0, False)


def test_compute_trends_player_absent_from_previous_is_new() -> None:
    assert compute_trends({1: 1, 2: 2}, {1: 1})[2] == Trend(None, True)
