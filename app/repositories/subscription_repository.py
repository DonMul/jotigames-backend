"""Repository for subscription, minute balance, top-up, and payment persistence."""

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from app.models import (
    MinuteBalance,
    MinuteTopupPackage,
    MinuteTopupPurchase,
    MinuteUsageLog,
    PaymentRecord,
    SubscriptionPlan,
    UserSubscription,
)


class SubscriptionRepository:
    """Data-access layer for the monetisation domain."""

    # ── Subscription plans ───────────────────────────────────────────────

    @staticmethod
    def list_plans(db: Session, *, active_only: bool = False) -> List[SubscriptionPlan]:
        query = select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order)
        if active_only:
            query = query.where(SubscriptionPlan.is_active.is_(True))
        return list(db.execute(query).scalars().all())

    @staticmethod
    def get_plan_by_id(db: Session, plan_id: str) -> Optional[SubscriptionPlan]:
        return db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_plan_by_slug(db: Session, slug: str) -> Optional[SubscriptionPlan]:
        return db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.slug == slug).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_plan(db: Session, **kwargs: Any) -> SubscriptionPlan:
        now = datetime.now(UTC).replace(tzinfo=None)
        plan = SubscriptionPlan(
            id=str(uuid4()),
            created_at=now,
            updated_at=now,
            **kwargs,
        )
        db.add(plan)
        return plan

    @staticmethod
    def update_plan(db: Session, plan: SubscriptionPlan, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(plan, key, value)
        plan.updated_at = datetime.now(UTC).replace(tzinfo=None)

    @staticmethod
    def get_default_plan(db: Session) -> Optional[SubscriptionPlan]:
        """Return the plan marked as the default for new users, if any."""
        return db.execute(
            select(SubscriptionPlan).where(
                and_(SubscriptionPlan.is_default.is_(True), SubscriptionPlan.is_active.is_(True))
            ).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def set_default_plan(db: Session, plan_id: str) -> Optional[SubscriptionPlan]:
        """Mark *plan_id* as the sole default and clear the flag on all others."""
        now = datetime.now(UTC).replace(tzinfo=None)
        # Clear existing defaults
        db.execute(
            update(SubscriptionPlan)
            .where(SubscriptionPlan.is_default.is_(True))
            .values(is_default=False, updated_at=now)
        )
        plan = db.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id).limit(1)
        ).scalar_one_or_none()
        if plan:
            plan.is_default = True
            plan.updated_at = now
        return plan

    @staticmethod
    def clear_default_plan(db: Session) -> None:
        """Remove the default flag from all plans."""
        now = datetime.now(UTC).replace(tzinfo=None)
        db.execute(
            update(SubscriptionPlan)
            .where(SubscriptionPlan.is_default.is_(True))
            .values(is_default=False, updated_at=now)
        )

    # ── User subscriptions ───────────────────────────────────────────────

    @staticmethod
    def get_active_subscription(db: Session, user_id: str) -> Optional[UserSubscription]:
        return db.execute(
            select(UserSubscription)
            .where(
                and_(
                    UserSubscription.user_id == user_id,
                    UserSubscription.status.in_(["active", "past_due"]),
                )
            )
            .order_by(UserSubscription.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_subscription_by_id(db: Session, sub_id: str) -> Optional[UserSubscription]:
        return db.execute(
            select(UserSubscription).where(UserSubscription.id == sub_id).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_subscription_by_stripe_id(db: Session, stripe_sub_id: str) -> Optional[UserSubscription]:
        return db.execute(
            select(UserSubscription)
            .where(UserSubscription.stripe_subscription_id == stripe_sub_id)
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_subscription(db: Session, **kwargs: Any) -> UserSubscription:
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = UserSubscription(
            id=str(uuid4()),
            created_at=now,
            updated_at=now,
            **kwargs,
        )
        db.add(sub)
        return sub

    @staticmethod
    def update_subscription(db: Session, sub: UserSubscription, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(sub, key, value)
        sub.updated_at = datetime.now(UTC).replace(tzinfo=None)

    @staticmethod
    def list_subscriptions_due_for_renewal(db: Session, before: datetime) -> List[UserSubscription]:
        """Find active subscriptions whose period ends before the given timestamp."""
        return list(
            db.execute(
                select(UserSubscription)
                .where(
                    and_(
                        UserSubscription.status == "active",
                        UserSubscription.current_period_end <= before,
                        UserSubscription.cancel_at_period_end.is_(False),
                    )
                )
            ).scalars().all()
        )

    @staticmethod
    def list_subscriptions_pending_cancel(db: Session, before: datetime) -> List[UserSubscription]:
        """Find subscriptions flagged for cancellation whose period has ended."""
        return list(
            db.execute(
                select(UserSubscription)
                .where(
                    and_(
                        UserSubscription.status == "active",
                        UserSubscription.current_period_end <= before,
                        UserSubscription.cancel_at_period_end.is_(True),
                    )
                )
            ).scalars().all()
        )

    @staticmethod
    def list_all_subscriptions(db: Session) -> List[UserSubscription]:
        """Return every subscription row (for admin overview)."""
        return list(
            db.execute(
                select(UserSubscription).order_by(UserSubscription.created_at.desc())
            ).scalars().all()
        )

    # ── Minute balances ──────────────────────────────────────────────────

    @staticmethod
    def get_or_create_balance(
        db: Session,
        user_id: str,
        year: int,
        month: int,
        *,
        allocated: int = 0,
        is_unlimited: bool = False,
    ) -> MinuteBalance:
        existing = db.execute(
            select(MinuteBalance).where(
                and_(
                    MinuteBalance.user_id == user_id,
                    MinuteBalance.period_year == year,
                    MinuteBalance.period_month == month,
                )
            ).limit(1)
        ).scalar_one_or_none()
        if existing:
            return existing
        now = datetime.now(UTC).replace(tzinfo=None)
        balance = MinuteBalance(
            id=str(uuid4()),
            user_id=user_id,
            period_year=year,
            period_month=month,
            minutes_allocated=allocated,
            minutes_used=0,
            is_unlimited=is_unlimited,
            created_at=now,
            updated_at=now,
        )
        db.add(balance)
        db.flush()
        return balance

    @staticmethod
    def get_balance(db: Session, user_id: str, year: int, month: int) -> Optional[MinuteBalance]:
        return db.execute(
            select(MinuteBalance).where(
                and_(
                    MinuteBalance.user_id == user_id,
                    MinuteBalance.period_year == year,
                    MinuteBalance.period_month == month,
                )
            ).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def increment_minutes_used(db: Session, balance_id: str, minutes: int) -> None:
        db.execute(
            update(MinuteBalance)
            .where(MinuteBalance.id == balance_id)
            .values(
                minutes_used=MinuteBalance.minutes_used + minutes,
                updated_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

    # ── Top-up packages ──────────────────────────────────────────────────

    @staticmethod
    def list_topup_packages(db: Session, *, active_only: bool = False) -> List[MinuteTopupPackage]:
        query = select(MinuteTopupPackage).order_by(MinuteTopupPackage.sort_order)
        if active_only:
            query = query.where(MinuteTopupPackage.is_active.is_(True))
        return list(db.execute(query).scalars().all())

    @staticmethod
    def get_topup_package_by_id(db: Session, pkg_id: str) -> Optional[MinuteTopupPackage]:
        return db.execute(
            select(MinuteTopupPackage).where(MinuteTopupPackage.id == pkg_id).limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def create_topup_package(db: Session, **kwargs: Any) -> MinuteTopupPackage:
        now = datetime.now(UTC).replace(tzinfo=None)
        pkg = MinuteTopupPackage(id=str(uuid4()), created_at=now, updated_at=now, **kwargs)
        db.add(pkg)
        return pkg

    @staticmethod
    def update_topup_package(db: Session, pkg: MinuteTopupPackage, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(pkg, key, value)
        pkg.updated_at = datetime.now(UTC).replace(tzinfo=None)

    # ── Top-up purchases ─────────────────────────────────────────────────

    @staticmethod
    def create_topup_purchase(db: Session, **kwargs: Any) -> MinuteTopupPurchase:
        purchase = MinuteTopupPurchase(
            id=str(uuid4()),
            created_at=datetime.now(UTC).replace(tzinfo=None),
            **kwargs,
        )
        db.add(purchase)
        return purchase

    @staticmethod
    def get_active_topups(db: Session, user_id: str) -> List[MinuteTopupPurchase]:
        now = datetime.now(UTC).replace(tzinfo=None)
        return list(
            db.execute(
                select(MinuteTopupPurchase)
                .where(
                    and_(
                        MinuteTopupPurchase.user_id == user_id,
                        MinuteTopupPurchase.minutes_remaining > 0,
                        MinuteTopupPurchase.expires_at > now,
                    )
                )
                .order_by(MinuteTopupPurchase.expires_at.asc())
            ).scalars().all()
        )

    @staticmethod
    def decrement_topup_minutes(db: Session, purchase_id: str, minutes: int) -> None:
        db.execute(
            update(MinuteTopupPurchase)
            .where(MinuteTopupPurchase.id == purchase_id)
            .values(minutes_remaining=MinuteTopupPurchase.minutes_remaining - minutes)
        )

    # ── Payment records ──────────────────────────────────────────────────

    @staticmethod
    def create_payment(db: Session, **kwargs: Any) -> PaymentRecord:
        record = PaymentRecord(
            id=str(uuid4()),
            created_at=datetime.now(UTC).replace(tzinfo=None),
            **kwargs,
        )
        db.add(record)
        return record

    @staticmethod
    def update_payment(db: Session, payment: PaymentRecord, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(payment, key, value)

    @staticmethod
    def get_payment_by_stripe_invoice_id(db: Session, stripe_invoice_id: str) -> Optional[PaymentRecord]:
        return db.execute(
            select(PaymentRecord)
            .where(PaymentRecord.stripe_invoice_id == stripe_invoice_id)
            .order_by(PaymentRecord.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_payment_by_stripe_payment_intent_id(db: Session, stripe_payment_intent_id: str) -> Optional[PaymentRecord]:
        return db.execute(
            select(PaymentRecord)
            .where(PaymentRecord.stripe_payment_intent_id == stripe_payment_intent_id)
            .order_by(PaymentRecord.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def get_latest_pending_payment_for_subscription(db: Session, subscription_id: str) -> Optional[PaymentRecord]:
        return db.execute(
            select(PaymentRecord)
            .where(
                and_(
                    PaymentRecord.subscription_id == subscription_id,
                    PaymentRecord.status == "pending",
                )
            )
            .order_by(PaymentRecord.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def list_payments(
        db: Session,
        *,
        user_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[PaymentRecord]:
        query = select(PaymentRecord).order_by(PaymentRecord.created_at.desc())
        if user_id:
            query = query.where(PaymentRecord.user_id == user_id)
        if since:
            query = query.where(PaymentRecord.created_at >= since)
        if until:
            query = query.where(PaymentRecord.created_at < until)
        return list(db.execute(query).scalars().all())

    @staticmethod
    def revenue_summary(
        db: Session,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Compute total revenue grouped by payment type within a time range."""
        query = select(
            PaymentRecord.type,
            PaymentRecord.currency,
            func.sum(PaymentRecord.amount_cents).label("total_cents"),
            func.count(PaymentRecord.id).label("count"),
        ).where(PaymentRecord.status == "succeeded")
        if since:
            query = query.where(PaymentRecord.created_at >= since)
        if until:
            query = query.where(PaymentRecord.created_at < until)
        query = query.group_by(PaymentRecord.type, PaymentRecord.currency)
        rows = db.execute(query).all()
        return {
            "groups": [
                {
                    "type": row.type,
                    "currency": row.currency,
                    "total_cents": int(row.total_cents or 0),
                    "count": int(row.count or 0),
                }
                for row in rows
            ]
        }

    @staticmethod
    def projected_revenue(db: Session, *, until: datetime) -> Dict[str, Any]:
        """Estimate upcoming revenue from active subscriptions due before *until*."""
        subs = (
            db.execute(
                select(UserSubscription, SubscriptionPlan)
                .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
                .where(
                    and_(
                        UserSubscription.status == "active",
                        UserSubscription.cancel_at_period_end.is_(False),
                        UserSubscription.current_period_end <= until,
                    )
                )
            ).all()
        )
        total_cents = 0
        count = 0
        for sub, plan in subs:
            total_cents += plan.price_cents
            count += 1
        return {"total_cents": total_cents, "subscription_count": count}

    # ── Usage logs ───────────────────────────────────────────────────────

    @staticmethod
    def create_usage_log(db: Session, **kwargs: Any) -> MinuteUsageLog:
        log = MinuteUsageLog(
            id=str(uuid4()),
            recorded_at=datetime.now(UTC).replace(tzinfo=None),
            **kwargs,
        )
        db.add(log)
        return log
