from datetime import date
from pathlib import Path

import pytest

from nba_projector.adapters.repository import Repository
from nba_projector.domain.models import SORT_KEYS, Category, GameLog, RankedPlayer, Window


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
