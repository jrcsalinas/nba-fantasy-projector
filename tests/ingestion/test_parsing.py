"""Tests for the payload parsers."""

from datetime import datetime, timezone

import pytest

from nbaproj.ingestion.parsing import (
    TeamInfo,
    fantasy_game_type,
    game_type_from_id,
    parse_boxscore,
    parse_minutes,
    parse_scoreboard,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PT33M15.00S", 33.25),
        ("PT00M00.00S", 0.0),
        ("PT240M00.00S", 240.0),
        ("PT05M30S", 5.5),
    ],
)
def test_parse_minutes_converts_iso_durations(raw, expected):
    """ISO durations become decimal minutes."""
    assert parse_minutes(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "33:15", "PT33M", "abc", "PT-1M00.00S"])
def test_parse_minutes_rejects_invalid_values(raw):
    """Anything that is not an ISO duration raises ValueError."""
    with pytest.raises(ValueError):
        parse_minutes(raw)


@pytest.mark.parametrize(
    ("game_id", "expected"),
    [
        ("0012500003", "preseason"),
        ("0022500001", "regular_season"),
        ("0042500101", "playoffs"),
        ("0052500001", "play_in"),
    ],
)
def test_game_type_from_id_reads_the_prefix(game_id, expected):
    """The first three digits of the game id name the game type."""
    assert game_type_from_id(game_id) == expected


def test_game_type_from_id_rejects_unknown_prefix():
    """An unrecognised prefix raises ValueError."""
    with pytest.raises(ValueError):
        game_type_from_id("0032500001")


def test_parse_scoreboard_returns_games_with_status_and_teams(scoreboard_payload):
    """Each scoreboard game keeps its id, status, tip-off time and both teams."""
    games = parse_scoreboard(scoreboard_payload)

    assert [g.game_id for g in games] == [
        "0022500001",
        "0022500002",
        "0012500003",
        "0042500101",
        "0022500004",
    ]
    assert [g.status for g in games] == [
        "final",
        "live",
        "final",
        "final",
        "scheduled",
    ]
    first = games[0]
    assert first.tip_off_utc == datetime(2025, 10, 28, 23, 30, tzinfo=timezone.utc)
    assert first.home == TeamInfo(1610612747, "LAL", "Lakers", "Los Angeles")
    assert first.away == TeamInfo(1610612744, "GSW", "Warriors", "Golden State")


def test_parse_scoreboard_rejects_unknown_status(scoreboard_payload):
    """A status code outside 1-3 is an error, not a silent guess."""
    scoreboard_payload["scoreboard"]["games"][0]["gameStatus"] = 9
    with pytest.raises(ValueError):
        parse_scoreboard(scoreboard_payload)


def test_parse_boxscore_returns_only_players_who_played(boxscore_payload):
    """Players with played == "0" are left out; a zero-minute player who played stays."""
    box = parse_boxscore(boxscore_payload)

    assert box.game_id == "0022500001"
    assert box.status == "final"
    assert box.home.tricode == "LAL"
    assert box.away.tricode == "GSW"
    assert [line.player_id for line in box.lines] == [1001, 1002, 2001, 2002, 2003]

    alex = box.lines[0]
    assert alex.name == "Alex Home"
    assert alex.team_id == 1610612747
    assert alex.minutes == pytest.approx(36.0)
    assert (alex.pts, alex.reb, alex.ast, alex.stl, alex.blk) == (28, 7, 6, 2, 1)
    assert (alex.tpm, alex.fgm, alex.fga, alex.ftm, alex.fta, alex.tov) == (
        3,
        10,
        20,
        5,
        5,
        4,
    )
    assert box.lines[2].team_id == 1610612744


def test_parse_boxscore_derives_usage_with_team_totals(boxscore_payload):
    """Usage uses the team totals; a zero-minute player has none."""
    box = parse_boxscore(boxscore_payload)
    by_id = {line.player_id: line for line in box.lines}

    assert by_id[1001].usage_pct == pytest.approx(30.9145, abs=1e-3)
    assert by_id[2003].usage_pct is None


def test_parse_boxscore_handles_overtime_minutes(overtime_payload):
    """Usage scales by the team's actual minutes (265), not a fixed 240."""
    box = parse_boxscore(overtime_payload)

    expected = 100 * (26 + 0.44 * 8 + 5) * (265 / 5) / (44 * (95 + 0.44 * 28 + 13))
    assert box.lines[0].usage_pct == pytest.approx(expected)


@pytest.mark.parametrize(
    ("game_id", "expected"),
    [
        ("0022500001", "regular_season"),
        ("0042500101", "playoffs"),
        ("0052500001", "play_in"),
        ("0012500003", None),
        ("0032500001", None),
        ("0062500001", None),
        ("0092500001", None),
    ],
)
def test_fantasy_game_type_keeps_only_countable_prefixes(game_id, expected):
    """Regular season, playoffs and play-in count; everything else is None."""
    assert fantasy_game_type(game_id) == expected
