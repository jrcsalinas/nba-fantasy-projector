"""SQLite storage for game logs and daily ranking snapshots."""

import logging
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import fields
from datetime import date
from pathlib import Path

from nba_projector.domain.models import (
    SORT_KEYS,
    Category,
    GameLog,
    PlayerAverages,
    RankedPlayer,
    Window,
)

logger = logging.getLogger(__name__)

_LOG_COLUMNS = [f.name for f in fields(GameLog)]
_Z_COLUMNS = [f"z_{cat.value}" for cat in Category]
_RANK_COLUMNS = [f"rank_{key}" for key in SORT_KEYS]
_AVG_RATE_FIELDS = ("pts", "reb", "ast", "stl", "blk", "fg3m", "fgm", "fga", "ftm", "fta", "tov")
# Nullable so snapshots saved before per-game stats were stored still load.
_STAT_COLUMN_TYPES = {
    "games_played": "INTEGER",
    "avg_games": "INTEGER",
    "avg_minutes_pg": "REAL",
    **{f"avg_{field}": "REAL" for field in _AVG_RATE_FIELDS},
}
_STAT_COLUMNS = list(_STAT_COLUMN_TYPES)
_SNAPSHOT_COLUMNS = [
    "snapshot_date",
    "window",
    "player_id",
    "player_name",
    "total",
    *_Z_COLUMNS,
    *_RANK_COLUMNS,
    *_STAT_COLUMNS,
]

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS game_logs (
    season TEXT NOT NULL,
    game_id TEXT NOT NULL,
    game_date TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    minutes REAL NOT NULL,
    pts INTEGER NOT NULL, reb INTEGER NOT NULL, ast INTEGER NOT NULL,
    stl INTEGER NOT NULL, blk INTEGER NOT NULL, fg3m INTEGER NOT NULL,
    fgm INTEGER NOT NULL, fga INTEGER NOT NULL, ftm INTEGER NOT NULL,
    fta INTEGER NOT NULL, tov INTEGER NOT NULL,
    PRIMARY KEY (player_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_game_logs_season ON game_logs (season);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_date TEXT NOT NULL,
    window TEXT NOT NULL,
    player_id INTEGER NOT NULL,
    player_name TEXT NOT NULL,
    total REAL NOT NULL,
    {", ".join(f"{c} REAL NOT NULL" for c in _Z_COLUMNS)},
    {", ".join(f"{c} INTEGER NOT NULL" for c in _RANK_COLUMNS)},
    {", ".join(f"{c} {t}" for c, t in _STAT_COLUMN_TYPES.items())},
    PRIMARY KEY (snapshot_date, window, player_id)
);
"""


def _placeholders(columns: Sequence[str]) -> str:
    return ", ".join("?" for _ in columns)


def _log_row(log: GameLog) -> tuple[object, ...]:
    return tuple(
        log.game_date.isoformat() if col == "game_date" else getattr(log, col)
        for col in _LOG_COLUMNS
    )


def _to_game_log(row: Sequence[object]) -> GameLog:
    values = dict(zip(_LOG_COLUMNS, row, strict=True))
    values["game_date"] = date.fromisoformat(str(values["game_date"]))
    return GameLog(**values)  # type: ignore[arg-type]


def _stat_values(player: RankedPlayer) -> tuple[object, ...]:
    averages = player.averages
    if averages is None:
        return (player.games_played, *([None] * (len(_STAT_COLUMNS) - 1)))
    return (
        player.games_played,
        averages.games,
        averages.minutes_pg,
        *(getattr(averages, field) for field in _AVG_RATE_FIELDS),
    )


def _snapshot_row(snapshot_date: date, window: Window, player: RankedPlayer) -> tuple[object, ...]:
    return (
        snapshot_date.isoformat(),
        window.value,
        player.player_id,
        player.player_name,
        player.total,
        *(player.z[cat] for cat in Category),
        *(player.ranks[key] for key in SORT_KEYS),
        *_stat_values(player),
    )


def _to_ranked_player(row: Sequence[object]) -> RankedPlayer:
    values = dict(zip(_SNAPSHOT_COLUMNS, row, strict=True))
    averages = None
    if values["avg_pts"] is not None:
        averages = PlayerAverages(
            player_id=values["player_id"],  # type: ignore[arg-type]
            player_name=values["player_name"],  # type: ignore[arg-type]
            games=values["avg_games"],  # type: ignore[arg-type]
            minutes_pg=values["avg_minutes_pg"],  # type: ignore[arg-type]
            **{field: values[f"avg_{field}"] for field in _AVG_RATE_FIELDS},  # type: ignore[arg-type]
        )
    return RankedPlayer(
        player_id=values["player_id"],  # type: ignore[arg-type]
        player_name=values["player_name"],  # type: ignore[arg-type]
        total=values["total"],  # type: ignore[arg-type]
        z={cat: values[f"z_{cat.value}"] for cat in Category},  # type: ignore[misc]
        ranks={key: values[f"rank_{key}"] for key in SORT_KEYS},  # type: ignore[misc]
        averages=averages,
        games_played=values["games_played"],  # type: ignore[arg-type]
    )


class Repository:
    """SQLite-backed store; each call uses a short-lived connection."""

    def __init__(self, db_path: Path) -> None:
        """Create the database file, parent directory, and schema if missing.

        Args:
            db_path: Location of the SQLite file.
        """
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._add_missing_stat_columns(conn)

    @staticmethod
    def _add_missing_stat_columns(conn: sqlite3.Connection) -> None:
        """Upgrade a snapshots table created before per-game stats were stored."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(snapshots)")}
        for column, column_type in _STAT_COLUMN_TYPES.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE snapshots ADD COLUMN {column} {column_type}")
                logger.info("Added snapshots.%s", column)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection that commits on success, rolls back on error, then closes."""
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            yield conn

    def save_game_logs(self, logs: Sequence[GameLog]) -> None:
        """Upsert game logs on ``(player_id, game_id)``."""
        sql = (
            f"INSERT OR REPLACE INTO game_logs ({', '.join(_LOG_COLUMNS)}) "
            f"VALUES ({_placeholders(_LOG_COLUMNS)})"
        )
        rows = [_log_row(log) for log in logs]
        with self._connect() as conn:
            conn.executemany(sql, rows)
        logger.info("Saved %d game logs", len(rows))

    def load_game_logs(self, seasons: Sequence[str]) -> list[GameLog]:
        """Load all game logs for the given seasons."""
        if not seasons:
            return []
        sql = (
            f"SELECT {', '.join(_LOG_COLUMNS)} FROM game_logs "
            f"WHERE season IN ({_placeholders(seasons)}) ORDER BY game_date, game_id, player_id"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, list(seasons)).fetchall()
        return [_to_game_log(row) for row in rows]

    def replace_snapshots(
        self, snapshot_date: date, snapshots: Mapping[Window, Sequence[RankedPlayer]]
    ) -> None:
        """Replace one date's snapshots for each given window in a single transaction.

        Args:
            snapshot_date: The game date being saved.
            snapshots: Ranked players per window; every window listed is fully replaced.

        Raises:
            KeyError: If a player's ``z`` or ``ranks`` is missing a key; nothing is changed.
        """
        insert = (
            f"INSERT INTO snapshots ({', '.join(_SNAPSHOT_COLUMNS)}) "
            f"VALUES ({_placeholders(_SNAPSHOT_COLUMNS)})"
        )
        with self._connect() as conn:
            for window, players in snapshots.items():
                conn.execute(
                    "DELETE FROM snapshots WHERE snapshot_date = ? AND window = ?",
                    (snapshot_date.isoformat(), window.value),
                )
                conn.executemany(insert, [_snapshot_row(snapshot_date, window, p) for p in players])
        logger.info(
            "Saved snapshot %s: %s",
            snapshot_date,
            {w.value: len(p) for w, p in snapshots.items()},
        )

    def snapshot_dates(self, window: Window, limit: int = 2) -> list[date]:
        """Return the newest snapshot dates for a window, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT snapshot_date FROM snapshots WHERE window = ? "
                "ORDER BY snapshot_date DESC LIMIT ?",
                (window.value, limit),
            ).fetchall()
        return [date.fromisoformat(r[0]) for r in rows]

    def load_snapshot(self, snapshot_date: date, window: Window) -> list[RankedPlayer]:
        """Load one snapshot ordered by overall rank; empty if none exists."""
        sql = (
            f"SELECT {', '.join(_SNAPSHOT_COLUMNS)} FROM snapshots "
            "WHERE snapshot_date = ? AND window = ? ORDER BY rank_overall, player_id"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, (snapshot_date.isoformat(), window.value)).fetchall()
        return [_to_ranked_player(row) for row in rows]
