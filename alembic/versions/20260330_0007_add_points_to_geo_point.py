"""Add points column to geo_point table.

Revision ID: 20260330_0007
Revises: 20260326_0006
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "20260330_0007"
down_revision = "20260326_0006"
branch_labels = None
depends_on = None


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _column_exists("geo_point", "points"):
        op.add_column(
            "geo_point",
            sa.Column("points", sa.Integer(), nullable=False, server_default=sa.text("1")),
        )


def downgrade() -> None:
    if _column_exists("geo_point", "points"):
        op.drop_column("geo_point", "points")
