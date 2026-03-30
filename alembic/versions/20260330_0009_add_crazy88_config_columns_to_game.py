"""Add Crazy88 config columns to game table.

Revision ID: 20260330_0009
Revises: 20260330_0008
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "20260330_0009"
down_revision = "20260330_0008"
branch_labels = None
depends_on = None


_VISIBILITY_DEFAULT = "all_visible"


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    if not _column_exists("game", "crazy88_visibility_mode"):
        op.add_column(
            "game",
            sa.Column(
                "crazy88_visibility_mode",
                sa.String(length=32),
                nullable=False,
                server_default=sa.text(f"'{_VISIBILITY_DEFAULT}'"),
            ),
        )

    if not _column_exists("game", "crazy88_show_highscore"):
        op.add_column(
            "game",
            sa.Column(
                "crazy88_show_highscore",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            ),
        )


def downgrade() -> None:
    if _column_exists("game", "crazy88_show_highscore"):
        op.drop_column("game", "crazy88_show_highscore")

    if _column_exists("game", "crazy88_visibility_mode"):
        op.drop_column("game", "crazy88_visibility_mode")
