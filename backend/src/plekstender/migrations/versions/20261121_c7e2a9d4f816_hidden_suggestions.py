"""hidden artist suggestions

Revision ID: c7e2a9d4f816
Revises: a4d1e7c3b902
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e2a9d4f816"
down_revision: str | None = "a4d1e7c3b902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hidden_suggestions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("artist_key", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "hidden_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_hidden_suggestions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "artist_key", name=op.f("pk_hidden_suggestions")),
    )


def downgrade() -> None:
    op.drop_table("hidden_suggestions")
