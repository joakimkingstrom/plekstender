"""hidden songs

Songs someone has hidden from History -> Rediscover.

Revision ID: c5f7a3e9d214
Revises: 4b8e1d6f0a93
Create Date: 2026-11-25 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5f7a3e9d214"
down_revision: str | None = "4b8e1d6f0a93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hidden_songs",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("rating_key", sa.BigInteger(), nullable=False),
        sa.Column(
            "hidden_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["server_id"], ["plex_servers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "server_id", "rating_key"),
    )


def downgrade() -> None:
    op.drop_table("hidden_songs")
