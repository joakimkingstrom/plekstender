"""drop scanned_at

Written on every music-file scan but never read: the size and modification time already say
whether a file has changed.

Revision ID: 9d4f2a6b1c58
Revises: 5b1e0c3d9a72
Create Date: 2026-11-23 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9d4f2a6b1c58"
down_revision: str | None = "5b1e0c3d9a72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("media_files", "nfo_files", "folder_images")


def upgrade() -> None:
    for table in TABLES:
        op.drop_column(table, "scanned_at")


def downgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "scanned_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
