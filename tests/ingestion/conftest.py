"""Shared fixtures for ingestion tests."""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Read a JSON payload from tests/fixtures."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(name="scoreboard_payload")
def scoreboard_payload_fixture() -> dict:
    """The hand-built live scoreboard payload."""
    return load_fixture("scoreboard.json")


@pytest.fixture(name="boxscore_payload")
def boxscore_payload_fixture() -> dict:
    """The hand-built regular box score payload."""
    return load_fixture("boxscore.json")


@pytest.fixture(name="overtime_payload")
def overtime_payload_fixture() -> dict:
    """The hand-built overtime box score payload."""
    return load_fixture("boxscore_overtime.json")
