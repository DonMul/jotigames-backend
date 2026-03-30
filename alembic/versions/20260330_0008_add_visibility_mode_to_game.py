"""Add GeoHunter visibility mode column to game table.

Revision ID: 20260330_0008
Revises: 20260330_0007
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "20260330_0008"
down_revision = "20260330_0007"
branch_labels = None
depends_on = None


_DEF_VALUE = "all_visible"


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _column_exists("game", "geo_hunter_visibility_mode"):
        op.add_column(
            "game",
            sa.Column(
                "geo_hunter_visibility_mode",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text(f"'{_DEF_VALUE}'"),
            ),
        )


def downgrade() -> None:
    if _column_exists("game", "geo_hunter_visibility_mode"):
        op.drop_column("game", "geo_hunter_visibility_mode")
