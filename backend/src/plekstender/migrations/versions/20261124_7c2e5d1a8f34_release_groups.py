"""release groups

Artists' release groups from MusicBrainz, when each artist was last checked, and the ones
someone has hidden (Library -> Releases).

Revision ID: 7c2e5d1a8f34
Revises: 3f7a2c9e4b10
Create Date: 2026-11-24 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c2e5d1a8f34"
down_revision: str | None = "3f7a2c9e4b10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "release_groups",
        sa.Column("artist_mbid", sa.String(length=36), nullable=False),
        sa.Column("mbid", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_norm", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("primary_type", sa.String(length=32), nullable=True),
        sa.Column("secondary_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("first_release_date", sa.String(length=10), nullable=True),
        sa.Column("released", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("artist_mbid", "mbid"),
    )
    op.create_index("ix_release_groups_mbid", "release_groups", ["mbid"])
    op.create_index("ix_release_groups_released", "release_groups", ["released"])
    op.create_table(
        "artist_release_checks",
        sa.Column("artist_mbid", sa.String(length=36), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("found", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("artist_mbid"),
    )
    op.create_table(
        "hidden_releases",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mbid", sa.String(length=36), nullable=False),
        sa.Column(
            "hidden_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "mbid"),
    )


def downgrade() -> None:
    op.drop_table("hidden_releases")
    op.drop_table("artist_release_checks")
    op.drop_index("ix_release_groups_released", table_name="release_groups")
    op.drop_index("ix_release_groups_mbid", table_name="release_groups")
    op.drop_table("release_groups")
