"""The trends page against a season big enough to have Hot, Cold and low-sample players.

Twenty-two players, twenty game days. Players 101 to 121 play every day; player 101 + k scores
20 - k points on days 1 to 13 and 10 + k on days 14 to 20, so the higher k, the more he has
risen (k = 20 goes from 0 to 30 a game) and k = 0 has fallen from 20 to 10. Player 122 plays
days 1 to 14 and day 20 only, so he has just two games in the last 7 days. Usage is 20 early and
24 late: for the 20-game players the season average is (13 x 20 + 7 x 24) / 20 = 21.4 against 24.0
lately (+2.6 points); for player 122 it is (13 x 20 + 2 x 24) / 15 = 20.53 (+3.5 points).
"""

import re
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from nbaproj.db.models import Player, RosterEntry

HOT, COLD, LOW_SAMPLE = 121, 101, 122


@pytest.fixture(name="big_season")
def big_season_fixture(seed, engine):
    """The season above, with players 121, 101 and 122 on josh's roster."""
    seed.session.add_all([Player(player_id=n, full_name=f"Player {n}") for n in range(101, 123)])
    seed.session.commit()

    def lines(day: int) -> dict[int, dict]:
        usage = 20.0 if day <= 13 else 24.0
        day_lines = {}
        for player in range(101, 122):
            k = player - 101
            pts = 20 - k if day <= 13 else 10 + k
            day_lines[player] = {"pts": pts, "usage_pct": usage}
        if day <= 14 or day == 20:
            day_lines[LOW_SAMPLE] = {"pts": 10, "usage_pct": 20.0 if day <= 13 else 24.0}
        return day_lines

    seed.daily(range(1, 21), lines)
    with Session(engine) as session:
        for player in (HOT, COLD, LOW_SAMPLE):
            session.add(
                RosterEntry(
                    owner="josh",
                    player_id=player,
                    updated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                    source="manual",
                )
            )
        session.commit()


def _section(html: str, name: str) -> str:
    """The card of one player, so assertions cannot match text from another card."""
    sections = re.findall(r'<section class="trend-card">(.*?)</section>', html, re.S)
    (found,) = [section for section in sections if name in section]
    return found


@pytest.mark.usefixtures("big_season")
def test_the_hot_player_has_a_hot_badge_and_the_cold_player_a_cold_one(make_client):
    """The top and bottom of the league's trends are labelled, with the matching style."""
    html = make_client().get("/trends?window=7").text

    hot, cold = _section(html, f"Player {HOT}"), _section(html, f"Player {COLD}")
    assert re.search(r'class="badge badge-up">\s*Hot \+', hot)
    assert re.search(r'class="badge badge-down">\s*Cold -', cold)
    assert "PTS +" in hot and "PTS -" in cold


@pytest.mark.usefixtures("big_season")
def test_every_card_shows_minutes_and_usage_in_realistic_percentage_points(make_client):
    """Minutes did not change; usage rose from 21.4 to 24.0 (+2.6 points), and for player 122
    from 20.53 to 24.0 (+3.5 points). The stored usage is a percentage, so these are points."""
    html = make_client().get("/trends?window=7").text

    for player, usage in ((HOT, "+2.6"), (COLD, "+2.6"), (LOW_SAMPLE, "+3.5")):
        card = _section(html, f"Player {player}")
        assert "Minutes +0.0" in card and f"Usage {usage} pts" in card


@pytest.mark.usefixtures("big_season")
def test_a_two_game_window_is_flagged_as_a_low_sample(make_client):
    """Player 122 has 2 games in the last 7 days: the card says so; the others do not."""
    html = make_client().get("/trends?window=7").text

    low = _section(html, f"Player {LOW_SAMPLE}")
    assert "2 games in the last 7 days" in low and "Low sample: read with care" in low
    assert "Low sample" not in _section(html, f"Player {HOT}")


@pytest.mark.usefixtures("big_season")
def test_the_page_explains_the_z_numbers_and_spaces_each_value_from_its_z(make_client):
    """A z with no explanation is noise, and "+9.0z +0.4" is how a screen reader would say it."""
    html = make_client().get("/trends?window=7").text

    assert "standard deviations" in html
    card = _section(html, f"Player {HOT}")
    assert re.search(r"</span>\s+<span class=\"cat-z muted\">z [+-]\d", card)
