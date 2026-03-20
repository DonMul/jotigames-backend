"""create game_chat_message table

Revision ID: 20260318_0003
Revises: 20260318_0002
Create Date: 2026-03-18 23:40:00.000000
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260318_0003"
down_revision: Optional[str] = "20260318_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "game_chat_message" not in table_names:
        op.create_table(
            "game_chat_message",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("game_id", sa.String(length=36), nullable=False),
            sa.Column("author_role", sa.String(length=16), nullable=False),
            sa.Column("author_label", sa.String(length=120), nullable=False),
            sa.Column("author_team_id", sa.String(length=36), nullable=True),
            sa.Column("author_user_id", sa.String(length=255), nullable=True),
            sa.Column("author_logo_path", sa.String(length=255), nullable=True),
            sa.Column("author_session_id", sa.String(length=255), nullable=True),
            sa.Column("message", sa.String(length=512), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    existing_indexes = {index["name"] for index in inspector.get_indexes("game_chat_message")} if "game_chat_message" in set(inspector.get_table_names()) else set()

    if "ix_game_chat_message_game_created" not in existing_indexes:
        op.create_index(
            "ix_game_chat_message_game_created",
            "game_chat_message",
            ["game_id", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "game_chat_message" not in table_names:
        return

    existing_indexes = {index["name"] for index in inspector.get_indexes("game_chat_message")}
    if "ix_game_chat_message_game_created" in existing_indexes:
        op.drop_index("ix_game_chat_message_game_created", table_name="game_chat_message")

    op.drop_table("game_chat_message")
