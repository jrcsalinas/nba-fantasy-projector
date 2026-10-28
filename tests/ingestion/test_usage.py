"""Tests for the usage rate calculation."""

import pytest

from nbaproj.ingestion.usage import Totals, usage_pct

TEAM = Totals(minutes=240.0, fga=90, fta=25, tov=12)


def test_usage_pct_matches_hand_calculation():
    """100 * ((20 + 0.44*5 + 4) * (240/5)) / (36 * (90 + 0.44*25 + 12))."""
    player = Totals(minutes=36.0, fga=20, fta=5, tov=4)
    assert usage_pct(player, TEAM) == pytest.approx(30.9145, abs=1e-3)


def test_usage_pct_is_none_for_zero_minutes():
    """A player with no minutes has no usage rate."""
    player = Totals(minutes=0.0, fga=0, fta=0, tov=0)
    assert usage_pct(player, TEAM) is None


def test_usage_pct_is_none_when_team_has_no_possessions():
    """A zero team denominator would divide by zero."""
    player = Totals(minutes=10.0, fga=0, fta=0, tov=0)
    assert usage_pct(player, Totals(minutes=240.0, fga=0, fta=0, tov=0)) is None
