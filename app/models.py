from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiAuthToken(Base):
    __tablename__ = "api_auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    principal_type: Mapped[str] = mapped_column(String(16), index=True, default="user")
    principal_id: Mapped[str] = mapped_column(String(255), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)

    __table_args__ = (
        Index("ix_api_auth_tokens_user_expires", "user_id", "expires_at"),
        Index("ix_api_auth_tokens_principal_expires", "principal_type", "principal_id", "expires_at"),
    )


# ── Monetisation models ─────────────────────────────────────────────────────


class SubscriptionPlan(Base):
    """Subscription tier definition managed by super-admins."""

    __tablename__ = "subscription_plan"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    monthly_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # NULL = unlimited
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    stripe_price_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    __table_args__ = (
        Index("ix_subscription_plan_slug", "slug", unique=True),
        Index("ix_subscription_plan_active", "is_active"),
    )


class UserSubscription(Base):
    """Active subscription binding a user to a plan with Stripe integration."""

    __tablename__ = "user_subscription"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    plan_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    __table_args__ = (
        Index("ix_user_subscription_user_status", "user_id", "status"),
        Index("ix_user_subscription_stripe_sub", "stripe_subscription_id"),
        Index("ix_user_subscription_period_end", "current_period_end"),
    )


class MinuteBalance(Base):
    """Monthly game-minute allocation and usage per user."""

    __tablename__ = "minute_balance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes_allocated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    minutes_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_unlimited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    __table_args__ = (
        Index("ix_minute_balance_user_period", "user_id", "period_year", "period_month", unique=True),
    )


class MinuteTopupPackage(Base):
    """One-off minute package available for purchase."""

    __tablename__ = "minute_topup_package"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    stripe_price_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)


class MinuteTopupPurchase(Base):
    """Recorded top-up purchase with remaining balance and 12-month expiry."""

    __tablename__ = "minute_topup_purchase"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    package_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    minutes_total: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    stripe_payment_intent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    __table_args__ = (
        Index("ix_minute_topup_purchase_user_expires", "user_id", "expires_at"),
    )


class PaymentRecord(Base):
    """Immutable payment/invoice audit trail for all monetary transactions."""

    __tablename__ = "payment_record"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="succeeded")
    stripe_payment_intent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    stripe_invoice_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    subscription_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    topup_purchase_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    __table_args__ = (
        Index("ix_payment_record_user_created", "user_id", "created_at"),
        Index("ix_payment_record_stripe_pi", "stripe_payment_intent_id"),
        Index("ix_payment_record_stripe_inv", "stripe_invoice_id"),
    )


class MinuteUsageLog(Base):
    """Audit log for game-minute consumption ticks."""

    __tablename__ = "minute_usage_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    game_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    minutes_consumed: Mapped[int] = mapped_column(Integer, nullable=False)
    team_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="subscription")
    balance_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    topup_purchase_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)

    __table_args__ = (
        Index("ix_minute_usage_log_user_recorded", "user_id", "recorded_at"),
        Index("ix_minute_usage_log_game_recorded", "game_id", "recorded_at"),
    )
