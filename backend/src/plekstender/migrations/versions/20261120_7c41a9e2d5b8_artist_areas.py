"""artist areas

Revision ID: 7c41a9e2d5b8
Revises: 3ffdbe5d1174
Create Date: 2026-11-20 20:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c41a9e2d5b8"
down_revision: str | None = "3ffdbe5d1174"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artist_areas",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_artist_areas")),
    )


def downgrade() -> None:
    op.drop_table("artist_areas")
