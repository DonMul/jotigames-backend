"""add principal columns to api_auth_tokens

Revision ID: 20260318_0002
Revises: 20260318_0001
Create Date: 2026-03-18 00:30:00.000000
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260318_0002"
down_revision: Optional[str] = "20260318_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("api_auth_tokens")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("api_auth_tokens")}

    if "principal_type" not in existing_columns:
        op.add_column(
            "api_auth_tokens",
            sa.Column("principal_type", sa.String(length=16), nullable=False, server_default="user"),
        )

    if "principal_id" not in existing_columns:
        op.add_column(
            "api_auth_tokens",
            sa.Column("principal_id", sa.String(length=255), nullable=True),
        )

    op.execute(sa.text("UPDATE api_auth_tokens SET principal_id = user_id WHERE principal_id IS NULL"))

    if bind.dialect.name != "sqlite":
        op.alter_column("api_auth_tokens", "principal_id", existing_type=sa.String(length=255), nullable=False)

    if "ix_api_auth_tokens_principal_type" not in existing_indexes:
        op.create_index("ix_api_auth_tokens_principal_type", "api_auth_tokens", ["principal_type"])
    if "ix_api_auth_tokens_principal_id" not in existing_indexes:
        op.create_index("ix_api_auth_tokens_principal_id", "api_auth_tokens", ["principal_id"])
    if "ix_api_auth_tokens_principal_expires" not in existing_indexes:
        op.create_index(
            "ix_api_auth_tokens_principal_expires",
            "api_auth_tokens",
            ["principal_type", "principal_id", "expires_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("api_auth_tokens")}
    existing_indexes = {index["name"] for index in inspector.get_indexes("api_auth_tokens")}

    if "ix_api_auth_tokens_principal_expires" in existing_indexes:
        op.drop_index("ix_api_auth_tokens_principal_expires", table_name="api_auth_tokens")
    if "ix_api_auth_tokens_principal_id" in existing_indexes:
        op.drop_index("ix_api_auth_tokens_principal_id", table_name="api_auth_tokens")
    if "ix_api_auth_tokens_principal_type" in existing_indexes:
        op.drop_index("ix_api_auth_tokens_principal_type", table_name="api_auth_tokens")

    if "principal_id" in existing_columns:
        op.drop_column("api_auth_tokens", "principal_id")
    if "principal_type" in existing_columns:
        op.drop_column("api_auth_tokens", "principal_type")
