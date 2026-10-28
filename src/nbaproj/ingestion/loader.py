"""Idempotent writes of parsed box scores into the database."""

from sqlalchemy.orm import Session

from nbaproj.db.models import Game, GameLog, Player, Team
from nbaproj.ingestion.parsing import (
    BoxScore,
    PlayerLine,
    TeamInfo,
    game_type_from_id,
)

SOURCE_LIVE = "live"


def season_from_game_id(game_id: str) -> str:
    """Read the season label (for example ``2025-26``) from digits 4-5 of a game id."""
    start_year = 2000 + int(game_id[3:5])
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def _merge_team(session: Session, team: TeamInfo) -> None:
    session.merge(
        Team(
            team_id=team.team_id,
            tricode=team.tricode,
            name=team.name,
            city=team.city,
        )
    )


def _merge_line(session: Session, game_id: str, line: PlayerLine, source: str) -> None:
    session.merge(Player(player_id=line.player_id, full_name=line.name))
    session.merge(
        GameLog(
            game_id=game_id,
            player_id=line.player_id,
            team_id=line.team_id,
            minutes=line.minutes,
            pts=line.pts,
            reb=line.reb,
            ast=line.ast,
            stl=line.stl,
            blk=line.blk,
            tpm=line.tpm,
            fgm=line.fgm,
            fga=line.fga,
            ftm=line.ftm,
            fta=line.fta,
            tov=line.tov,
            usage_pct=line.usage_pct,
            source=source,
        )
    )


def store_boxscore(session: Session, box: BoxScore, source: str = SOURCE_LIVE) -> int:
    """Upsert a box score's teams, game, players and lines. Returns the lines written.

    Rows are merged by primary key, so storing the same box score twice leaves the
    same rows and a corrected box score updates them. The caller commits.
    """
    _merge_team(session, box.home)
    _merge_team(session, box.away)
    session.merge(
        Game(
            game_id=box.game_id,
            season=season_from_game_id(box.game_id),
            game_type=game_type_from_id(box.game_id),
            tip_off_utc=box.tip_off_utc,
            home_team_id=box.home.team_id,
            away_team_id=box.away.team_id,
            status=box.status,
        )
    )
    session.flush()
    for line in box.lines:
        _merge_line(session, box.game_id, line, source)
    session.flush()
    return len(box.lines)
