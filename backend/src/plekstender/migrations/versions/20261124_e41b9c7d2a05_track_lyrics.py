"""track lyrics

Lyrics found for music files on LRCLIB, and what each file already had (Library -> Tag
editing -> Lyrics).

Revision ID: e41b9c7d2a05
Revises: 7c2e5d1a8f34
Create Date: 2026-11-24 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e41b9c7d2a05"
down_revision: str | None = "7c2e5d1a8f34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "track_lyrics",
        sa.Column("media_file_id", sa.BigInteger(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("synced", sa.Text(), nullable=True),
        sa.Column("plain", sa.Text(), nullable=True),
        sa.Column("lrclib_id", sa.BigInteger(), nullable=True),
        sa.Column("existing_lrc", sa.Text(), nullable=True),
        sa.Column("existing_txt", sa.Text(), nullable=True),
        sa.Column("existing_embedded", sa.Text(), nullable=True),
        sa.Column("embeddable", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("looked_up_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["media_file_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_id"], ["plex_servers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("media_file_id"),
    )
    op.create_index("ix_track_lyrics_server", "track_lyrics", ["server_id"])


def downgrade() -> None:
    op.drop_index("ix_track_lyrics_server", table_name="track_lyrics")
    op.drop_table("track_lyrics")
