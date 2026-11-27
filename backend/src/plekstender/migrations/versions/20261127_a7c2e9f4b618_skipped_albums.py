"""skipped albums

Albums someone looked up on MusicBrainz in Tag editing and skipped, hidden from the list for
a while.

Revision ID: a7c2e9f4b618
Revises: f1a3d8e6b247
Create Date: 2026-11-27 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c2e9f4b618"
down_revision: str | None = "f1a3d8e6b247"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skipped_albums",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("rating_key", sa.BigInteger(), nullable=False),
        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"], ["plex_servers.id"],
            name=op.f("fk_skipped_albums_server_id_plex_servers"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_skipped_albums_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "server_id", "rating_key", name=op.f("pk_skipped_albums")
        ),
    )  # fmt: skip


def downgrade() -> None:
    op.drop_table("skipped_albums")
