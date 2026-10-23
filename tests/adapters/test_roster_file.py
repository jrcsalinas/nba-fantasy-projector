import json
from pathlib import Path

import pytest

from nba_projector.adapters.roster_file import (
    RosterFileError,
    RosterFileNotFoundError,
    load_roster,
)
from nba_projector.domain.roster import Roster, RosterPlayer


def write(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "roster.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def test_load_roster_reads_active_and_il(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        {
            "active": [{"player_id": 1, "name": "Dennis Schröder"}, {"player_id": 2, "name": "B"}],
            "il": [{"player_id": 3, "name": "C"}],
        },
    )

    roster = load_roster(path)

    assert roster == Roster(
        active=(RosterPlayer(1, "Dennis Schröder"), RosterPlayer(2, "B")),
        il=(RosterPlayer(3, "C"),),
    )
    assert roster.ids == {1, 2, 3}


def test_load_roster_il_is_optional(tmp_path: Path) -> None:
    roster = load_roster(write(tmp_path, {"active": [{"player_id": 1, "name": "A"}]}))

    assert roster.il == ()


def test_load_roster_missing_file_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(RosterFileNotFoundError, match=r"roster\.json"):
        load_roster(tmp_path / "roster.json")


def test_load_roster_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "roster.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(RosterFileError, match="not valid JSON"):
        load_roster(path)


@pytest.mark.parametrize(
    "content",
    [
        {"active": [{"name": "A"}]},
        {"active": [{"player_id": "abc", "name": "A"}]},
        {"il": []},
        ["not", "an", "object"],
    ],
)
def test_load_roster_schema_errors_raise(tmp_path: Path, content: object) -> None:
    with pytest.raises(RosterFileError):
        load_roster(write(tmp_path, content))


def test_load_roster_duplicate_player_raises(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        {
            "active": [{"player_id": 1, "name": "A"}],
            "il": [{"player_id": 1, "name": "A again"}],
        },
    )

    with pytest.raises(RosterFileError, match="more than once"):
        load_roster(path)
