import logging
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from nba_projector.adapters.provider import ScheduledGame
from nba_projector.adapters.repository import Repository
from nba_projector.domain.models import GameLog, Window
from nba_projector.jobs.refresh import build_snapshots, refresh_deadline, run_refresh

GAME_DAY = date(2026, 10, 23)
GAME_START = datetime(2026, 10, 23, 23, 0, tzinfo=UTC)
CURRENT = "2026-27"
PRIOR = "2025-26"


def make_logs(player_id: int, season: str, start: date, games: int, pts: int = 20) -> list[GameLog]:
    return [
        GameLog(
            season=season,
            game_id=f"{season}-{player_id}-{i}",
            game_date=start + timedelta(days=i),
            player_id=player_id,
            player_name=f"Player {player_id}",
            minutes=30.0,
            pts=pts + player_id,
            reb=5,
            ast=5,
            stl=1,
            blk=1,
            fg3m=2,
            fgm=6,
            fga=12,
            ftm=3,
            fta=4,
            tov=2,
        )
        for i in range(games)
    ]


def pool(season: str, start: date, games: int, players: Sequence[int] = (1, 2, 3)) -> list[GameLog]:
    return [log for pid in players for log in make_logs(pid, season, start, games)]


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)


class FakeProvider:
    def __init__(
        self,
        logs_by_season: dict[str, list[GameLog]],
        schedule: Callable[[int], list[ScheduledGame]],
        fail_logs: bool = False,
    ) -> None:
        self.logs_by_season = logs_by_season
        self.schedule = schedule
        self.fail_logs = fail_logs
        self.schedule_calls = 0
        self.log_seasons: list[str] = []

    def fetch_schedule(self, game_date: date) -> list[ScheduledGame]:
        self.schedule_calls += 1
        return self.schedule(self.schedule_calls)

    def fetch_game_logs(self, season: str) -> list[GameLog]:
        if self.fail_logs:
            raise RuntimeError("boom")
        self.log_seasons.append(season)
        return self.logs_by_season.get(season, [])


def final_games(_: int) -> list[ScheduledGame]:
    return [ScheduledGame("0022600001", GAME_START, True)]


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    return Repository(tmp_path / "test.db")


def test_refresh_deadline_is_latest_start_plus_three_hours() -> None:
    games = [
        ScheduledGame("a", GAME_START, False),
        ScheduledGame("b", GAME_START + timedelta(hours=2), False),
    ]

    assert refresh_deadline(games) == GAME_START + timedelta(hours=5)


def test_refresh_deadline_returns_none_without_games() -> None:
    assert refresh_deadline([]) is None


def test_build_snapshots_has_all_three_windows() -> None:
    logs = pool(CURRENT, date(2026, 11, 14), 12)

    snapshots = build_snapshots(logs, date(2026, 11, 30))

    assert set(snapshots) == set(Window)
    assert all(len(players) == 3 for players in snapshots.values())


def test_build_snapshots_season_opener_includes_prior_only_players() -> None:
    logs = pool(PRIOR, date(2026, 1, 1), 30)

    snapshots = build_snapshots(logs, date(2026, 10, 22))

    assert len(snapshots[Window.SEASON]) == 3
    assert snapshots[Window.LAST_15] == []


def test_build_snapshots_excludes_prior_only_players_once_season_is_underway() -> None:
    logs = pool(PRIOR, date(2026, 1, 1), 30) + pool(CURRENT, date(2026, 10, 20), 6, players=(1, 2))

    snapshots = build_snapshots(logs, date(2026, 10, 30))

    assert {p.player_id for p in snapshots[Window.SEASON]} == {1, 2}


def test_run_refresh_no_games_writes_nothing_and_returns_false(repo: Repository) -> None:
    provider = FakeProvider({}, lambda _: [])

    wrote = run_refresh(provider, repo, GAME_DAY, wait=False)

    assert wrote is False
    assert repo.snapshot_dates(Window.SEASON) == []


