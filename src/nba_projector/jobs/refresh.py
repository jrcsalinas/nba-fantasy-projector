"""Daily refresh job: wait for the day's last game, then snapshot the rankings.

Run with ``uv run python -m nba_projector.jobs.refresh [--date YYYY-MM-DD] [--no-wait]``.
"""

import argparse
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta

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


def _season_averages(logs: Sequence[GameLog], as_of: date) -> list[PlayerAverages]:
    """Season-window averages blended with last season while samples are small."""
    current = average_logs(logs, Window.SEASON, as_of)
    prior_all = average_logs(logs, Window.SEASON, date(_season_start_year(as_of), 7, 31))
    prior = {pid: a for pid, a in prior_all.items() if a.games >= MIN_GAMES[Window.SEASON]}
    underway = max((a.games for a in current.values()), default=0) >= _UNDERWAY_GAMES
    player_ids = set(current) if underway else set(current) | set(prior)

    blended = (blend_with_prior(current.get(pid), prior.get(pid)) for pid in player_ids)
    return [a for a in blended if a is not None]


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
    snapshots: dict[Window, list[RankedPlayer]] = {
        Window.SEASON: rank_players(
            _season_averages(logs, as_of), MIN_GAMES[Window.SEASON], MIN_MINUTES_PG
        )
    }
    for window in (Window.LAST_15, Window.LAST_30):
        averages = list(average_logs(logs, window, as_of).values())
        snapshots[window] = rank_players(averages, MIN_GAMES[window], MIN_MINUTES_PG)
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


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh the daily 9-cat ranking snapshot.")
    parser.add_argument(
        "--date", type=date.fromisoformat, help="Game date, YYYY-MM-DD (US Eastern)"
    )
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for games to finish")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns a non-zero exit code on failure."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args(argv)
    game_date = args.date or datetime.now(EASTERN).date()
    settings = get_settings()
    try:
        run_refresh(
            NbaApiProvider(settings.raw_dir),
            Repository(settings.db_path),
            game_date,
            wait=not args.no_wait,
        )
    except Exception:
        logger.exception("Refresh failed for %s", game_date)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
