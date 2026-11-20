"""album colours

Revision ID: b2e8f0c4a913
Revises: 7c41a9e2d5b8
Create Date: 2026-11-20 20:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2e8f0c4a913"
down_revision: str | None = "7c41a9e2d5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "album_colours",
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("rating_key", sa.BigInteger(), nullable=False),
        sa.Column("thumb", sa.Text(), nullable=False),
        sa.Column("hex", sa.String(length=7), nullable=True),
        sa.Column("hue", sa.Float(), nullable=True),
        sa.Column("saturation", sa.Float(), nullable=True),
        sa.Column("lightness", sa.Float(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["plex_servers.id"],
            name=op.f("fk_album_colours_server_id_plex_servers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("server_id", "rating_key", name=op.f("pk_album_colours")),
    )


def downgrade() -> None:
    op.drop_table("album_colours")
