"""calendar token hash

Keep only the SHA-256 of the calendar feed token. Existing tokens are hashed in place, so
addresses already in people's calendar apps keep working.

Revision ID: 5b1e0c3d9a72
Revises: c8cd44592fe4
Create Date: 2026-11-23 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b1e0c3d9a72"
down_revision: str | None = "c8cd44592fe4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("live_settings", "calendar_token", new_column_name="calendar_token_hash")
    op.execute(
        "UPDATE live_settings SET calendar_token_hash = "
        "encode(sha256(convert_to(calendar_token_hash, 'UTF8')), 'hex') "
        "WHERE calendar_token_hash IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE live_settings RENAME CONSTRAINT uq_live_settings_calendar_token "
        "TO uq_live_settings_calendar_token_hash"
    )
    op.add_column(
        "live_settings",
        sa.Column("calendar_created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # The tokens can't be recovered from their hashes: everyone needs a new address.
    op.drop_column("live_settings", "calendar_created_at")
    op.execute(
        "ALTER TABLE live_settings RENAME CONSTRAINT uq_live_settings_calendar_token_hash "
        "TO uq_live_settings_calendar_token"
    )
    op.execute("UPDATE live_settings SET calendar_token_hash = NULL")
    op.alter_column("live_settings", "calendar_token_hash", new_column_name="calendar_token")
