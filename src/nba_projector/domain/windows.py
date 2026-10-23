"""Window selection, averaging, and prior-season blending."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, timedelta

from nba_projector.domain.models import GameLog, PlayerAverages, Window

_WINDOW_DAYS: dict[Window, int] = {Window.LAST_15: 15, Window.LAST_30: 30}
_RATE_FIELDS = (
    "minutes_pg",
    "pts",
    "reb",
    "ast",
    "stl",
    "blk",
    "fg3m",
    "fgm",
    "fga",
    "ftm",
    "fta",
    "tov",
)
_LOG_FIELD_FOR_RATE = {"minutes_pg": "minutes"}


def season_for(day: date) -> str:
    """Return the season label for a date; August 1 starts the next season.

    Args:
        day: Any calendar date.

    Returns:
        A label such as ``"2026-27"``.
    """
    start_year = day.year if day.month >= 8 else day.year - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def _in_window(log: GameLog, window: Window, as_of: date) -> bool:
    if log.game_date > as_of:
        return False
    if window is Window.SEASON:
        return log.season == season_for(as_of)
    return log.game_date >= as_of - timedelta(days=_WINDOW_DAYS[window] - 1)


def average_logs(logs: Sequence[GameLog], window: Window, as_of: date) -> dict[int, PlayerAverages]:
    """Average game logs per player over a window ending on ``as_of`` (inclusive).

    Logs are grouped by ``player_id``, so a traded player is one row.

    Args:
        logs: Game logs, any order.
        window: Which window to average over.
        as_of: Last day (inclusive) of the window.

    Returns:
        Mapping of ``player_id`` to per-game averages.
    """
    by_player: dict[int, list[GameLog]] = defaultdict(list)
    for log in logs:
        if _in_window(log, window, as_of):
            by_player[log.player_id].append(log)

    result: dict[int, PlayerAverages] = {}
    for player_id, player_logs in by_player.items():
        games = len(player_logs)
        rates = {
            name: sum(getattr(log, _LOG_FIELD_FOR_RATE.get(name, name)) for log in player_logs)
            / games
            for name in _RATE_FIELDS
        }
        latest = max(player_logs, key=lambda log: log.game_date)
        result[player_id] = PlayerAverages(
            player_id=player_id,
            player_name=latest.player_name,
            games=games,
            **rates,
        )
    return result


def blend_with_prior(
    current: PlayerAverages | None,
    prior: PlayerAverages | None,
    full_weight_games: int = 20,
) -> PlayerAverages | None:
    """Blend this season's averages with last season's while the sample is small.

    Prior weight is ``max(0, full_weight_games - current.games)``; once the current
    sample reaches ``full_weight_games`` the current averages are returned unchanged.

    Args:
        current: This season's averages, if any.
        prior: Last season's averages, if any.
        full_weight_games: Games at which the prior stops contributing.

    Returns:
        Blended averages, or ``None`` if both sides are missing.
    """
    if prior is None:
        return current
    if current is None:
        return replace(prior, games=full_weight_games)

    prior_weight = max(0, full_weight_games - current.games)
    if prior_weight == 0:
        return current

    total = current.games + prior_weight
    rates = {
        name: (getattr(current, name) * current.games + getattr(prior, name) * prior_weight) / total
        for name in _RATE_FIELDS
    }
    return PlayerAverages(
        player_id=current.player_id,
        player_name=current.player_name,
        games=total,
        **rates,
    )
