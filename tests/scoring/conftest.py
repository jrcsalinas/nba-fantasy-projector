"""Helpers for the scoring tests: small hand-made game logs."""

from datetime import date

import pandas as pd
import pytest

from nbaproj.scoring.stats import LOG_COLUMNS, STAT_COLUMNS

ZERO_LINE = {
    "minutes": 0.0,
    "pts": 0,
    "reb": 0,
    "ast": 0,
    "stl": 0,
    "blk": 0,
    "tpm": 0,
    "fgm": 0,
    "fga": 0,
    "ftm": 0,
    "fta": 0,
    "tov": 0,
    "usage_pct": float("nan"),
}


def day(number: int) -> date:
    """A date: day 1 is 1 January 2026, so ``day(10)`` is 10 January."""
    return date(2026, 1, number)


def make_logs(*lines: dict) -> pd.DataFrame:
    """Game logs from lines that name only what matters; everything else is zero.

    Each line needs ``player_id`` and ``day`` (a number, see ``day``).
    """
    rows = []
    for line in lines:
        row = {**ZERO_LINE, **line}
        row["game_day"] = day(row.pop("day"))
        rows.append(row)
    return pd.DataFrame(rows, columns=list(LOG_COLUMNS))


@pytest.fixture(name="logs_factory")
def logs_factory_fixture():
    """``make_logs`` as a fixture, for tests that prefer it."""
    return make_logs


def make_stats(**players: dict) -> pd.DataFrame:
    """Period stats for players named ``p<id>``; whatever a player does not name is zero."""
    rows = {}
    for name, values in players.items():
        row = {column: 0.0 for column in STAT_COLUMNS}
        row.update({"games": 10, **values})
        rows[int(name.removeprefix("p"))] = row
    stats = pd.DataFrame.from_dict(rows, orient="index", columns=STAT_COLUMNS)
    stats.index.name = "player_id"
    return stats
