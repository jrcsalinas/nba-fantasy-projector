"""roster and roster history

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-29

`roster` holds one row per owner and player; `roster_history` is the append-only record of
every add and remove. Timestamps are plain DateTime columns holding naive UTC, like the
other tables; the application's UtcDateTime type adds the timezone on the way out.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the roster tables and the history index."""
    op.create_table(
        "roster",
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("player_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player.player_id"]),
        sa.PrimaryKeyConstraint("owner", "player_id"),
    )
    op.create_table(
        "roster_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["player.player_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_roster_history_owner_changed_at", "roster_history", ["owner", "changed_at"]
    )


def downgrade() -> None:
    """Drop the roster tables."""
    op.drop_index("ix_roster_history_owner_changed_at", table_name="roster_history")
    op.drop_table("roster_history")
    op.drop_table("roster")
