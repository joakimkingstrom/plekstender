"""cover colour method

Album cover colours are worked out again with a better method (white covers with dark text
were black, cream ones red), which also notes how light the whole cover is.

Revision ID: e4b9c1d7a352
Revises: d2a6f8b4e913
Create Date: 2026-11-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4b9c1d7a352"
down_revision: str | None = "d2a6f8b4e913"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("album_colours", sa.Column("brightness", sa.Float(), nullable=True))
    op.add_column(
        "album_colours",
        sa.Column("method", sa.SmallInteger(), server_default="1", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("album_colours", "method")
    op.drop_column("album_colours", "brightness")
