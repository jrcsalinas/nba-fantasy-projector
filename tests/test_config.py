from pathlib import Path

import pytest

from nba_projector.config import Settings


def test_roster_path_defaults_to_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NBA_PROJECTOR_ROSTER_PATH", raising=False)

    assert Settings(_env_file=None).roster_path == Path("data/roster.json")


def test_roster_path_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NBA_PROJECTOR_ROSTER_PATH", "elsewhere/team.json")

    assert Settings(_env_file=None).roster_path == Path("elsewhere/team.json")
