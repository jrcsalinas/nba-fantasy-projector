"""Tests for trends. Expected numbers are worked out by hand."""

import ast
from pathlib import Path

import pytest
from pandas.testing import assert_frame_equal

import nbaproj.scoring
from nbaproj.scoring.trends import (
    CategoryTrend,
    build_reason,
    label_thresholds,
    score_trends,
    trend_label,
)
from tests.scoring.conftest import make_stats


def _trend(season, window, player_id, days=14):
    """The trend of one player, scored against the whole of ``season`` and ``window``."""
    return score_trends(season, window, days, [player_id])[0]


def _by_key(trend):
    return {category.key: category for category in trend.categories}


def _crossing_points():
    """Season points 10, 20, 30; window points 30, 20, 10: players 1 and 3 swap places.

    Season z: -1.2247, 0, +1.2247 (population standard deviation sqrt(200 / 3) = 8.165).
    Window z: +1.2247, 0, -1.2247. So player 1's points trend is +2.4495, player 3's -2.4495.
    """
    season = make_stats(
        p1={"pts": 10.0, "games": 20}, p2={"pts": 20.0, "games": 20}, p3={"pts": 30.0, "games": 20}
    )
    window = make_stats(
        p1={"pts": 30.0, "games": 5}, p2={"pts": 20.0, "games": 5}, p3={"pts": 10.0, "games": 5}
    )
    return season, window


def test_a_trend_is_the_window_z_score_minus_the_season_z_score():
    """Player 1 rose by 2.4495 standard deviations in points; player 2 did not move."""
    season, window = _crossing_points()

    rising = _by_key(_trend(season, window, 1))["pts"]
    flat = _by_key(_trend(season, window, 2))["pts"]
    falling = _by_key(_trend(season, window, 3))["pts"]

    assert rising.z_change == pytest.approx(2.44949, abs=1e-4)
    assert flat.z_change == pytest.approx(0.0, abs=1e-9)
    assert falling.z_change == pytest.approx(-2.44949, abs=1e-4)
    keys = [category.key for category in _trend(season, window, 1).categories]
    assert keys == ["pts", "reb", "ast", "stl", "blk", "tpm", "tov", "fg", "ft"]


def test_a_trend_also_reports_the_plain_per_game_change():
    """Points 10 to 30 is +20.0 a game; FG 50% to 55% is +0.05 (shown as a percentage)."""
    season = make_stats(
        p1={"pts": 10.0, "fg_pct": 0.50, "fg_impact": 0.0, "games": 20},
        p2={"pts": 20.0, "fg_pct": 0.45, "fg_impact": 1.0, "games": 20},
    )
    window = make_stats(
        p1={"pts": 30.0, "fg_pct": 0.55, "fg_impact": 1.0, "games": 5},
        p2={"pts": 20.0, "fg_pct": 0.45, "fg_impact": 1.0, "games": 5},
    )

    categories = _by_key(_trend(season, window, 1))

    assert categories["pts"].change == pytest.approx(20.0)
    assert categories["fg"].change == pytest.approx(0.05)


def test_minutes_delta_is_window_minus_season_per_game():
    """30.0 minutes a game over the season, 36.0 over the window: +6.0."""
    season, window = _crossing_points()
    season.loc[1, "minutes"] = 30.0
    window.loc[1, "minutes"] = 36.0

    assert _trend(season, window, 1).minutes_change == pytest.approx(6.0)


def test_usage_delta_is_unknown_when_either_period_lacks_usage():
    """0.20 to 0.25 is +0.05; if either side is unknown the answer is unknown, not zero."""
    season, window = _crossing_points()
    season["usage"], window["usage"] = 0.20, 0.25
    season.loc[2, "usage"] = float("nan")
    window.loc[3, "usage"] = float("nan")

    assert _trend(season, window, 1).usage_change == pytest.approx(0.05)
    assert _trend(season, window, 2).usage_change is None
    assert _trend(season, window, 3).usage_change is None


@pytest.mark.parametrize(
    ("season_games", "window_games", "reason"),
    [
        (20, 1, "only 1 game in the last 7 days"),
        (20, None, "no games in the last 7 days"),
        (3, 5, "only 3 games this season"),
        (None, 5, "no games this season"),
    ],
)
def test_a_player_with_too_few_games_has_no_trend_and_says_why(season_games, window_games, reason):
    """Under 5 season games or 2 window games there is nothing to compare: say so."""
    season, window = _crossing_points()
    if season_games is None:
        season = season.drop(index=1)
    else:
        season.loc[1, "games"] = season_games
    if window_games is None:
        window = window.drop(index=1)
    else:
        window.loc[1, "games"] = window_games

    trend = _trend(season, window, 1, days=7)

    assert trend.has_trend is False and trend.reason == reason
    assert trend.categories == () and trend.overall is None and trend.label is None
    assert trend.minutes_change is None and trend.usage_change is None


