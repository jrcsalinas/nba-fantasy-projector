"""Tests for the text the trends page shows, built from hand-made trends."""

import pytest

from nbaproj.scoring.categories import CATEGORIES
from nbaproj.scoring.trends import CategoryTrend, PlayerTrend
from nbaproj.services.trends import TrendRow, TrendsView
from nbaproj.web.trend_cards import build_cards


def _trend(label="Steady", overall=1.1, z_changes=None, changes=None, **overrides) -> PlayerTrend:
    """A trend for a player with nine categories; unnamed ones did not move."""
    z_changes, changes = z_changes or {}, changes or {}
    categories = tuple(
        CategoryTrend(c.key, c.label, z_changes.get(c.key, 0.0), changes.get(c.key, 0.0))
        for c in CATEGORIES
    )
    values = {
        "player_id": 1,
        "season_games": 60,
        "window_games": 7,
        "has_trend": True,
        "reason": "No notable change over the last 14 days",
        "categories": categories,
        "minutes_change": 0.0,
        "usage_change": 0.0,
        "overall": overall,
        "label": label,
    }
    return PlayerTrend(**{**values, **overrides})


def _card(trend: PlayerTrend, window_days: int = 14):
    view = TrendsView(window_days, "2025-26", None, (TrendRow(1, "Ada One", trend),))
    return build_cards(view)[0]


@pytest.mark.parametrize(
    ("label", "tone"), [("Hot", "up"), ("Cold", "down"), ("Steady", "flat"), (None, "flat")]
)
def test_the_label_and_its_tone(label, tone):
    """Hot reads good, Cold reads bad; with no label (too few players) there is just the score."""
    card = _card(_trend(label=label, overall=4.2))

    assert card.label == (label or "") and card.tone == tone
    assert card.overall == "+4.2" and card.has_trend is True


def test_a_score_that_rounds_to_zero_never_reads_minus_zero():
    """-0.04 rounds to -0.0, which is noise: it is shown as +0.0 everywhere."""
    trend = _trend(
        overall=-0.04,
        z_changes={"pts": -0.03},
        changes={"pts": -0.04, "fg": -0.0004},
        minutes_change=-0.02,
        usage_change=-0.04,
    )

    card = _card(trend)
    cells = {cell.label: cell for cell in card.cells}

    assert card.overall == "+0.0" and card.minutes == "Minutes +0.0"
    assert card.usage == "Usage +0.0 pts"
    assert cells["PTS"].change == "+0.0" and cells["PTS"].z_change == "+0.0"
    assert cells["FG%"].change == "+0.0 pts"
    assert "-0.0" not in str([card])


def test_category_cells_show_plain_changes_percentages_in_points_and_unknowns():
    """Counting stats as they are, FG% and FT% in percentage points, an unknown change as n/a."""
    trend = _trend(
        z_changes={"pts": 2.4, "tov": -1.0, "fg": 0.1},
        changes={"pts": 20.0, "tov": 1.0, "fg": 0.04, "ft": None},
    )

    cells = {cell.label: cell for cell in _card(trend).cells}

    assert [cell.label for cell in _card(trend).cells] == [c.label for c in CATEGORIES]
    assert (cells["PTS"].change, cells["PTS"].z_change) == ("+20.0", "+2.4")
    assert cells["FG%"].change == "+4.0 pts"
    assert cells["FT%"].change == "n/a"
    assert (cells["PTS"].tone, cells["TO"].tone, cells["FG%"].tone) == ("up", "down", "flat")


def test_minutes_and_usage_changes_in_plain_words():
    """Minutes per game, and usage in percentage points (the stored usage is a percentage)."""
    card = _card(_trend(minutes_change=-6.0, usage_change=2.5))
    unknown = _card(_trend(usage_change=None))

    assert card.minutes == "Minutes -6.0" and card.usage == "Usage +2.5 pts"
    assert unknown.usage == "Usage unknown"


def test_games_and_the_low_sample_flag():
    """How many games the numbers rest on, in the singular too; low sample is passed through."""
    one = _card(_trend(window_games=1, season_games=1, low_sample=True), window_days=7)
    many = _card(_trend(window_games=7, season_games=60))

    assert one.games == "1 game in the last 7 days · 1 this season" and one.low_sample is True
    assert many.games == "7 games in the last 14 days · 60 this season"
    assert many.low_sample is False


def test_a_player_without_a_trend_has_only_a_name_and_the_reason():
    """The reason is shown as it is; there are no cells, score or label."""
    trend = PlayerTrend(1, 0, 0, has_trend=False, reason="no games this season")

    card = _card(trend)

    assert (card.name, card.has_trend, card.reason) == ("Ada One", False, "no games this season")
    assert card.cells == () and card.label == "" and card.overall == ""
