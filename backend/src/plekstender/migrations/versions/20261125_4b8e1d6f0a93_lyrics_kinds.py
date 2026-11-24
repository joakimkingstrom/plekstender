"""lyrics kinds

The kind of lyrics each music file has (in its tags and overall, for Library -> Lyrics), and
LRCLIB's Lyricsfile for the lyrics found.

Revision ID: 4b8e1d6f0a93
Revises: a9d3f6b2c871
Create Date: 2026-11-25 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b8e1d6f0a93"
down_revision: str | None = "a9d3f6b2c871"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media_files", sa.Column("embedded_lyrics", sa.String(length=12), nullable=True))
    op.add_column("media_files", sa.Column("lyrics", sa.String(length=12), nullable=True))
    op.add_column("track_lyrics", sa.Column("lyricsfile", sa.Text(), nullable=True))
    op.add_column(
        "track_lyrics",
        sa.Column("word_synced", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("track_lyrics", sa.Column("existing_lyricsfile", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("track_lyrics", "existing_lyricsfile")
    op.drop_column("track_lyrics", "word_synced")
    op.drop_column("track_lyrics", "lyricsfile")
    op.drop_column("media_files", "lyrics")
    op.drop_column("media_files", "embedded_lyrics")
