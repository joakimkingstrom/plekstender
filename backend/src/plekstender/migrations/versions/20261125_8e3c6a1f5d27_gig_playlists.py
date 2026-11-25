"""gig playlists

Playlists made for gigs: before a starred one, after one the user went to.

Revision ID: 8e3c6a1f5d27
Revises: c5f7a3e9d214
Create Date: 2026-11-25 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e3c6a1f5d27"
down_revision: str | None = "c5f7a3e9d214"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gig_playlists",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("ref", sa.String(length=128), nullable=False),
        sa.Column("playlist_id", sa.Integer(), nullable=True),
        sa.Column("gig_date", sa.Date(), nullable=False),
        sa.Column("songs", sa.Integer(), nullable=False),
        sa.Column("made_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["playlist_id"], ["playlists.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "kind", "ref"),
    )


def downgrade() -> None:
    op.drop_table("gig_playlists")
