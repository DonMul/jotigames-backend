"""create api_auth_tokens table

Revision ID: 20260318_0001
Revises:
Create Date: 2026-03-18 00:00:00.000000
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260318_0001"
down_revision: Optional[str] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_auth_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=False), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )

    op.create_index("ix_api_auth_tokens_user_id", "api_auth_tokens", ["user_id"])
    op.create_index("ix_api_auth_tokens_token_hash", "api_auth_tokens", ["token_hash"])
    op.create_index("ix_api_auth_tokens_issued_at", "api_auth_tokens", ["issued_at"])
    op.create_index("ix_api_auth_tokens_expires_at", "api_auth_tokens", ["expires_at"])
    op.create_index(
        "ix_api_auth_tokens_user_expires",
        "api_auth_tokens",
        ["user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_api_auth_tokens_user_expires", table_name="api_auth_tokens")
    op.drop_index("ix_api_auth_tokens_expires_at", table_name="api_auth_tokens")
    op.drop_index("ix_api_auth_tokens_issued_at", table_name="api_auth_tokens")
    op.drop_index("ix_api_auth_tokens_token_hash", table_name="api_auth_tokens")
    op.drop_index("ix_api_auth_tokens_user_id", table_name="api_auth_tokens")
    op.drop_table("api_auth_tokens")
