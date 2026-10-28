"""Idempotent writes of parsed box scores into the database."""

from collections.abc import Iterator, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from nbaproj.db.models import Game, GameLog, Player, Team
from nbaproj.ingestion.parsing import (
    BoxScore,
    PlayerLine,
    ScoreboardGame,
    TeamInfo,
    game_type_from_id,
)

SOURCE_LIVE = "live"
FINAL = "final"
LOOKUP_CHUNK = 500


# A box score and a schedule entry both carry what the ``game`` table needs.
GameHeader = ScoreboardGame | BoxScore


def season_from_game_id(game_id: str) -> str:
    """Read the season label (for example ``2025-26``) from digits 4-5 of a game id."""
    start_year = 2000 + int(game_id[3:5])
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def merge_team(session: Session, team: TeamInfo) -> None:
    """Insert or update a team by id."""
    session.merge(
        Team(
            team_id=team.team_id,
            tricode=team.tricode,
            name=team.name,
            city=team.city,
        )
    )


def game_log_row(game_id: str, line: PlayerLine, source: str) -> GameLog:
    """Build the ``game_log`` row for one player line."""
    return GameLog(
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


def _merge_line(session: Session, game_id: str, line: PlayerLine, source: str) -> None:
    session.merge(Player(player_id=line.player_id, full_name=line.name))
    session.merge(game_log_row(game_id, line, source))


def chunked(items: Sequence, size: int = LOOKUP_CHUNK) -> Iterator[Sequence]:
    """Split ``items`` into slices of at most ``size``, to keep IN lists short."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _existing_games(session: Session, game_ids: list[str]) -> dict[str, Game]:
    """Load the stored games among ``game_ids`` in a few queries, into the session."""
    found: dict[str, Game] = {}
    for chunk in chunked(game_ids):
        for game in session.scalars(select(Game).where(Game.game_id.in_(chunk))):
            found[game.game_id] = game
    return found


def upsert_games(session: Session, headers: Sequence[GameHeader]) -> int:
    """Insert or update teams and games in bulk. Returns the number of games.

    A stored ``final`` game is never moved back to a non-final status. The caller commits.
    """
    teams = {team.team_id: team for h in headers for team in (h.home, h.away)}
    for team in teams.values():
        merge_team(session, team)
    session.flush()
    existing = _existing_games(session, [h.game_id for h in headers])
    for header in headers:
        row = existing.get(header.game_id)
        if row is None:
            session.add(
                Game(
                    game_id=header.game_id,
                    season=season_from_game_id(header.game_id),
                    game_type=game_type_from_id(header.game_id),
                    tip_off_utc=header.tip_off_utc,
                    home_team_id=header.home.team_id,
                    away_team_id=header.away.team_id,
                    status=header.status,
                )
            )
            continue
        row.tip_off_utc = header.tip_off_utc
        row.home_team_id = header.home.team_id
        row.away_team_id = header.away.team_id
        row.status = FINAL if row.status == FINAL else header.status
    session.flush()
    return len(headers)


def store_boxscore(session: Session, box: BoxScore, source: str = SOURCE_LIVE) -> int:
    """Upsert a box score's teams, game, players and lines. Returns the lines written.

    Rows are merged by primary key, so storing the same box score twice leaves the
    same rows and a corrected box score updates them. The caller commits.
    """
    upsert_games(session, [box])
    for line in box.lines:
        _merge_line(session, box.game_id, line, source)
    session.flush()
    return len(box.lines)
