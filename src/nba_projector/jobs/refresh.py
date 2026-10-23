"""Daily refresh job: wait for the day's last game, then snapshot the rankings.

Run with ``uv run python -m nba_projector.jobs.refresh`` and one of ``--date YYYY-MM-DD``,
``--latest`` (load the most recent finished game day), or ``--daemon`` (keep updating daily).
``--no-wait`` skips waiting for games to finish.
"""

import argparse
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time

from nba_projector.adapters.nba_api_provider import NbaApiProvider
from nba_projector.adapters.provider import DataProvider, ScheduledGame
from nba_projector.adapters.repository import Repository
from nba_projector.config import EASTERN, get_settings
from nba_projector.domain.models import GameLog, PlayerAverages, RankedPlayer, Window
from nba_projector.domain.ranking import MIN_GAMES, MIN_MINUTES_PG, rank_players
from nba_projector.domain.windows import average_logs, blend_with_prior, season_for

logger = logging.getLogger(__name__)

_UNDERWAY_GAMES = 5


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _season_start_year(as_of: date) -> int:
    return int(season_for(as_of)[:4])


def _prior_season(as_of: date) -> str:
    return season_for(date(_season_start_year(as_of), 7, 31))


def refresh_deadline(
    games: Sequence[ScheduledGame], buffer: timedelta = timedelta(hours=3)
) -> datetime | None:
    """Return when the day's last game should be over: latest start plus ``buffer``.

    Args:
        games: The day's scheduled games.
        buffer: Assumed game length.

    Returns:
        The deadline, or ``None`` if there are no games.
    """
    if not games:
        return None
    return max(g.start_utc for g in games) + buffer


def _season_averages(
    logs: Sequence[GameLog], as_of: date
) -> tuple[list[PlayerAverages], dict[int, int]]:
    """Season-window averages blended with last season while samples are small.

    Returns the blended averages and the games each player has actually played this season.
    """
    current = average_logs(logs, Window.SEASON, as_of)
    prior_all = average_logs(logs, Window.SEASON, date(_season_start_year(as_of), 7, 31))
    prior = {pid: a for pid, a in prior_all.items() if a.games >= MIN_GAMES[Window.SEASON]}
    underway = max((a.games for a in current.values()), default=0) >= _UNDERWAY_GAMES
    player_ids = set(current) if underway else set(current) | set(prior)

    blended = (blend_with_prior(current.get(pid), prior.get(pid)) for pid in player_ids)
    return [a for a in blended if a is not None], {pid: a.games for pid, a in current.items()}


def _with_games_played(
    ranked: Sequence[RankedPlayer], games_played: Mapping[int, int] | None = None
) -> list[RankedPlayer]:
    """Attach games played: from ``games_played`` when given, else the averages' own count."""
    return [
        replace(
            r,
            games_played=(
                games_played.get(r.player_id, 0)
                if games_played is not None
                else (r.averages.games if r.averages else None)
            ),
        )
        for r in ranked
    ]


def build_snapshots(logs: Sequence[GameLog], as_of: date) -> dict[Window, list[RankedPlayer]]:
    """Rank players for every window as of a date.

    The Season window blends in last season's averages. Players with no games this season are
    included only until some player in the league has played at least five games.

    Args:
        logs: Game logs for this and the prior season.
        as_of: Last day (inclusive) of every window.

    Returns:
        Ranked players per window.
    """
    season_averages, season_games = _season_averages(logs, as_of)
    ranked_season = rank_players(season_averages, MIN_GAMES[Window.SEASON], MIN_MINUTES_PG)
    snapshots: dict[Window, list[RankedPlayer]] = {
        Window.SEASON: _with_games_played(ranked_season, season_games)
    }
    for window in (Window.LAST_15, Window.LAST_30):
        averages = list(average_logs(logs, window, as_of).values())
        ranked = rank_players(averages, MIN_GAMES[window], MIN_MINUTES_PG)
        snapshots[window] = _with_games_played(ranked)
    return snapshots


def _wait_for_final(
    provider: DataProvider,
    game_date: date,
    games: Sequence[ScheduledGame],
    now: Callable[[], datetime],
    sleep: Callable[[float], None],
    poll_seconds: float,
    cutoff: timedelta,
) -> None:
    deadline = refresh_deadline(games)
    if deadline is not None and (delay := (deadline - now()).total_seconds()) > 0:
        logger.info("Waiting %.0fs until %s for the last game to finish", delay, deadline)
        sleep(delay)

    cutoff_at = max(g.start_utc for g in games) + cutoff
    while True:
        unfinished = [g.game_id for g in provider.fetch_schedule(game_date) if not g.is_final]
        if not unfinished:
            logger.info("All games on %s are final", game_date)
            return
        if now() >= cutoff_at:
            logger.warning("Cutoff reached; continuing without unfinished games: %s", unfinished)
            return
        logger.info("%d game(s) not final yet; polling in %.0fs", len(unfinished), poll_seconds)
        sleep(poll_seconds)


