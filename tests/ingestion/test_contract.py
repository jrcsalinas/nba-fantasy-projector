"""Tests for the helpers behind the manual payload contract checks."""

from nbaproj.ingestion.contract import missing_fields, report


def test_missing_fields_names_each_absent_field_with_its_location():
    """Only absent fields are listed, prefixed with where they were expected."""
    assert missing_fields({"a": 1, "c": 3}, ["a", "b", "c", "d"], "game") == [
        "game.b",
        "game.d",
    ]


def test_report_prints_problems_and_returns_the_exit_code(capsys):
    """No problems exits 0 with OK; any problem exits 1 and lists it."""
    assert report([]) == 0
    assert "RESULT: OK" in capsys.readouterr().out

    assert report(["game.b"]) == 1
    out = capsys.readouterr().out
    assert "MISSING game.b" in out
    assert "RESULT: FIELDS MISSING" in out
