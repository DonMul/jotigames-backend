"""add blind hike finish radius config column

Revision ID: 20260322_0004
Revises: d105c260258d
Create Date: 2026-03-22 20:45:00.000000
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260322_0004"
down_revision: Optional[str] = "20260318_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "game" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("game")}
    if "blind_hike_finish_radius_meters" not in columns:
        op.add_column(
            "game",
            sa.Column("blind_hike_finish_radius_meters", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "game" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("game")}
    if "blind_hike_finish_radius_meters" in columns:
        op.drop_column("game", "blind_hike_finish_radius_meters")
