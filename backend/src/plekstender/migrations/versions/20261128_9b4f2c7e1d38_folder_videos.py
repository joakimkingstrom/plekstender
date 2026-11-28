"""folder videos: video covers found in the music folder (cover.mp4...)

Revision ID: 9b4f2c7e1d38
Revises: c5e8a1f3d920
Create Date: 2026-11-28 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b4f2c7e1d38"
down_revision: str | None = "c5e8a1f3d920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "folder_videos",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mtime", sa.Float(), nullable=False),
        sa.Column("stored", sa.String(length=80), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["plex_servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "path"),
    )


def downgrade() -> None:
    op.drop_table("folder_videos")
