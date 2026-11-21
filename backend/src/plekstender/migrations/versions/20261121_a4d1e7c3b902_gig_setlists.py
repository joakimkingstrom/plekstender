"""Gig setlists: what each artist played, for "seen live".

Revision ID: a4d1e7c3b902
Revises: f3c9a1b7d264
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d1e7c3b902"
down_revision: str | None = "f3c9a1b7d264"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gigs",
        sa.Column(
            "setlists",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("gigs", "setlists")
