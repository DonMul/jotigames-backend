"""Add is_default column to subscription_plan table.

Revision ID: 20260326_0006
Revises: 20260325_0005
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa

revision = "20260326_0006"
down_revision = "20260325_0005"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    """Check whether *column* already exists on *table* (works on MySQL & SQLite)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _column_exists("subscription_plan", "is_default"):
        op.add_column(
            "subscription_plan",
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    if _column_exists("subscription_plan", "is_default"):
        op.drop_column("subscription_plan", "is_default")
