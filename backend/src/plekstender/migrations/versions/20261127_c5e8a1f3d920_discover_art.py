"""discover art: fanart.tv artwork of the artists suggested on Discover

Revision ID: c5e8a1f3d920
Revises: b8d3f2a6c914
Create Date: 2026-11-27 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e8a1f3d920"
down_revision: str | None = "b8d3f2a6c914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discover_art",
        sa.Column("artist_key", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("mbid", sa.String(length=36), nullable=True),
        sa.Column("background", sa.Boolean(), nullable=False),
        sa.Column("banner", sa.Boolean(), nullable=False),
        sa.Column("logo", sa.Boolean(), nullable=False),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("artist_key"),
    )


def downgrade() -> None:
    op.drop_table("discover_art")
