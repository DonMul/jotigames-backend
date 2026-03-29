"""create monetisation tables

Revision ID: 20260325_0005
Revises: 20260322_0004
Create Date: 2026-03-25 00:00:00.000000
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260325_0005"
down_revision: Optional[str] = "20260322_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    """Check if a table already exists in the database."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return name in insp.get_table_names()


def _table_has_column(table: str, column: str) -> bool:
    """Check if a column exists on an existing table."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    # ── Subscription plan tiers (managed by super-admin) ─────────────────
    if not _table_exists("subscription_plan"):
        op.create_table(
            "subscription_plan",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("slug", sa.String(64), nullable=False, unique=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("monthly_minutes", sa.Integer(), nullable=True),  # NULL = unlimited
            sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
            sa.Column("stripe_price_id", sa.String(255), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index("ix_subscription_plan_slug", "subscription_plan", ["slug"], unique=True)
        op.create_index("ix_subscription_plan_active", "subscription_plan", ["is_active"])

    # ── User subscriptions ───────────────────────────────────────────────
    # Drop legacy table from old PHP/Doctrine schema if it exists with wrong columns
    if _table_exists("user_subscription") and not _table_has_column("user_subscription", "plan_id"):
        op.drop_table("user_subscription")

    if not _table_exists("user_subscription"):
        op.create_table(
            "user_subscription",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(255), nullable=False, index=True),
            sa.Column("plan_id", sa.String(36), nullable=False, index=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            # active | past_due | cancelled | expired
            sa.Column("stripe_customer_id", sa.String(255), nullable=True),
            sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
            sa.Column("current_period_start", sa.DateTime(timezone=False), nullable=False),
            sa.Column("current_period_end", sa.DateTime(timezone=False), nullable=False),
            sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("cancelled_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index("ix_user_subscription_user_status", "user_subscription", ["user_id", "status"])
        op.create_index("ix_user_subscription_stripe_sub", "user_subscription", ["stripe_subscription_id"])
        op.create_index("ix_user_subscription_period_end", "user_subscription", ["current_period_end"])

    # ── Monthly minute balance per user ──────────────────────────────────
    if not _table_exists("minute_balance"):
        op.create_table(
            "minute_balance",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(255), nullable=False),
            sa.Column("period_year", sa.Integer(), nullable=False),
            sa.Column("period_month", sa.Integer(), nullable=False),
            sa.Column("minutes_allocated", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("minutes_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_unlimited", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index(
            "ix_minute_balance_user_period",
            "minute_balance",
            ["user_id", "period_year", "period_month"],
            unique=True,
        )

    # ── One-off minute top-up packages (super-admin managed) ─────────────
    if not _table_exists("minute_topup_package"):
        op.create_table(
            "minute_topup_package",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("minutes", sa.Integer(), nullable=False),
            sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
            sa.Column("stripe_price_id", sa.String(255), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
        )

    # ── Purchased top-up minutes (12-month lifetime) ─────────────────────
    if not _table_exists("minute_topup_purchase"):
        op.create_table(
            "minute_topup_purchase",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(255), nullable=False, index=True),
            sa.Column("package_id", sa.String(36), nullable=True),
            sa.Column("minutes_total", sa.Integer(), nullable=False),
            sa.Column("minutes_remaining", sa.Integer(), nullable=False),
            sa.Column("stripe_payment_intent_id", sa.String(255), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=False), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index("ix_minute_topup_purchase_user_expires", "minute_topup_purchase", ["user_id", "expires_at"])

    # ── Payment / invoice history ────────────────────────────────────────
    if not _table_exists("payment_record"):
        op.create_table(
            "payment_record",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(255), nullable=False, index=True),
            sa.Column("type", sa.String(32), nullable=False),
            # subscription | topup | refund
            sa.Column("amount_cents", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
            sa.Column("status", sa.String(32), nullable=False, server_default="succeeded"),
            # succeeded | pending | failed | refunded
            sa.Column("stripe_payment_intent_id", sa.String(255), nullable=True),
            sa.Column("stripe_invoice_id", sa.String(255), nullable=True),
            sa.Column("subscription_id", sa.String(36), nullable=True),
            sa.Column("topup_purchase_id", sa.String(36), nullable=True),
            sa.Column("description", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index("ix_payment_record_user_created", "payment_record", ["user_id", "created_at"])
        op.create_index("ix_payment_record_stripe_pi", "payment_record", ["stripe_payment_intent_id"])
        op.create_index("ix_payment_record_stripe_inv", "payment_record", ["stripe_invoice_id"])

    # ── Game minute usage log (audit trail) ──────────────────────────────
    if not _table_exists("minute_usage_log"):
        op.create_table(
            "minute_usage_log",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(255), nullable=False, index=True),
            sa.Column("game_id", sa.String(255), nullable=False, index=True),
            sa.Column("minutes_consumed", sa.Integer(), nullable=False),
            sa.Column("team_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source", sa.String(32), nullable=False, server_default="subscription"),
            # subscription | topup
            sa.Column("balance_id", sa.String(36), nullable=True),
            sa.Column("topup_purchase_id", sa.String(36), nullable=True),
            sa.Column("recorded_at", sa.DateTime(timezone=False), nullable=False),
        )
        op.create_index("ix_minute_usage_log_user_recorded", "minute_usage_log", ["user_id", "recorded_at"])
        op.create_index("ix_minute_usage_log_game_recorded", "minute_usage_log", ["game_id", "recorded_at"])


def downgrade() -> None:
    op.drop_table("minute_usage_log")
    op.drop_table("payment_record")
    op.drop_table("minute_topup_purchase")
    op.drop_table("minute_topup_package")
    op.drop_table("minute_balance")
    op.drop_table("user_subscription")
    op.drop_table("subscription_plan")
