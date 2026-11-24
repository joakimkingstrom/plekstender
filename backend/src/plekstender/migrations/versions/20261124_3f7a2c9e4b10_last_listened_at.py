"""last listened at

A track's latest listen in the user's whole history (Plex plays and imports), for
History -> Rediscover and the forgotten favourites playlist.

Revision ID: 3f7a2c9e4b10
Revises: 9d4f2a6b1c58
Create Date: 2026-11-24 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f7a2c9e4b10"
down_revision: str | None = "9d4f2a6b1c58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_item_state", sa.Column("last_listened_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user_item_state", "last_listened_at")
