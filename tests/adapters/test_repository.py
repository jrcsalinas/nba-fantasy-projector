import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from nba_projector.adapters.repository import Repository
from nba_projector.domain.models import (
    SORT_KEYS,
    Category,
    GameLog,
    PlayerAverages,
    RankedPlayer,
    Window,
)


def make_log(player_id: int = 1, game_id: str = "g1") -> GameLog:
    return GameLog(
        season="2026-27",
        game_id=game_id,
        game_date=date(2026, 10, 22),
        player_id=player_id,
        player_name=f"Player {player_id}",
        minutes=30.5,
        pts=20,
        reb=5,
        ast=4,
        stl=1,
        blk=1,
        fg3m=2,
        fgm=8,
        fga=15,
        ftm=2,
        fta=3,
        tov=2,
    )


def make_ranked(player_id: int, rank: int = 1) -> RankedPlayer:
    return RankedPlayer(
        player_id=player_id,
        player_name=f"Player {player_id}",
        z={cat: float(i) / 10 for i, cat in enumerate(Category)},
        total=3.6,
        ranks={key: rank + i for i, key in enumerate(SORT_KEYS)},
    )


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    return Repository(tmp_path / "nested" / "test.db")


def test_save_game_logs_is_idempotent(repo: Repository) -> None:
    logs = [make_log(1, "g1"), make_log(2, "g1")]

    repo.save_game_logs(logs)
    repo.save_game_logs(logs)

    assert len(repo.load_game_logs(["2026-27"])) == 2


def test_load_game_logs_filters_by_season(repo: Repository) -> None:
    repo.save_game_logs([make_log(1, "g1")])

    assert repo.load_game_logs(["2025-26"]) == []
    assert repo.load_game_logs([]) == []
    assert repo.load_game_logs(["2026-27"]) == [make_log(1, "g1")]


def test_replace_snapshots_replaces_same_date(repo: Repository) -> None:
    day = date(2026, 10, 22)

    repo.replace_snapshots(day, {Window.SEASON: [make_ranked(1)]})
    repo.replace_snapshots(day, {Window.SEASON: [make_ranked(2)]})

    assert [p.player_id for p in repo.load_snapshot(day, Window.SEASON)] == [2]


def test_replace_snapshots_rolls_back_on_error(repo: Repository) -> None:
    day = date(2026, 10, 22)
    repo.replace_snapshots(day, {Window.SEASON: [make_ranked(1)], Window.LAST_15: [make_ranked(1)]})
    bad = make_ranked(9)
    del bad.ranks["pts"]

    with pytest.raises(KeyError):
        repo.replace_snapshots(day, {Window.SEASON: [make_ranked(2)], Window.LAST_15: [bad]})

    for window in (Window.SEASON, Window.LAST_15):
        assert [p.player_id for p in repo.load_snapshot(day, window)] == [1]


def test_snapshot_dates_returns_newest_first_per_window(repo: Repository) -> None:
    for day in (date(2026, 10, 20), date(2026, 10, 22), date(2026, 10, 21)):
        repo.replace_snapshots(day, {Window.SEASON: [make_ranked(1)]})
    repo.replace_snapshots(date(2026, 10, 25), {Window.LAST_15: [make_ranked(1)]})

    assert repo.snapshot_dates(Window.SEASON) == [date(2026, 10, 22), date(2026, 10, 21)]
    assert repo.snapshot_dates(Window.SEASON, limit=5)[-1] == date(2026, 10, 20)
    assert repo.snapshot_dates(Window.LAST_15) == [date(2026, 10, 25)]
    assert repo.snapshot_dates(Window.LAST_30) == []


def test_load_snapshot_round_trips_ranked_players(repo: Repository) -> None:
    day = date(2026, 10, 22)
    players = [make_ranked(1, rank=1), make_ranked(2, rank=2)]

    repo.replace_snapshots(day, {Window.SEASON: players})

    loaded = repo.load_snapshot(day, Window.SEASON)
    assert loaded == players
    assert set(loaded[0].ranks) == set(SORT_KEYS)
    assert len(loaded[0].ranks) == 10


def test_load_snapshot_missing_date_returns_empty_list(repo: Repository) -> None:
    assert repo.load_snapshot(date(2026, 1, 1), Window.SEASON) == []


def make_averages(player_id: int = 1) -> PlayerAverages:
    return PlayerAverages(
        player_id=player_id,
        player_name=f"Player {player_id}",
        games=20,
        minutes_pg=33.5,
        pts=27.25,
        reb=7.5,
        ast=6.0,
        stl=1.5,
        blk=0.75,
        fg3m=3.0,
        fgm=9.5,
        fga=19.0,
        ftm=5.0,
        fta=6.0,
        tov=2.5,
    )


def test_load_snapshot_round_trips_averages_and_games_played(repo: Repository) -> None:
    day = date(2026, 10, 22)
    player = replace(make_ranked(1), averages=make_averages(1), games_played=7)

    repo.replace_snapshots(day, {Window.SEASON: [player]})

    assert repo.load_snapshot(day, Window.SEASON) == [player]


def test_load_snapshot_without_averages_returns_none(repo: Repository) -> None:
    day = date(2026, 10, 22)

    repo.replace_snapshots(day, {Window.SEASON: [make_ranked(1)]})

    loaded = repo.load_snapshot(day, Window.SEASON)[0]
    assert loaded.averages is None
    assert loaded.games_played is None


def test_existing_database_without_stat_columns_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE snapshots (snapshot_date TEXT NOT NULL, window TEXT NOT NULL, "
            "player_id INTEGER NOT NULL, player_name TEXT NOT NULL, total REAL NOT NULL, "
            + ", ".join(f"z_{c.value} REAL NOT NULL" for c in Category)
            + ", "
            + ", ".join(f"rank_{k} INTEGER NOT NULL" for k in SORT_KEYS)
            + ", PRIMARY KEY (snapshot_date, window, player_id))"
        )
        values = ["2026-10-21", "season", 1, "Old Row", 1.5, *([0.1] * 9), *range(1, 11)]
        conn.execute(f"INSERT INTO snapshots VALUES ({', '.join('?' for _ in values)})", values)
    conn.close()

    repo = Repository(path)

    old = repo.load_snapshot(date(2026, 10, 21), Window.SEASON)
    assert [p.player_name for p in old] == ["Old Row"]
    assert old[0].averages is None
    new = replace(make_ranked(2), averages=make_averages(2), games_played=3)
    repo.replace_snapshots(date(2026, 10, 22), {Window.SEASON: [new]})
    assert repo.load_snapshot(date(2026, 10, 22), Window.SEASON) == [new]
