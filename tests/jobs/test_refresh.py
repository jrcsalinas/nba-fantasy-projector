import logging
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from nba_projector.adapters.provider import ScheduledGame
from nba_projector.adapters.repository import Repository
from nba_projector.domain.models import GameLog, Window
from nba_projector.jobs.refresh import (
    _parse_args,
    build_snapshots,
    refresh_deadline,
    refresh_latest,
    run_daily,
    run_refresh,
)

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


class DatedProvider(FakeProvider):
    """A provider whose schedule depends on the requested date."""

    def __init__(
        self,
        logs: dict[str, list[GameLog]],
        game_dates: set[date],
        unplayed_dates: set[date] = frozenset(),  # type: ignore[assignment]
    ) -> None:
        super().__init__(logs, final_games)
        self.game_dates = game_dates
        self.unplayed_dates = unplayed_dates
        self.requested: list[date] = []

    def fetch_schedule(self, game_date: date) -> list[ScheduledGame]:
        self.requested.append(game_date)
        if game_date in self.unplayed_dates:
            return [ScheduledGame("0022600099", GAME_START, False)]
        return final_games(0) if game_date in self.game_dates else []


def test_refresh_latest_snapshots_today_when_there_are_games(repo: Repository) -> None:
    provider = DatedProvider({CURRENT: pool(CURRENT, date(2026, 10, 1), 12)}, {GAME_DAY})

    result = refresh_latest(provider, repo, GAME_DAY)

    assert result == GAME_DAY
    assert repo.snapshot_dates(Window.SEASON, limit=5) == [GAME_DAY]


def test_refresh_latest_falls_back_to_most_recent_game_day(repo: Repository) -> None:
    two_days_ago = GAME_DAY - timedelta(days=2)
    provider = DatedProvider(
        {CURRENT: pool(CURRENT, date(2026, 10, 1), 12)},
        {two_days_ago, GAME_DAY - timedelta(days=5)},
    )

    result = refresh_latest(provider, repo, GAME_DAY)

    assert result == two_days_ago
    assert repo.snapshot_dates(Window.SEASON, limit=5) == [two_days_ago]


def test_refresh_latest_skips_days_whose_games_have_not_finished(repo: Repository) -> None:
    yesterday = GAME_DAY - timedelta(days=1)
    provider = DatedProvider(
        {CURRENT: pool(CURRENT, date(2026, 10, 1), 12)},
        {yesterday},
        unplayed_dates={GAME_DAY},
    )

    result = refresh_latest(provider, repo, GAME_DAY)

    assert result == yesterday
    assert repo.snapshot_dates(Window.SEASON, limit=5) == [yesterday]


def test_refresh_latest_returns_none_and_writes_nothing_without_games(repo: Repository) -> None:
    provider = DatedProvider({}, set())

    result = refresh_latest(provider, repo, GAME_DAY, days=3)

    assert result is None
    assert len(provider.requested) == 3
    assert repo.snapshot_dates(Window.SEASON) == []


def test_cli_accepts_latest_date_and_no_wait() -> None:
    assert _parse_args(["--latest"]).latest is True
    assert _parse_args(["--date", "2026-10-22", "--no-wait"]).date == date(2026, 10, 22)
    assert _parse_args([]).latest is False


def test_cli_rejects_latest_together_with_date() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--latest", "--date", "2026-10-22"])


def evening_games(game_date: date, *, final: bool = True) -> list[ScheduledGame]:
    start = datetime.combine(game_date, time(23, 0), tzinfo=UTC)
    return [ScheduledGame(f"g-{game_date}", start, final)]


class DailyProvider(FakeProvider):
    """A provider with one 7pm Eastern game on each date in ``game_dates``."""

    def __init__(self, logs: dict[str, list[GameLog]], game_dates: set[date]) -> None:
        super().__init__(logs, final_games)
        self.game_dates = game_dates
        self.failures_left = 0

    def fetch_schedule(self, game_date: date) -> list[ScheduledGame]:
        return evening_games(game_date) if game_date in self.game_dates else []

    def fetch_game_logs(self, season: str) -> list[GameLog]:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise RuntimeError("network down")
        return super().fetch_game_logs(season)


def test_run_daily_updates_once_per_day_after_each_days_games(repo: Repository) -> None:
    day_one, day_two = date(2026, 10, 23), date(2026, 10, 24)
    clock = FakeClock(datetime(2026, 10, 23, 15, 0, tzinfo=UTC))  # 11am Eastern on day one
    provider = DailyProvider({CURRENT: pool(CURRENT, date(2026, 10, 1), 12)}, {day_one, day_two})

    run_daily(provider, repo, now=clock.now, sleep=clock.sleep, stop_after=2)

    assert repo.snapshot_dates(Window.SEASON, limit=5) == [day_two, day_one]
    # Day one: wait from 11am Eastern until 3 hours after the 7pm Eastern game starts.
    assert clock.sleeps[0] == 11 * 3600


def test_run_daily_sleeps_until_the_next_eastern_day_when_there_are_no_games(
    repo: Repository,
) -> None:
    clock = FakeClock(datetime(2026, 10, 23, 15, 0, tzinfo=UTC))  # 11am Eastern
    provider = DailyProvider({}, set())

    run_daily(provider, repo, now=clock.now, sleep=clock.sleep, stop_after=1)

    assert clock.sleeps == [13 * 3600]  # until midnight Eastern
    assert repo.snapshot_dates(Window.SEASON) == []


def test_run_daily_retries_after_a_failure_and_keeps_the_last_snapshot(repo: Repository) -> None:
    day = date(2026, 10, 23)
    clock = FakeClock(datetime(2026, 10, 24, 3, 0, tzinfo=UTC))  # 11pm Eastern; games over
    provider = DailyProvider({CURRENT: pool(CURRENT, date(2026, 10, 1), 12)}, {day})
    provider.failures_left = 1

    run_daily(provider, repo, now=clock.now, sleep=clock.sleep, retry_seconds=900, stop_after=2)

    assert 900 in clock.sleeps
    assert repo.snapshot_dates(Window.SEASON, limit=5) == [day]


def test_cli_daemon_flag_is_exclusive_with_date_and_latest() -> None:
    assert _parse_args(["--daemon"]).daemon is True
    for other in (["--date", "2026-10-22"], ["--latest"]):
        with pytest.raises(SystemExit):
            _parse_args(["--daemon", *other])
