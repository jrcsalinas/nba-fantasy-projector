"""Trends for a roster: read the season's box score lines, find the windows, call scoring.

The windows are calendar days in the app's timezone, ending at the latest game day in the
data (not at today's date, so an off day or a stale refresh does not empty the page). The
season is the season of the latest game; only its lines are read. Every player in it is
scored, because the z-scores are measured against all of them, but only the roster's
players come back.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from nbaproj.db.models import Game, GameLog
from nbaproj.roster.service import RosterService
from nbaproj.scoring.stats import LOG_COLUMNS, period_stats
from nbaproj.scoring.trends import PlayerTrend, score_trends

# What is read from the database; the local game day is worked out from the tip-off.
LINE_COLUMNS = tuple(column for column in LOG_COLUMNS if column != "game_day")


@dataclass(frozen=True)
class TrendRow:
    """One roster player and his trend."""

    player_id: int
    name: str
    trend: PlayerTrend


@dataclass(frozen=True)
class TrendsView:
    """What the trends page shows: the roster's trends and the dates they are measured over."""

    window_days: int
    season: str | None
    last_game_day: date | None
    rows: tuple[TrendRow, ...]

    @property
    def has_data(self) -> bool:
        """False when there are no box scores at all yet."""
        return self.season is not None


def _latest_game(session: Session):
    """The season and tip-off of the latest game that has box score lines, or None."""
    return session.execute(
        select(Game.season, Game.tip_off_utc)
        .join(GameLog, GameLog.game_id == Game.game_id)
        .order_by(Game.tip_off_utc.desc())
        .limit(1)
    ).first()


def _season_logs(session: Session, season: str, timezone: str) -> pd.DataFrame:
    """Every box score line of the season, with the local game day of each game."""
    rows = session.execute(
        select(Game.tip_off_utc, *(getattr(GameLog, column) for column in LINE_COLUMNS))
        .join(Game, GameLog.game_id == Game.game_id)
        .where(Game.season == season)
    ).all()
    frame = pd.DataFrame(rows, columns=["tip_off_utc", *LINE_COLUMNS])
    tip_offs = pd.to_datetime(frame["tip_off_utc"], utc=True)
    frame["game_day"] = tip_offs.dt.tz_convert(timezone).dt.date
    frame["usage_pct"] = frame["usage_pct"].astype(float)
    return frame[list(LOG_COLUMNS)]


def load_trends(
    session: Session, roster: RosterService, timezone: str, window_days: int
) -> TrendsView:
    """The trend of each player on ``roster`` over the last ``window_days`` game days."""
    players = roster.players()
    ids = [player.player_id for player in players]
    latest = _latest_game(session)
    if latest is None:
        no_stats = period_stats(pd.DataFrame(columns=list(LOG_COLUMNS)), date.min, date.max)
        trends = score_trends(no_stats, no_stats, window_days, ids)
        season, last_day = None, None
    else:
        season = latest.season
        logs = _season_logs(session, season, timezone)
        last_day = logs["game_day"].max()
        first_day = logs["game_day"].min()
        window_start = last_day - timedelta(days=window_days - 1)
        trends = score_trends(
            period_stats(logs, first_day, last_day),
            period_stats(logs, window_start, last_day),
            window_days,
            ids,
        )
    rows = tuple(
        TrendRow(player.player_id, player.name, trend) for player, trend in zip(players, trends)
    )
    return TrendsView(window_days, season, last_day, rows)
