"""song covers: whether songs are covers, and of whose song (SecondHandSongs, MusicBrainz)

Revision ID: 7d2e9b4c1a65
Revises: c5e8a1f3d920
Create Date: 2026-11-28 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d2e9b4c1a65"
down_revision: str | None = "c5e8a1f3d920"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "song_covers",
        sa.Column("artist_key", sa.Text(), nullable=False),
        sa.Column("title_norm", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("original_artist", sa.Text(), nullable=True),
        sa.Column("original_title", sa.Text(), nullable=True),
        sa.Column("performance_url", sa.Text(), nullable=True),
        sa.Column("original_url", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("tried", sa.String(length=64), nullable=False),
        sa.Column("requests", sa.SmallInteger(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("artist_key", "title_norm"),
    )
    op.create_index("ix_song_covers_checked", "song_covers", ["checked_at"])


def downgrade() -> None:
    op.drop_index("ix_song_covers_checked", table_name="song_covers")
    op.drop_table("song_covers")