@pytest.mark.parametrize(("window_games", "low_sample"), [(2, True), (3, False)])
def test_fewer_than_three_window_games_is_a_low_sample(window_games, low_sample):
    """A trend from 2 games is given, but flagged so it is not over-read."""
    season, window = _crossing_points()
    window.loc[1, "games"] = window_games

    trend = _trend(season, window, 1)

    assert trend.has_trend is True and trend.low_sample is low_sample
    assert trend.window_games == window_games


def test_overall_score_is_the_sum_of_the_category_trends():
    """Points and rebounds both swap places: 2.4495 + 2.4495 = 4.899, and the mirror is -4.899."""
    season, window = _crossing_points()
    season["reb"] = [10.0, 20.0, 30.0]
    window["reb"] = [30.0, 20.0, 10.0]

    rising = _trend(season, window, 1)
    flat = _trend(season, window, 2)
    falling = _trend(season, window, 3)

    assert rising.overall == pytest.approx(4.89898, abs=1e-4)
    assert flat.overall == pytest.approx(0.0, abs=1e-9)
    assert falling.overall == pytest.approx(-4.89898, abs=1e-4)
    assert rising.overall == pytest.approx(sum(c.z_change for c in rising.categories))


def test_label_thresholds_are_the_top_and_bottom_tenth_of_the_population():
    """Overall scores 1 to 20: the 90th percentile is 18.1 and the 10th is 2.9."""
    thresholds = label_thresholds([float(n) for n in range(1, 21)])

    assert thresholds == pytest.approx((18.1, 2.9))
    labels = [trend_label(x, thresholds) for x in (19.0, 18.2, 18.0, 3.0, 2.8)]
    assert labels == ["Hot", "Hot", "Steady", "Steady", "Cold"]


def test_a_population_too_small_to_say_who_is_unusual_gets_no_label():
    """Nineteen players is too few: no thresholds, so no label (the score itself is still given)."""
    assert label_thresholds([float(n) for n in range(1, 20)]) is None
    assert trend_label(5.0, None) is None
    season, window = _crossing_points()
    trend = _trend(season, window, 1)
    assert trend.has_trend is True and trend.overall is not None and trend.label is None


def test_labels_follow_the_leagues_spread_not_a_fixed_number():
    """Twenty players, equal all season; in the window player n scores n points a game.

    The overall trend then rises with n, so the top tenth (players 19 and 20) are Hot and the
    bottom tenth (players 1 and 2) are Cold, whatever the size of the numbers.
    """
    players = range(1, 21)
    season = make_stats(**{f"p{n}": {"pts": 10.0, "games": 20} for n in players})
    window = make_stats(**{f"p{n}": {"pts": float(n), "games": 5} for n in players})

    trends = score_trends(season, window, 14, list(players))
    labels = {t.player_id: t.label for t in trends}

    assert [n for n in players if labels[n] == "Hot"] == [19, 20]
    assert [n for n in players if labels[n] == "Cold"] == [1, 2]
    assert [n for n in players if labels[n] == "Steady"] == list(range(3, 19))


def _labels(trends):
    return {t.player_id: t.label for t in trends}


def test_a_league_where_nobody_stands_out_labels_nobody():
    """Twenty-five identical players share every threshold, so none is beyond it: all Steady."""
    same = make_stats(**{f"p{n}": {"pts": 10.0, "games": 20} for n in range(1, 26)})

    labels = _labels(score_trends(same, same, 14, list(range(1, 26))))

    assert set(labels.values()) == {"Steady"}


def test_a_threshold_shared_by_many_players_does_not_label_them_all():
    """22 players hold at 10 points and three rise to 20, 30 and 40 (season z is zero for all).

    The 10th percentile sits on the 22 tied players, so nobody is below it: no Cold. The
    90th percentile falls between the tied group and the 20-point player: the three risers
    are Hot and everyone else Steady, instead of 22 of 25 players being called Cold.
    """
    season = make_stats(**{f"p{n}": {"pts": 10.0, "games": 20} for n in range(1, 26)})
    window = make_stats(
        **{f"p{n}": {"pts": 10.0 + 10.0 * max(0, n - 22), "games": 5} for n in range(1, 26)}
    )

    labels = _labels(score_trends(season, window, 14, list(range(1, 26))))

    assert [n for n, label in labels.items() if label == "Hot"] == [23, 24, 25]
    assert "Cold" not in labels.values()


