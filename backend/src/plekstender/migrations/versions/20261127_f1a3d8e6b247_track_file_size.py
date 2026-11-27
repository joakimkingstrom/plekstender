"""track file size

Each song's file size as Plex has it (filled in by the next full sync), for the size of the
library by type of file.

Revision ID: f1a3d8e6b247
Revises: e4b9c1d7a352
Create Date: 2026-11-27 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a3d8e6b247"
down_revision: str | None = "e4b9c1d7a352"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tracks", sa.Column("file_size", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("tracks", "file_size")