def test_run_refresh_waits_until_deadline_then_polls_until_final(repo: Repository) -> None:
    clock = FakeClock(datetime(2026, 10, 23, 15, 0, tzinfo=UTC))

    def schedule(call: int) -> list[ScheduledGame]:
        return [ScheduledGame("0022600001", GAME_START, call >= 4)]

    provider = FakeProvider({CURRENT: pool(CURRENT, date(2026, 10, 1), 12)}, schedule)

    wrote = run_refresh(provider, repo, GAME_DAY, now=clock.now, sleep=clock.sleep)

    assert wrote is True
    assert clock.sleeps == [11 * 3600, 600, 600]
    assert repo.snapshot_dates(Window.SEASON) == [GAME_DAY]


def test_run_refresh_stops_polling_at_cutoff_and_writes_with_final_games(
    repo: Repository, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock(GAME_START + timedelta(hours=3))
    provider = FakeProvider(
        {CURRENT: pool(CURRENT, date(2026, 10, 1), 12)},
        lambda _: [
            ScheduledGame("done", GAME_START, True),
            ScheduledGame("stuck", GAME_START, False),
        ],
    )

    with caplog.at_level(logging.WARNING):
        wrote = run_refresh(provider, repo, GAME_DAY, now=clock.now, sleep=clock.sleep)

    assert wrote is True
    assert clock.current == GAME_START + timedelta(hours=6)
    assert "stuck" in caplog.text
    assert repo.snapshot_dates(Window.SEASON) == [GAME_DAY]


def test_run_refresh_rerun_same_date_does_not_duplicate(repo: Repository) -> None:
    provider = FakeProvider({CURRENT: pool(CURRENT, date(2026, 10, 1), 12)}, final_games)

    run_refresh(provider, repo, GAME_DAY, wait=False)
    run_refresh(provider, repo, GAME_DAY, wait=False)

    assert repo.snapshot_dates(Window.SEASON, limit=5) == [GAME_DAY]
    assert len(repo.load_snapshot(GAME_DAY, Window.SEASON)) == 3
    assert len(repo.load_game_logs([CURRENT])) == 36


def test_run_refresh_fetches_prior_season_only_when_missing(repo: Repository) -> None:
    provider = FakeProvider(
        {
            CURRENT: pool(CURRENT, date(2026, 10, 1), 12),
            PRIOR: pool(PRIOR, date(2026, 1, 1), 30),
        },
        final_games,
    )

    run_refresh(provider, repo, GAME_DAY, wait=False)
    run_refresh(provider, repo, GAME_DAY, wait=False)

    assert provider.log_seasons == [CURRENT, PRIOR, CURRENT]


def test_run_refresh_fetch_failure_leaves_previous_snapshot_intact(repo: Repository) -> None:
    good = FakeProvider({CURRENT: pool(CURRENT, date(2026, 10, 1), 12)}, final_games)
    run_refresh(good, repo, GAME_DAY, wait=False)
    before = repo.load_snapshot(GAME_DAY, Window.SEASON)
    broken = FakeProvider({}, final_games, fail_logs=True)

    with pytest.raises(RuntimeError, match="boom"):
        run_refresh(broken, repo, GAME_DAY, wait=False)

    assert repo.load_snapshot(GAME_DAY, Window.SEASON) == before


def test_build_snapshots_reports_games_actually_played_not_blended_weight() -> None:
    logs = pool(PRIOR, date(2026, 1, 1), 30) + pool(CURRENT, date(2026, 10, 20), 6, players=(1, 2))

    snapshots = build_snapshots(logs, date(2026, 10, 30))

    season = {p.player_id: p for p in snapshots[Window.SEASON]}
    assert season[1].games_played == 6
    assert season[1].averages is not None
    assert season[1].averages.games == 20  # blended weight, not games played


def test_build_snapshots_season_opener_games_played_is_zero_for_prior_only() -> None:
    logs = pool(PRIOR, date(2026, 1, 1), 30)

    season = build_snapshots(logs, date(2026, 10, 22))[Window.SEASON]

    assert {p.games_played for p in season} == {0}


def test_build_snapshots_window_games_played_counts_games_in_window() -> None:
    logs = pool(CURRENT, date(2026, 11, 14), 12)

    snapshots = build_snapshots(logs, date(2026, 11, 30))

    assert {p.games_played for p in snapshots[Window.LAST_15]} == {10}
    assert {p.games_played for p in snapshots[Window.LAST_30]} == {12}
