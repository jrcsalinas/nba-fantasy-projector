"""index game_log by player

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-28

The primary key is (game_id, player_id), so it cannot serve "all games for one
player", which is the query behind every projection. This adds the index for it.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the player_id index to game_log."""
    op.create_index("ix_game_log_player_id", "game_log", ["player_id"])


def downgrade() -> None:
    """Drop the player_id index from game_log."""
    op.drop_index("ix_game_log_player_id", table_name="game_log")
