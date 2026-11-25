"""file fingerprints

Music files' raw Chromaprint fingerprints, to find files that are the same recording
(Library -> Tag editing -> Duplicate files).

Revision ID: a9d3f6b2c871
Revises: e41b9c7d2a05
Create Date: 2026-11-24 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9d3f6b2c871"
down_revision: str | None = "e41b9c7d2a05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_fingerprints",
        sa.Column("media_file_id", sa.BigInteger(), nullable=False),
        sa.Column("file_key", sa.String(length=64), nullable=False),
        sa.Column("values", sa.ARRAY(sa.Integer()), nullable=False),
        sa.ForeignKeyConstraint(["media_file_id"], ["media_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("media_file_id"),
    )


def downgrade() -> None:
    op.drop_table("file_fingerprints")
