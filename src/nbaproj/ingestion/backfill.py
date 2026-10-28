"""Season backfill from ``LeagueGameLog`` (stats host): last season's lines for the prior.

Backfilled minutes are whole numbers (the live feed has decimals), so usage derived
here is slightly coarser than usage derived from live box scores.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from nbaproj.db.models import Game, GameLog, Player
from nbaproj.ingestion.client import StatsClient
from nbaproj.ingestion.loader import SOURCE_LIVE, chunked, game_log_row
from nbaproj.ingestion.parsing import (
    PLAYER_NAME_LENGTH,
    PlayerLine,
    limited_text,
    malformed_as_payload_error,
    parse_game_id,
)
from nbaproj.ingestion.runs import Job, Lease, RunResult, guarded, utc_now
from nbaproj.ingestion.schedule import parse_schedule, store_schedule
from nbaproj.ingestion.usage import Totals, usage_pct

SOURCE_BACKFILL = "backfill"
BACKFILL_LOCK_TTL = timedelta(minutes=30)


def parse_result_set_rows(payload: dict) -> list[dict]:
    """Turn the first result set of a stats payload into one dict per row."""
    with malformed_as_payload_error("game log payload"):
        result = payload["resultSets"][0]
        headers = result["headers"]
        return [dict(zip(headers, row, strict=True)) for row in result["rowSet"]]


def _totals(row: dict) -> Totals:
    return Totals(
        minutes=float(row["MIN"]),
        fga=int(row["FGA"]),
        fta=int(row["FTA"]),
        tov=int(row["TOV"]),
    )


def _team_totals(team_payload: dict) -> dict[tuple[str, int], Totals]:
    totals = {}
    for row in parse_result_set_rows(team_payload):
        where = f"team game log row (game {row.get('GAME_ID')}, team {row.get('TEAM_ID')})"
        with malformed_as_payload_error(where):
            totals[(row["GAME_ID"], int(row["TEAM_ID"]))] = _totals(row)
    return totals


def _line(row: dict, team: Totals | None) -> PlayerLine:
    player = _totals(row)
    return PlayerLine(
        player_id=int(row["PLAYER_ID"]),
        name=limited_text(row["PLAYER_NAME"], PLAYER_NAME_LENGTH, "player name"),
        team_id=int(row["TEAM_ID"]),
        minutes=player.minutes,
        pts=int(row["PTS"]),
        reb=int(row["REB"]),
        ast=int(row["AST"]),
        stl=int(row["STL"]),
        blk=int(row["BLK"]),
        tpm=int(row["FG3M"]),
        fgm=int(row["FGM"]),
        fga=player.fga,
        ftm=int(row["FTM"]),
        fta=player.fta,
        tov=player.tov,
        usage_pct=None if team is None else usage_pct(player, team),
    )


def build_backfill_lines(
    player_payload: dict, team_payload: dict
) -> dict[str, list[PlayerLine]]:
    """Group player lines by game id, deriving usage from the matching team totals.

    A line whose team row is missing keeps no usage rather than guessing.
    """
    teams = _team_totals(team_payload)
    by_game: dict[str, dict[int, PlayerLine]] = {}
    for row in parse_result_set_rows(player_payload):
        where = f"game log row (game {row.get('GAME_ID')}, player {row.get('PLAYER_ID')})"
        with malformed_as_payload_error(where):
            game_id = parse_game_id(row["GAME_ID"])
            team = teams.get((game_id, int(row["TEAM_ID"])))
            line = _line(row, team)
            by_game.setdefault(game_id, {})[line.player_id] = line  # last duplicate wins
    return {game_id: list(lines.values()) for game_id, lines in by_game.items()}


@dataclass(frozen=True)
class BackfillStored:
    """What a backfill store did, in lines."""

    lines_stored: int = 0
    lines_skipped_unscheduled: int = 0
    lines_skipped_live: int = 0


def _scheduled_game_ids(session: Session, game_ids: list[str]) -> set[str]:
    found: set[str] = set()
    for chunk in chunked(game_ids):
        found.update(session.scalars(select(Game.game_id).where(Game.game_id.in_(chunk))))
    return found


def _live_keys(session: Session, game_ids: list[str]) -> set[tuple[str, int]]:
    keys: set[tuple[str, int]] = set()
    for chunk in chunked(game_ids):
        rows = session.execute(
            select(GameLog.game_id, GameLog.player_id).where(
                GameLog.game_id.in_(chunk), GameLog.source == SOURCE_LIVE
            )
        )
        keys.update((game_id, player_id) for game_id, player_id in rows)
    return keys


def _delete_backfill_lines(session: Session, game_ids: list[str]) -> None:
    for chunk in chunked(game_ids):
        session.execute(
            delete(GameLog).where(
                GameLog.game_id.in_(chunk), GameLog.source == SOURCE_BACKFILL
            )
        )


def _add_new_players(session: Session, lines: list[PlayerLine]) -> None:
    names = {line.player_id: line.name for line in lines}
    known: set[int] = set()
    for chunk in chunked(list(names)):
        known.update(session.scalars(select(Player.player_id).where(Player.player_id.in_(chunk))))
    session.add_all(
        Player(player_id=player_id, full_name=name)
        for player_id, name in names.items()
        if player_id not in known
    )
    session.flush()


def store_backfill(
    session: Session, lines_by_game: dict[str, list[PlayerLine]]
) -> BackfillStored:
    """Bulk-store backfill lines for games already in the schedule. The caller commits.

    Idempotent: this source's earlier lines for these games are replaced. Live lines
    (decimal minutes) are never touched, and lines for games missing from the schedule
    are skipped and counted.
    """
    scheduled = _scheduled_game_ids(session, list(lines_by_game))
    unscheduled = sum(len(v) for g, v in lines_by_game.items() if g not in scheduled)
    game_ids = [g for g in lines_by_game if g in scheduled]
    live = _live_keys(session, game_ids)
    keep = [
        (game_id, line)
        for game_id in game_ids
        for line in lines_by_game[game_id]
        if (game_id, line.player_id) not in live
    ]
    _delete_backfill_lines(session, game_ids)
    _add_new_players(session, [line for _, line in keep])
    session.add_all(game_log_row(game_id, line, SOURCE_BACKFILL) for game_id, line in keep)
    session.flush()
    skipped_live = sum(len(lines_by_game[g]) for g in game_ids) - len(keep)
    return BackfillStored(len(keep), unscheduled, skipped_live)


@dataclass(frozen=True)
class BackfillResult(RunResult):
    """What a season backfill run did."""

    games_scheduled: int = 0
    lines_stored: int = 0
    lines_skipped_unscheduled: int = 0
    lines_skipped_live: int = 0


BACKFILL_JOB = Job("backfill", BackfillResult, ttl=BACKFILL_LOCK_TTL)


def _run_season(
    session: Session, client: StatsClient, season: str, lease: Lease
) -> BackfillResult:
    """Fetch and parse everything, then store it in one short transaction.

    Nothing is written until every payload is in hand, so no write transaction is held
    open (blocking other writers) during the slow network calls. A failure while writing
    rolls the whole season back.
    """
    games, stored = 0, BackfillStored()
    with guarded(session, season) as guard:
        schedule = parse_schedule(client.get_schedule(season))
        lines = build_backfill_lines(
            client.get_player_game_log(season), client.get_team_game_log(season)
        )
        if not schedule:
            return BackfillResult(
                status="failed",
                message=f"{season}: the schedule is empty (wrong season or an empty response)",
            )
        if not lines:
            return BackfillResult(
                status="skipped", message=f"{season}: no player lines yet, so nothing to backfill"
            )
        if not lease.renew():
            return BackfillResult(
                status="failed",
                message=f"{season}: lost the lock to another writer; nothing was written",
            )
        games = store_schedule(session, schedule)
        stored = store_backfill(session, lines)
        session.commit()
    if guard.error:
        return BackfillResult(status="failed", message=guard.error)
    return BackfillResult(
        status="succeeded",
        rows_upserted=games + stored.lines_stored,
        message=(
            f"{season}: {games} games scheduled, {stored.lines_stored} lines stored, "
            f"{stored.lines_skipped_unscheduled} skipped (game not in schedule), "
            f"{stored.lines_skipped_live} skipped (live line kept)"
        ),
        games_scheduled=games,
        lines_stored=stored.lines_stored,
        lines_skipped_unscheduled=stored.lines_skipped_unscheduled,
        lines_skipped_live=stored.lines_skipped_live,
    )


def backfill_season(
    session: Session,
    client: StatsClient,
    season: str,
    trigger: str,
    clock: Callable[[], datetime] = utc_now,
) -> BackfillResult:
    """Backfill one regular season: the schedule first, then every player's lines.

    Runs under the shared lock and is recorded in the refresh log. The whole season is
    one transaction, so a failure leaves nothing half-loaded.
    """
    return BACKFILL_JOB.run(
        session, trigger, clock, lambda lease: _run_season(session, client, season, lease)
    )
