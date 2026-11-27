"""stop requested

Long jobs can be stopped (or cancelled) from the app: artwork jobs, file scans, edits being
written, and history imports and sends.

Revision ID: d2a6f8b4e913
Revises: 8e3c6a1f5d27
Create Date: 2026-11-27 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d2a6f8b4e913"
down_revision: str | None = "8e3c6a1f5d27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("artwork_jobs", "file_scans", "tag_change_sets", "listen_imports")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("stop_requested", sa.String(length=8), nullable=True))
    # What an artwork job replaced, to put back when it's cancelled.
    op.add_column(
        "artwork_jobs",
        sa.Column(
            "undo",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("artwork_jobs", "undo")
    for table in TABLES:
        op.drop_column(table, "stop_requested")