def run_refresh(
    provider: DataProvider,
    repo: Repository,
    game_date: date,
    *,
    wait: bool = True,
    now: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 600,
    cutoff: timedelta = timedelta(hours=6),
) -> bool:
    """Fetch data and save one ranking snapshot for a game date.

    Args:
        provider: Source of schedule and game logs.
        repo: Store for logs and snapshots.
        game_date: US Eastern game date to snapshot.
        wait: Wait for the last game to finish; ``False`` skips waiting and polling.
        now: Clock, injectable for tests.
        sleep: Sleep function, injectable for tests.
        poll_seconds: Seconds between finality checks.
        cutoff: Stop polling this long after the last game's start.

    Returns:
        ``True`` if a snapshot was written, ``False`` on a day without games.

    Raises:
        Exception: Any fetch failure propagates before anything is written.
    """
    games = provider.fetch_schedule(game_date)
    if not games:
        logger.info("No games on %s; keeping the last snapshot", game_date)
        return False
    logger.info("%d game(s) scheduled on %s", len(games), game_date)

    if wait:
        _wait_for_final(provider, game_date, games, now, sleep, poll_seconds, cutoff)

    current_season, prior_season = season_for(game_date), _prior_season(game_date)
    repo.save_game_logs(provider.fetch_game_logs(current_season))
    if not repo.load_game_logs([prior_season]):
        repo.save_game_logs(provider.fetch_game_logs(prior_season))

    logs = repo.load_game_logs([prior_season, current_season])
    snapshots = build_snapshots(logs, game_date)
    repo.replace_snapshots(game_date, snapshots)
    return True


def refresh_latest(
    provider: DataProvider, repo: Repository, today: date, *, days: int = 7
) -> date | None:
    """Snapshot the most recent day with a finished game, looking back from ``today``.

    Days whose games have not started or finished yet are skipped. Does not wait for games, so
    it is for loading data on demand rather than for the daily scheduled run. Re-running a date
    replaces its snapshot.

    Args:
        provider: Source of schedule and game logs.
        repo: Store for logs and snapshots.
        today: First date to try (US Eastern).
        days: How many days to look back, including ``today``.

    Returns:
        The date that was snapshotted, or ``None`` if none of the days had games.
    """
    for offset in range(days):
        game_date = today - timedelta(days=offset)
        if not any(g.is_final for g in provider.fetch_schedule(game_date)):
            continue
        if run_refresh(provider, repo, game_date, wait=False):
            return game_date
    logger.warning("No finished games in the last %d days; nothing was saved", days)
    return None


def _seconds_until_next_eastern_day(game_date: date, now: datetime) -> float:
    next_midnight = datetime.combine(game_date + timedelta(days=1), dt_time.min, tzinfo=EASTERN)
    return (next_midnight - now).total_seconds()


def run_daily(
    provider: DataProvider,
    repo: Repository,
    *,
    now: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], None] = time.sleep,
    retry_seconds: float = 900,
    stop_after: int | None = None,
) -> None:
    """Keep the rankings current: update after each day's last game, every day.

    Each pass refreshes today's date in US Eastern time (waiting for that day's games to finish),
    then sleeps until the next Eastern day. A failed pass keeps the last good snapshot and is
    retried after ``retry_seconds``.

    Args:
        provider: Source of schedule and game logs.
        repo: Store for logs and snapshots.
        now: Clock, injectable for tests.
        sleep: Sleep function, injectable for tests.
        retry_seconds: Delay before retrying after a failure.
        stop_after: Stop after this many passes (for tests); ``None`` runs until interrupted.
    """
    passes = 0
    while stop_after is None or passes < stop_after:
        passes += 1
        game_date = now().astimezone(EASTERN).date()
        try:
            run_refresh(provider, repo, game_date, now=now, sleep=sleep)
        except Exception:
            logger.exception(
                "Refresh failed for %s; retrying in %.0f minutes", game_date, retry_seconds / 60
            )
            sleep(retry_seconds)
            continue
        delay = _seconds_until_next_eastern_day(game_date, now())
        if delay > 0:
            logger.info(
                "Done for %s; sleeping %.1f hours until the next day", game_date, delay / 3600
            )
            sleep(delay)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the daily 9-cat ranking snapshot.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--date", type=date.fromisoformat, help="Game date, YYYY-MM-DD (US Eastern)"
    )
    target.add_argument(
        "--latest",
        action="store_true",
        help="Snapshot the most recent day with games (never waits for games to finish)",
    )
    target.add_argument(
        "--daemon",
        action="store_true",
        help="Keep running: update after each day's last game, every day (Ctrl+C to stop)",
    )
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for games to finish")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns a non-zero exit code on failure."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args(argv)
    today = datetime.now(EASTERN).date()
    game_date = args.date or today
    settings = get_settings()
    try:
        provider = NbaApiProvider(settings.raw_dir)
        repo = Repository(settings.db_path)
        if args.daemon:
            run_daily(provider, repo)
        elif args.latest:
            refresh_latest(provider, repo, today)
        else:
            run_refresh(provider, repo, game_date, wait=not args.no_wait)
    except KeyboardInterrupt:
        logger.info("Stopped")
    except Exception:
        logger.exception("Refresh failed for %s", game_date)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
