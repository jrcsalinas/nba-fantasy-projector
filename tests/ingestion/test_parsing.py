"""Tests for the payload parsers."""

from datetime import datetime, timezone

import pytest

from nbaproj.ingestion.parsing import (
    PayloadError,
    TeamInfo,
    fantasy_game_type,
    game_type_from_id,
    parse_boxscore,
    parse_minutes,
    parse_scoreboard,
    parse_utc,
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


def test_parse_boxscore_reports_a_null_field_as_value_error(boxscore_payload):
    """A null where a value is read becomes a ValueError that says what was malformed."""
    boxscore_payload["game"]["homeTeam"]["players"][0]["statistics"]["minutes"] = None

    with pytest.raises(ValueError, match="malformed box score"):
        parse_boxscore(boxscore_payload)


def test_parse_scoreboard_reports_a_missing_team_as_value_error(scoreboard_payload):
    """A game without a team object is a ValueError, not a TypeError."""
    scoreboard_payload["scoreboard"]["games"][0]["homeTeam"] = None

    with pytest.raises(ValueError, match="malformed scoreboard"):
        parse_scoreboard(scoreboard_payload)


@pytest.mark.parametrize("played", [1, True, "1"])
def test_parse_boxscore_accepts_played_as_int_or_bool(boxscore_payload, played):
    """The "played" flag counts however it is typed, so a format change cannot drop everyone."""
    for side in ("homeTeam", "awayTeam"):
        for player in boxscore_payload["game"][side]["players"]:
            if player["played"] == "1":
                player["played"] = played

    box = parse_boxscore(boxscore_payload)

    assert [line.player_id for line in box.lines] == [1001, 1002, 2001, 2002, 2003]


def test_parse_boxscore_of_none_raises_payload_error():
    """A payload that is not even a dict is a data problem like any other."""
    with pytest.raises(PayloadError):
        parse_boxscore(None)


def test_parse_boxscore_with_a_missing_key_raises_payload_error(boxscore_payload):
    """A missing key is reported as a payload error that says what was malformed."""
    del boxscore_payload["game"]["gameId"]

    with pytest.raises(PayloadError, match="malformed box score"):
        parse_boxscore(boxscore_payload)


def test_every_parse_problem_is_a_payload_error(scoreboard_payload):
    """Bad minutes, an unknown game type, and an unknown status are all payload errors."""
    scoreboard_payload["scoreboard"]["games"][0]["gameStatus"] = 9

    with pytest.raises(PayloadError):
        parse_minutes("abc")
    with pytest.raises(PayloadError):
        game_type_from_id("0032500001")
    with pytest.raises(PayloadError):
        parse_scoreboard(scoreboard_payload)


def test_parse_utc_rejects_a_timestamp_without_a_timezone():
    """A timestamp must say it is UTC (Z or +00:00); a naive one would be stored wrongly."""
    expected = datetime(2025, 10, 22, 23, 30, tzinfo=timezone.utc)

    assert parse_utc("2025-10-22T23:30:00Z") == expected
    assert parse_utc("2025-10-22T23:30:00+00:00") == expected
    with pytest.raises(PayloadError, match="timezone"):
        parse_utc("2025-10-22T23:30:00")


@pytest.mark.parametrize(
    ("field", "value"),
    [("teamTricode", "ABCD"), ("teamName", "x" * 65), ("teamCity", "x" * 65)],
)
def test_parse_scoreboard_rejects_team_values_longer_than_the_columns(
    scoreboard_payload, field, value
):
    """Postgres refuses over-long values (SQLite does not), so the parser rejects them."""
    scoreboard_payload["scoreboard"]["games"][0]["homeTeam"][field] = value

    with pytest.raises(PayloadError, match="too long"):
        parse_scoreboard(scoreboard_payload)


def test_parse_boxscore_rejects_a_player_name_longer_than_the_column(boxscore_payload):
    """An over-long player name is rejected before it can fail a whole transaction."""
    boxscore_payload["game"]["homeTeam"]["players"][0]["name"] = "x" * 129

    with pytest.raises(PayloadError, match="too long"):
        parse_boxscore(boxscore_payload)


@pytest.mark.parametrize("bad_id", ["abcdefghij", "00225000011", "002250000", ""])
def test_a_malformed_game_id_is_a_payload_error(scoreboard_payload, bad_id):
    """A game id must be exactly ten digits."""
    scoreboard_payload["scoreboard"]["games"][0]["gameId"] = bad_id

    with pytest.raises(PayloadError, match="game id"):
        parse_scoreboard(scoreboard_payload)


def test_parse_scoreboard_skips_a_malformed_non_fantasy_game(scoreboard_payload):
    """A broken preseason game must not take the real games down with it."""
    games = scoreboard_payload["scoreboard"]["games"]
    assert games[2]["gameId"] == "0012500003"
    games[2]["homeTeam"]["teamTricode"] = "TOOLONGTRICODE"

    parsed = parse_scoreboard(scoreboard_payload)

    assert [g.game_id for g in parsed] == [
        "0022500001",
        "0022500002",
        "0042500101",
        "0022500004",
    ]


def test_parse_scoreboard_still_rejects_a_malformed_fantasy_game(scoreboard_payload):
    """A game that would be loaded must be valid; its problem is not hidden."""
    scoreboard_payload["scoreboard"]["games"][0]["homeTeam"]["teamTricode"] = "TOOLONG"

    with pytest.raises(PayloadError, match="too long"):
        parse_scoreboard(scoreboard_payload)