def test_players_scored_in_only_one_period_are_left_out_of_the_label_population():
    """Five extra players have window games but no season at all, so they have no overall.

    Counting them as zeros would pull both thresholds toward zero and label a third player
    Hot (players 18, 19 and 20) and a third Cold (1, 2 and 3). They must not count: the top
    tenth of the twenty who have an overall is players 19 and 20, the bottom tenth 1 and 2.
    """
    base = range(1, 21)
    season = make_stats(**{f"p{n}": {"pts": 10.0, "games": 20} for n in base})
    window = make_stats(
        **{f"p{n}": {"pts": float(n), "games": 5} for n in base},
        **{f"p{n}": {"pts": 10.5, "games": 5} for n in range(21, 26)},
    )

    labels = _labels(score_trends(season, window, 14, list(base)))

    assert [n for n in base if labels[n] == "Hot"] == [19, 20]
    assert [n for n in base if labels[n] == "Cold"] == [1, 2]


def _moves():
    return [
        CategoryTrend("pts", "PTS", z_change=2.4, change=20.0),
        CategoryTrend("reb", "REB", z_change=0.9, change=1.4),
        CategoryTrend("tov", "TO", z_change=-1.5, change=1.0),
        CategoryTrend("fg", "FG%", z_change=0.1, change=0.04),
    ]


def test_the_reason_names_the_two_biggest_movers_and_a_big_minutes_change():
    """By size of z change: PTS (2.4) and TO (-1.5, turnovers up by 1.0), then minutes +6.0."""
    reason = build_reason(_moves(), minutes_change=6.0, window_days=14)

    assert reason == "PTS +20.0, 1.0 more TO over the last 14 days; minutes +6.0"


@pytest.mark.parametrize(
    ("moves", "minutes", "expected"),
    [
        (_moves()[1:2] + _moves()[3:], 2.9, "REB +1.4 over the last 14 days"),
        (
            [CategoryTrend("fg", "FG%", z_change=0.8, change=0.04)],
            0.0,
            "FG% +4.0 pts over the last 14 days",
        ),
        (_moves()[3:], 0.0, "No notable change over the last 14 days"),
        (_moves()[3:], -4.5, "Minutes -4.5 over the last 14 days"),
        (
            [CategoryTrend("tov", "TO", z_change=1.7, change=-1.1)],
            0.0,
            "1.1 fewer TO over the last 14 days",
        ),
    ],
    ids=[
        "small minutes left out",
        "percentage in points",
        "nothing notable",
        "minutes only",
        "fewer turnovers",
    ],
)
def test_the_reason_leaves_out_what_is_small(moves, minutes, expected):
    """Moves under 0.25 standard deviations and minutes changes under 3.0 are not worth a word."""
    assert build_reason(moves, minutes_change=minutes, window_days=14) == expected


def test_scoring_is_pure_same_input_same_output_and_the_input_untouched():
    """Calling twice gives the same answer and leaves both frames exactly as they were."""
    season, window = _crossing_points()
    before_season, before_window = season.copy(deep=True), window.copy(deep=True)

    first = score_trends(season, window, 14, [1, 2, 3, 99])
    second = score_trends(season, window, 14, [1, 2, 3, 99])

    assert first == second
    assert_frame_equal(season, before_season)
    assert_frame_equal(window, before_window)
    assert [trend.player_id for trend in first] == [1, 2, 3, 99]
    assert first[3].has_trend is False and first[3].reason == "no games this season"


FORBIDDEN_IMPORTS = (
    "sqlalchemy",
    "requests",
    "nba_api",
    "nbaproj.db",
    "nbaproj.ingestion",
    "nbaproj.web",
    "nbaproj.roster",
    "os",
    "socket",
    "urllib",
    "httpx",
    "time",
)
CLOCK_NAMES = {"datetime", "date", "time"}
CLOCK_CALLS = {"now", "today", "utcnow", "time", "monotonic", "perf_counter"}


def test_scoring_imports_no_database_or_network_code_and_reads_no_clock():
    """Pure means pure: the package may not import I/O libraries or ask what time it is."""
    sources = sorted(Path(nbaproj.scoring.__file__).parent.glob("*.py"))
    assert len(sources) >= 4

    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert not name.startswith(FORBIDDEN_IMPORTS), (source.name, name)
            if isinstance(node, ast.Attribute) and node.attr in CLOCK_CALLS:
                is_clock = isinstance(node.value, ast.Name) and node.value.id in CLOCK_NAMES
                assert not is_clock, (source.name, node.attr)
