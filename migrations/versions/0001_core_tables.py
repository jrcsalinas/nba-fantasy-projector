"""core tables

Revision ID: 0001
Revises:
Create Date: 2026-10-27

Timestamps are plain DateTime columns holding naive UTC. The application's
UtcDateTime type adds the timezone on the way out, so migrations do not import
application code.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the core tables."""
    op.create_table(
        "team",
        sa.Column("team_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("tricode", sa.String(length=3), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("city", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("team_id"),
    )
    op.create_table(
        "player",
        sa.Column("player_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("player_id"),
    )
    op.create_table(
        "game",
        sa.Column("game_id", sa.String(length=10), nullable=False),
        sa.Column("season", sa.String(length=7), nullable=False),
        sa.Column("game_type", sa.String(length=32), nullable=False),
        sa.Column("tip_off_utc", sa.DateTime(), nullable=False),
        sa.Column("home_team_id", sa.Integer(), nullable=False),
        sa.Column("away_team_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["away_team_id"], ["team.team_id"]),
        sa.ForeignKeyConstraint(["home_team_id"], ["team.team_id"]),
        sa.PrimaryKeyConstraint("game_id"),
    )
    op.create_table(
        "game_log",
        sa.Column("game_id", sa.String(length=10), nullable=False),
        sa.Column("player_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Float(), nullable=False),
        sa.Column("pts", sa.Integer(), nullable=False),
        sa.Column("reb", sa.Integer(), nullable=False),
        sa.Column("ast", sa.Integer(), nullable=False),
        sa.Column("stl", sa.Integer(), nullable=False),
        sa.Column("blk", sa.Integer(), nullable=False),
        sa.Column("tpm", sa.Integer(), nullable=False),
        sa.Column("fgm", sa.Integer(), nullable=False),
        sa.Column("fga", sa.Integer(), nullable=False),
        sa.Column("ftm", sa.Integer(), nullable=False),
        sa.Column("fta", sa.Integer(), nullable=False),
        sa.Column("tov", sa.Integer(), nullable=False),
        sa.Column("usage_pct", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["game.game_id"]),
        sa.ForeignKeyConstraint(["player_id"], ["player.player_id"]),
        sa.ForeignKeyConstraint(["team_id"], ["team.team_id"]),
        sa.PrimaryKeyConstraint("game_id", "player_id"),
    )
    op.create_table(
        "refresh_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("rows_upserted", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "job_lock",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("holder", sa.String(length=64), nullable=False),
        sa.Column("locked_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    """Drop the core tables."""
    op.drop_table("job_lock")
    op.drop_table("refresh_log")
    op.drop_table("game_log")
    op.drop_table("game")
    op.drop_table("player")
    op.drop_table("team")
