"""Tests for the subscription / monetisation feature.

Covers:
- SubscriptionRepository CRUD operations (plans, subs, balances, topups, payments, usage logs, projections)
- SubscriptionService business logic (subscribe, change plan, cancel, topup, consume, renew, webhooks)
- Service helpers (_next_period_end, _serialize_plan, _serialize_subscription)
- Super-admin serialization helpers (_plan_to_dict, _topup_pkg_to_dict, _subscription_to_dict, _payment_to_dict)
- SubscriptionModule API endpoint smoke tests
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import stripe
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Base,
    MinuteBalance,
    MinuteTopupPackage,
    MinuteTopupPurchase,
    MinuteUsageLog,
    PaymentRecord,
    SubscriptionPlan,
    UserSubscription,
)
from app.repositories.subscription_repository import SubscriptionRepository
from app.security import AuthenticatedPrincipal
from app.services.subscription_service import (
    SubscriptionService,
    _next_period_end,
    _serialize_plan,
    _serialize_subscription,
)


# ── Test fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """Create an in-memory SQLite database with all monetisation tables."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def repo():
    return SubscriptionRepository()


@pytest.fixture
def service():
    return SubscriptionService()


@pytest.fixture
def free_plan(db, repo):
    plan = repo.create_plan(
        db, slug="free", name="Free", monthly_minutes=600,
        price_cents=0, currency="eur", is_active=True, sort_order=0,
    )
    db.commit()
    return plan


@pytest.fixture
def pro_plan(db, repo):
    plan = repo.create_plan(
        db, slug="pro", name="Pro", monthly_minutes=10000,
        price_cents=2499, currency="eur", stripe_price_id="price_pro_test",
        is_active=True, sort_order=2,
    )
    db.commit()
    return plan


@pytest.fixture
def unlimited_plan(db, repo):
    plan = repo.create_plan(
        db, slug="unlimited", name="Unlimited", monthly_minutes=None,
        price_cents=4999, currency="eur", stripe_price_id="price_unlimited_test",
        is_active=True, sort_order=3,
    )
    db.commit()
    return plan


# ── Repository: Plans ────────────────────────────────────────────────────────

class TestSubscriptionRepositoryPlans:
    def test_create_plan(self, db, repo):
        plan = repo.create_plan(db, slug="test", name="Test", monthly_minutes=100, price_cents=500, currency="eur", is_active=True, sort_order=0)
        db.commit()
        assert plan.id is not None
        assert plan.slug == "test"
        assert plan.price_cents == 500

    def test_list_plans_active_only(self, db, repo, free_plan):
        repo.create_plan(db, slug="inactive", name="Inactive", monthly_minutes=0, price_cents=0, currency="eur", is_active=False, sort_order=99)
        db.commit()

        all_plans = repo.list_plans(db, active_only=False)
        active_plans = repo.list_plans(db, active_only=True)
        assert len(all_plans) == 2
        assert len(active_plans) == 1
        assert active_plans[0].slug == "free"

    def test_get_plan_by_slug(self, db, repo, free_plan):
        found = repo.get_plan_by_slug(db, "free")
        assert found is not None
        assert found.id == free_plan.id

    def test_get_plan_by_id(self, db, repo, free_plan):
        found = repo.get_plan_by_id(db, free_plan.id)
        assert found is not None
        assert found.slug == "free"

    def test_update_plan(self, db, repo, free_plan):
        repo.update_plan(db, free_plan, name="Free v2", monthly_minutes=700)
        db.commit()
        db.refresh(free_plan)
        assert free_plan.name == "Free v2"
        assert free_plan.monthly_minutes == 700


# ── Repository: Subscriptions ────────────────────────────────────────────────

class TestSubscriptionRepositorySubscriptions:
    def test_create_and_get_active_subscription(self, db, repo, free_plan):
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
        )
        db.commit()
        active = repo.get_active_subscription(db, "user-1")
        assert active is not None
        assert active.id == sub.id

    def test_no_active_subscription(self, db, repo):
        active = repo.get_active_subscription(db, "user-none")
        assert active is None

    def test_list_subscriptions_due_for_renewal(self, db, repo, free_plan):
        past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=past - timedelta(days=30),
            current_period_end=past,
            cancel_at_period_end=False,
        )
        db.commit()
        due = repo.list_subscriptions_due_for_renewal(db, datetime.now(UTC).replace(tzinfo=None))
        assert len(due) == 1

    def test_list_all_subscriptions(self, db, repo, free_plan):
        now = datetime.now(UTC).replace(tzinfo=None)
        for i in range(3):
            repo.create_subscription(
                db, user_id=f"user-{i}", plan_id=free_plan.id, status="active",
                current_period_start=now, current_period_end=now + timedelta(days=30),
            )
        db.commit()
        all_subs = repo.list_all_subscriptions(db)
        assert len(all_subs) == 3


# ── Repository: Minute Balance ───────────────────────────────────────────────

class TestSubscriptionRepositoryBalance:
    def test_get_or_create_balance(self, db, repo):
        balance = repo.get_or_create_balance(db, "user-1", 2025, 6, allocated=600)
        db.commit()
        assert balance.minutes_allocated == 600
        assert balance.minutes_used == 0

        # Calling again should return the same record
        same = repo.get_or_create_balance(db, "user-1", 2025, 6, allocated=600)
        assert same.id == balance.id

    def test_increment_minutes_used(self, db, repo):
        balance = repo.get_or_create_balance(db, "user-1", 2025, 6, allocated=600)
        db.commit()
        repo.increment_minutes_used(db, balance.id, 50)
        db.commit()
        db.refresh(balance)
        assert balance.minutes_used == 50


# ── Repository: Top-ups ──────────────────────────────────────────────────────

class TestSubscriptionRepositoryTopups:
    def test_create_and_list_topup_packages(self, db, repo):
        repo.create_topup_package(db, name="500 min", minutes=500, price_cents=499, currency="eur", is_active=True, sort_order=1)
        repo.create_topup_package(db, name="Hidden", minutes=100, price_cents=99, currency="eur", is_active=False, sort_order=2)
        db.commit()

        all_pkgs = repo.list_topup_packages(db, active_only=False)
        active_pkgs = repo.list_topup_packages(db, active_only=True)
        assert len(all_pkgs) == 2
        assert len(active_pkgs) == 1

    def test_update_topup_package_fields(self, db, repo):
        pkg = repo.create_topup_package(
            db,
            name="500 min",
            minutes=500,
            price_cents=499,
            currency="eur",
            is_active=True,
            sort_order=1,
        )
        db.commit()

        repo.update_topup_package(db, pkg, minutes=750, price_cents=799, is_active=False)
        db.commit()
        db.refresh(pkg)

        assert pkg.minutes == 750
        assert pkg.price_cents == 799
        assert pkg.is_active is False

    def test_list_topup_packages_respects_sort_order(self, db, repo):
        first = repo.create_topup_package(
            db,
            name="First",
            minutes=100,
            price_cents=100,
            currency="eur",
            is_active=True,
            sort_order=5,
        )
        second = repo.create_topup_package(
            db,
            name="Second",
            minutes=200,
            price_cents=200,
            currency="eur",
            is_active=True,
            sort_order=1,
        )
        db.commit()

        packages = repo.list_topup_packages(db, active_only=False)
        assert packages[0].id == second.id
        assert packages[1].id == first.id

    def test_create_topup_purchase(self, db, repo):
        purchase = repo.create_topup_purchase(
            db, user_id="user-1", package_id=None,
            minutes_total=500, minutes_remaining=500,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=365),
            stripe_payment_intent_id="pi_test",
        )
        db.commit()
        assert purchase.minutes_remaining == 500

    def test_get_active_topups_filters_expired(self, db, repo):
        # Active purchase
        repo.create_topup_purchase(
            db, user_id="user-1", package_id=None,
            minutes_total=500, minutes_remaining=300,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30),
        )
        # Expired purchase
        repo.create_topup_purchase(
            db, user_id="user-1", package_id=None,
            minutes_total=500, minutes_remaining=200,
            expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
        )
        # Zero remaining
        repo.create_topup_purchase(
            db, user_id="user-1", package_id=None,
            minutes_total=500, minutes_remaining=0,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30),
        )
        db.commit()
        active = repo.get_active_topups(db, "user-1")
        assert len(active) == 1
        assert active[0].minutes_remaining == 300

    def test_expire_topups_sets_remaining_to_zero(self, db, repo):
        expired = repo.create_topup_purchase(
            db,
            user_id="user-1",
            package_id=None,
            minutes_total=500,
            minutes_remaining=250,
            expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
        )
        active = repo.create_topup_purchase(
            db,
            user_id="user-1",
            package_id=None,
            minutes_total=500,
            minutes_remaining=300,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30),
        )
        db.commit()

        count = repo.expire_topups(db, datetime.now(UTC).replace(tzinfo=None))
        db.commit()
        db.refresh(expired)
        db.refresh(active)

        assert count == 1
        assert expired.minutes_remaining == 0
        assert active.minutes_remaining == 300


# ── Repository: Payments ─────────────────────────────────────────────────────

class TestSubscriptionRepositoryPayments:
    def test_create_and_list_payments(self, db, repo):
        repo.create_payment(
            db, user_id="user-1", amount_cents=999, currency="eur",
            type="subscription", status="succeeded",
        )
        repo.create_payment(
            db, user_id="user-2", amount_cents=499, currency="eur",
            type="topup", status="succeeded",
        )
        db.commit()

        all_payments = repo.list_payments(db)
        assert len(all_payments) == 2

        user_payments = repo.list_payments(db, user_id="user-1")
        assert len(user_payments) == 1

    def test_revenue_summary(self, db, repo):
        repo.create_payment(db, user_id="u1", amount_cents=1000, currency="eur", type="subscription", status="succeeded")
        repo.create_payment(db, user_id="u2", amount_cents=500, currency="eur", type="topup", status="succeeded")
        repo.create_payment(db, user_id="u3", amount_cents=2000, currency="eur", type="subscription", status="failed")
        db.commit()

        summary = repo.revenue_summary(db)
        # revenue_summary returns {"groups": [...]} where groups are by type+currency for succeeded only
        assert len(summary["groups"]) >= 1
        total = sum(g["total_cents"] for g in summary["groups"])
        assert total == 1500  # 1000 + 500, excluding the failed one


# ── Service: Seed Default Plans ──────────────────────────────────────────────

class TestSubscriptionServiceSeedPlans:
    def test_seed_creates_four_plans(self, db, service):
        service.seed_default_plans(db)
        repo = SubscriptionRepository()
        plans = repo.list_plans(db, active_only=False)
        assert len(plans) == 4
        slugs = {p.slug for p in plans}
        assert slugs == {"free", "beginner", "pro", "unlimited"}

    def test_seed_idempotent(self, db, service):
        service.seed_default_plans(db)
        service.seed_default_plans(db)
        repo = SubscriptionRepository()
        plans = repo.list_plans(db, active_only=False)
        assert len(plans) == 4


# ── Service: Subscribe ───────────────────────────────────────────────────────

class TestSubscriptionServiceSubscribe:
    def test_subscribe_to_free_plan(self, db, service, free_plan):
        result = service.subscribe(db, "user-1", "free", email="test@example.com")
        assert result["status"] == "active"
        assert result["plan"] == "free"

    def test_subscribe_already_subscribed(self, db, service, free_plan):
        service.subscribe(db, "user-1", "free", email="test@example.com")
        with pytest.raises(ValueError, match="already"):
            service.subscribe(db, "user-1", "free", email="test@example.com")

    def test_subscribe_nonexistent_plan(self, db, service):
        with pytest.raises(ValueError, match="notFound"):
            service.subscribe(db, "user-1", "nonexistent", email="test@example.com")


# ── Service: Get Summary ─────────────────────────────────────────────────────

class TestSubscriptionServiceSummary:
    @patch("app.services.subscription_service.get_settings")
    def test_summary_monetisation_disabled(self, mock_settings, db, service):
        mock_settings.return_value = MagicMock(enable_monetisation=False)
        summary = service.get_user_subscription_summary(db, "user-1")
        assert summary["monetisation_enabled"] is False
        assert summary["balance"]["is_unlimited"] is True
        assert summary["balance"]["minutes_remaining"] is None

    @patch("app.services.subscription_service.get_settings")
    def test_summary_with_subscription(self, mock_settings, db, service, free_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=True)
        service.subscribe(db, "user-1", "free", email="test@example.com")
        summary = service.get_user_subscription_summary(db, "user-1")
        assert summary["monetisation_enabled"] is True
        assert summary["plan"]["slug"] == "free"
        assert summary["balance"]["minutes_allocated"] == 600

    @patch("app.services.subscription_service.get_settings")
    def test_summary_no_subscription(self, mock_settings, db, service):
        mock_settings.return_value = MagicMock(enable_monetisation=True)
        summary = service.get_user_subscription_summary(db, "user-1")
        assert summary["subscription"] is None


# ── Service: Consume Minutes ─────────────────────────────────────────────────

class TestSubscriptionServiceConsumeMinutes:
    @patch("app.services.subscription_service.get_settings")
    def test_consume_from_balance(self, mock_settings, db, service, free_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=True)
        service.subscribe(db, "user-1", "free", email="test@example.com")
        ok = service.consume_minutes(db, "user-1", "game-1", 100, 1)
        assert ok is True

        # Verify balance updated
        repo = SubscriptionRepository()
        now = datetime.now(UTC)
        balance = repo.get_balance(db, "user-1", now.year, now.month)
        assert balance is not None
        assert balance.minutes_used == 100

    @patch("app.services.subscription_service.get_settings")
    def test_consume_exceeds_balance_uses_topups(self, mock_settings, db, service, free_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=True)
        service.subscribe(db, "user-1", "free", email="test@example.com")

        # Add a top-up
        repo = SubscriptionRepository()
        repo.create_topup_purchase(
            db, user_id="user-1", package_id=None,
            minutes_total=1000, minutes_remaining=1000,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=365),
        )
        db.commit()

        # Consume more than the 600 min allocation
        ok = service.consume_minutes(db, "user-1", "game-1", 700, 1)
        assert ok is True

    @patch("app.services.subscription_service.get_settings")
    def test_consume_unlimited_plan_always_succeeds(self, mock_settings, db, service, unlimited_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=True)
        # Create subscription directly since unlimited plan would normally need Stripe
        repo = SubscriptionRepository()
        now = datetime.now(UTC).replace(tzinfo=None)
        repo.create_subscription(
            db, user_id="user-1", plan_id=unlimited_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
        )
        db.commit()

        ok = service.consume_minutes(db, "user-1", "game-1", 999999, 1)
        assert ok is True

    @patch("app.services.subscription_service.get_settings")
    def test_consume_monetisation_disabled_always_succeeds(self, mock_settings, db, service):
        mock_settings.return_value = MagicMock(enable_monetisation=False)
        ok = service.consume_minutes(db, "user-1", "game-1", 1000000, 1)
        assert ok is True


# ── Service: Cancel / Reactivate ─────────────────────────────────────────────

class TestSubscriptionServiceCancelReactivate:
    def test_cancel_at_period_end(self, db, service, free_plan):
        service.subscribe(db, "user-1", "free", email="test@example.com")
        result = service.cancel_subscription(db, "user-1", immediate=False)
        assert result["cancel_at_period_end"] is True

        # Verify sub is still active but flagged
        repo = SubscriptionRepository()
        sub = repo.get_active_subscription(db, "user-1")
        assert sub.cancel_at_period_end is True

    def test_cancel_immediate(self, db, service, free_plan):
        service.subscribe(db, "user-1", "free", email="test@example.com")
        result = service.cancel_subscription(db, "user-1", immediate=True)
        assert result["status"] == "cancelled"

    def test_reactivate(self, db, service, free_plan):
        service.subscribe(db, "user-1", "free", email="test@example.com")
        service.cancel_subscription(db, "user-1", immediate=False)
        result = service.reactivate_subscription(db, "user-1")
        assert result["cancel_at_period_end"] is False

    def test_cancel_no_subscription(self, db, service):
        with pytest.raises(ValueError, match="notFound"):
            service.cancel_subscription(db, "user-1")


# ── Service: Renew Period ────────────────────────────────────────────────────

class TestSubscriptionServiceRenew:
    def test_renew_creates_new_balance(self, db, service, free_plan):
        service.subscribe(db, "user-1", "free", email="test@example.com")
        # Move period end to the past
        repo = SubscriptionRepository()
        sub = repo.get_active_subscription(db, "user-1")
        past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        repo.update_subscription(db, sub, current_period_end=past)
        db.commit()

        service.renew_period(db, sub)

        # Verify period extended
        db.refresh(sub)
        assert sub.current_period_end > past


# ── Service: Change Plan ─────────────────────────────────────────────────────

class TestSubscriptionServiceChangePlan:
    def test_change_from_free_to_another_free_slug(self, db, service, free_plan, repo):
        # Create a second free-tier plan
        repo.create_plan(db, slug="free2", name="Free2", monthly_minutes=300, price_cents=0, currency="eur", is_active=True, sort_order=1)
        db.commit()

        service.subscribe(db, "user-1", "free", email="test@example.com")
        result = service.change_plan(db, "user-1", "free2", email="test@example.com")
        assert result["plan"] == "free2"

    def test_change_to_same_plan_raises(self, db, service, free_plan):
        service.subscribe(db, "user-1", "free", email="test@example.com")
        with pytest.raises(ValueError, match="samePlan"):
            service.change_plan(db, "user-1", "free", email="test@example.com")


# ── Service: Purchase Top-up ─────────────────────────────────────────────────

class TestSubscriptionServiceTopup:
    def test_purchase_topup_free_package(self, db, service, free_plan, repo):
        pkg = repo.create_topup_package(
            db, name="100 min", minutes=100, price_cents=0, currency="eur", is_active=True, sort_order=0,
        )
        db.commit()

        service.subscribe(db, "user-1", "free", email="test@example.com")
        result = service.purchase_topup(db, "user-1", pkg.id, email="test@example.com")
        assert result["minutes"] == 100

    def test_purchase_topup_nonexistent_package(self, db, service):
        with pytest.raises(ValueError, match="notFound"):
            service.purchase_topup(db, "user-1", "fake-id", email="test@example.com")

    @patch("app.services.subscription_service.stripe.checkout.Session.create")
    def test_purchase_topup_paid_with_stripe_price_returns_checkout_url(
        self,
        mock_checkout_create,
        db,
        service,
        repo,
        free_plan,
    ):
        pkg = repo.create_topup_package(
            db,
            name="500 min",
            minutes=500,
            price_cents=499,
            currency="eur",
            stripe_price_id="price_topup_500",
            is_active=True,
            sort_order=0,
        )
        db.commit()

        service.subscribe(db, "user-1", "free", email="test@example.com")
        mock_checkout_create.return_value = {"url": "https://checkout.stripe.test/cs_topup_1"}

        with patch.object(service, "_get_or_create_stripe_customer", return_value="cus_123"):
            result = service.purchase_topup(db, "user-1", pkg.id, email="test@example.com")

        assert result["payment_url"] == "https://checkout.stripe.test/cs_topup_1"
        topups = repo.get_active_topups(db, "user-1")
        assert len(topups) == 0

        payments = repo.list_payments(db, user_id="user-1")
        assert len(payments) == 1
        assert payments[0].status == "pending"
        assert payments[0].type == "topup"

        _, kwargs = mock_checkout_create.call_args
        assert kwargs["mode"] == "payment"
        assert kwargs["line_items"][0]["price"] == "price_topup_500"
        assert kwargs["payment_method_types"] == service._subscription_payment_method_types()

    @patch("app.services.subscription_service.stripe.checkout.Session.create")
    def test_purchase_topup_paid_without_package_methods_uses_defaults(
        self,
        mock_checkout_create,
        db,
        service,
        repo,
        free_plan,
    ):
        pkg = repo.create_topup_package(
            db,
            name="100 min",
            minutes=100,
            price_cents=199,
            currency="eur",
            stripe_price_id="price_topup_100",
            is_active=True,
            sort_order=0,
        )
        db.commit()

        service.subscribe(db, "user-1", "free", email="test@example.com")
        mock_checkout_create.return_value = {"url": "https://checkout.stripe.test/cs_topup_default"}

        with patch.object(service, "_get_or_create_stripe_customer", return_value="cus_123"):
            service.purchase_topup(db, "user-1", pkg.id, email="test@example.com")

        _, kwargs = mock_checkout_create.call_args
        assert "card" in kwargs["payment_method_types"]

    @patch("app.services.subscription_service.stripe.checkout.Session.create")
    def test_purchase_topup_checkout_success_url_contains_session_placeholder(
        self,
        mock_checkout_create,
        db,
        service,
        repo,
        free_plan,
    ):
        pkg = repo.create_topup_package(
            db,
            name="100 min",
            minutes=100,
            price_cents=199,
            currency="eur",
            stripe_price_id="price_topup_100",
            is_active=True,
            sort_order=0,
        )
        db.commit()

        service.subscribe(db, "user-1", "free", email="test@example.com")
        mock_checkout_create.return_value = {"url": "https://checkout.stripe.test/cs_topup_success_url"}

        with patch.object(service, "_get_or_create_stripe_customer", return_value="cus_123"):
            service.purchase_topup(db, "user-1", pkg.id, email="test@example.com")

        _, kwargs = mock_checkout_create.call_args
        assert "session_id={CHECKOUT_SESSION_ID}" in kwargs["success_url"]


# ── Repository: subscription lookups ─────────────────────────────────────────

class TestSubscriptionRepositoryLookups:
    def test_get_subscription_by_id(self, db, repo, free_plan):
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
        )
        db.commit()
        found = repo.get_subscription_by_id(db, sub.id)
        assert found is not None
        assert found.user_id == "user-1"

    def test_get_subscription_by_id_not_found(self, db, repo):
        assert repo.get_subscription_by_id(db, "nonexistent") is None

    def test_get_subscription_by_stripe_id(self, db, repo, free_plan):
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
            stripe_subscription_id="sub_stripe_123",
        )
        db.commit()
        found = repo.get_subscription_by_stripe_id(db, "sub_stripe_123")
        assert found is not None
        assert found.id == sub.id

    def test_get_subscription_by_stripe_id_not_found(self, db, repo):
        assert repo.get_subscription_by_stripe_id(db, "sub_nonexistent") is None

    def test_list_subscriptions_pending_cancel(self, db, repo, free_plan):
        past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
        # Pending cancel with period ended
        repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=past - timedelta(days=30),
            current_period_end=past,
            cancel_at_period_end=True,
        )
        # Active but NOT pending cancel (should not appear)
        repo.create_subscription(
            db, user_id="user-2", plan_id=free_plan.id, status="active",
            current_period_start=past - timedelta(days=30),
            current_period_end=past,
            cancel_at_period_end=False,
        )
        # Pending cancel but period NOT ended yet (should not appear)
        future = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=10)
        repo.create_subscription(
            db, user_id="user-3", plan_id=free_plan.id, status="active",
            current_period_start=past,
            current_period_end=future,
            cancel_at_period_end=True,
        )
        db.commit()

        pending = repo.list_subscriptions_pending_cancel(db, datetime.now(UTC).replace(tzinfo=None))
        assert len(pending) == 1
        assert pending[0].user_id == "user-1"

    def test_update_subscription(self, db, repo, free_plan):
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
        )
        db.commit()
        old_updated = sub.updated_at

        repo.update_subscription(db, sub, status="cancelled")
        db.commit()
        db.refresh(sub)
        assert sub.status == "cancelled"
        assert sub.updated_at >= old_updated


# ── Repository: Balance direct access ────────────────────────────────────────

class TestSubscriptionRepositoryBalanceDirect:
    def test_get_balance_exists(self, db, repo):
        repo.get_or_create_balance(db, "user-1", 2025, 7, allocated=1000)
        db.commit()
        balance = repo.get_balance(db, "user-1", 2025, 7)
        assert balance is not None
        assert balance.minutes_allocated == 1000

    def test_get_balance_not_found(self, db, repo):
        assert repo.get_balance(db, "user-1", 2025, 12) is None


# ── Repository: Topup package management ─────────────────────────────────────

class TestSubscriptionRepositoryTopupPackages:
    def test_get_topup_package_by_id(self, db, repo):
        pkg = repo.create_topup_package(
            db, name="500 min", minutes=500, price_cents=499,
            currency="eur", is_active=True, sort_order=1,
        )
        db.commit()
        found = repo.get_topup_package_by_id(db, pkg.id)
        assert found is not None
        assert found.name == "500 min"

    def test_get_topup_package_not_found(self, db, repo):
        assert repo.get_topup_package_by_id(db, "nonexistent") is None

    def test_update_topup_package(self, db, repo):
        pkg = repo.create_topup_package(
            db, name="Old Name", minutes=200, price_cents=199,
            currency="eur", is_active=True, sort_order=1,
        )
        db.commit()
        old_updated = pkg.updated_at

        repo.update_topup_package(db, pkg, name="New Name", minutes=300)
        db.commit()
        db.refresh(pkg)
        assert pkg.name == "New Name"
        assert pkg.minutes == 300
        assert pkg.updated_at >= old_updated

    def test_decrement_topup_minutes(self, db, repo):
        purchase = repo.create_topup_purchase(
            db, user_id="user-1", package_id=None,
            minutes_total=500, minutes_remaining=500,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=365),
        )
        db.commit()
        repo.decrement_topup_minutes(db, purchase.id, 150)
        db.commit()
        db.refresh(purchase)
        assert purchase.minutes_remaining == 350


# ── Repository: Usage logs ───────────────────────────────────────────────────

class TestSubscriptionRepositoryUsageLogs:
    def test_create_usage_log(self, db, repo):
        log = repo.create_usage_log(
            db,
            user_id="user-1",
            game_id="game-1",
            minutes_consumed=30,
            team_count=4,
            source="subscription",
            balance_id="bal-1",
        )
        db.commit()
        assert log.id is not None
        assert log.minutes_consumed == 30
        assert log.team_count == 4
        assert log.source == "subscription"
        assert log.balance_id == "bal-1"
        assert log.recorded_at is not None

    def test_create_usage_log_topup_source(self, db, repo):
        log = repo.create_usage_log(
            db,
            user_id="user-1",
            game_id="game-2",
            minutes_consumed=10,
            team_count=2,
            source="topup",
            topup_purchase_id="tp-1",
        )
        db.commit()
        assert log.source == "topup"
        assert log.topup_purchase_id == "tp-1"


# ── Repository: Payments with date filters ───────────────────────────────────

class TestSubscriptionRepositoryPaymentFilters:
    def test_list_payments_since_filter(self, db, repo):
        now = datetime.now(UTC).replace(tzinfo=None)
        p1 = repo.create_payment(
            db, user_id="u1", amount_cents=100, currency="eur",
            type="subscription", status="succeeded",
        )
        db.commit()
        # Manually backdate one payment
        p1.created_at = now - timedelta(days=10)
        db.commit()

        p2 = repo.create_payment(
            db, user_id="u1", amount_cents=200, currency="eur",
            type="subscription", status="succeeded",
        )
        db.commit()

        # Since 5 days ago should only get p2
        all_recent = repo.list_payments(db, since=now - timedelta(days=5))
        assert len(all_recent) == 1
        assert all_recent[0].amount_cents == 200

    def test_list_payments_until_filter(self, db, repo):
        now = datetime.now(UTC).replace(tzinfo=None)
        p1 = repo.create_payment(
            db, user_id="u1", amount_cents=100, currency="eur",
            type="subscription", status="succeeded",
        )
        db.commit()
        p1.created_at = now - timedelta(days=10)
        db.commit()

        repo.create_payment(
            db, user_id="u1", amount_cents=200, currency="eur",
            type="subscription", status="succeeded",
        )
        db.commit()

        # Until 5 days ago should only get p1
        old_payments = repo.list_payments(db, until=now - timedelta(days=5))
        assert len(old_payments) == 1
        assert old_payments[0].amount_cents == 100


# ── Repository: Revenue summary with filters ────────────────────────────────

class TestSubscriptionRepositoryRevenueSummaryFilters:
    def test_revenue_summary_with_date_range(self, db, repo):
        now = datetime.now(UTC).replace(tzinfo=None)
        p1 = repo.create_payment(
            db, user_id="u1", amount_cents=1000, currency="eur",
            type="subscription", status="succeeded",
        )
        db.commit()
        p1.created_at = now - timedelta(days=60)
        db.commit()

        repo.create_payment(
            db, user_id="u2", amount_cents=500, currency="eur",
            type="topup", status="succeeded",
        )
        db.commit()

        # Only recent payments (last 30 days)
        summary = repo.revenue_summary(db, since=now - timedelta(days=30))
        total = sum(g["total_cents"] for g in summary["groups"])
        assert total == 500  # Only the topup from "now"

    def test_revenue_summary_empty_returns_empty_groups(self, db, repo):
        summary = repo.revenue_summary(db)
        assert summary == {"groups": []}


# ── Repository: Projected revenue ────────────────────────────────────────────

class TestSubscriptionRepositoryProjectedRevenue:
    def test_projected_revenue_with_active_subs(self, db, repo, free_plan, pro_plan):
        now = datetime.now(UTC).replace(tzinfo=None)
        # Active sub due for renewal soon (period end in past)
        repo.create_subscription(
            db, user_id="user-1", plan_id=pro_plan.id, status="active",
            current_period_start=now - timedelta(days=30),
            current_period_end=now - timedelta(hours=1),
            cancel_at_period_end=False,
        )
        # Active sub NOT due yet (period end far in future)
        repo.create_subscription(
            db, user_id="user-2", plan_id=free_plan.id, status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
        )
        # Cancel-at-end (should be excluded)
        repo.create_subscription(
            db, user_id="user-3", plan_id=pro_plan.id, status="active",
            current_period_start=now - timedelta(days=30),
            current_period_end=now - timedelta(hours=1),
            cancel_at_period_end=True,
        )
        db.commit()

        projected = repo.projected_revenue(db, until=now)
        assert projected["subscription_count"] == 1
        assert projected["total_cents"] == pro_plan.price_cents

    def test_projected_revenue_empty(self, db, repo):
        projected = repo.projected_revenue(db, until=datetime.now(UTC).replace(tzinfo=None))
        assert projected == {"total_cents": 0, "subscription_count": 0}


# ── Service: Process pending cancellations ───────────────────────────────────

class TestSubscriptionServicePendingCancellations:
    def test_process_pending_cancellations(self, db, service, free_plan, repo):
        now = datetime.now(UTC).replace(tzinfo=None)
        past = now - timedelta(hours=2)
        # Pending cancel with period ended
        repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=past - timedelta(days=30),
            current_period_end=past,
            cancel_at_period_end=True,
        )
        # Active but NOT pending cancel
        repo.create_subscription(
            db, user_id="user-2", plan_id=free_plan.id, status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
        )
        db.commit()

        count = service.process_pending_cancellations(db)
        assert count == 1

        # Verify user-1 is cancelled
        sub1 = repo.get_active_subscription(db, "user-1")
        assert sub1 is None  # No longer active

    def test_process_pending_cancellations_none_eligible(self, db, service, repo):
        count = service.process_pending_cancellations(db)
        assert count == 0


# ── Service: Consume minutes edge cases ──────────────────────────────────────

class TestSubscriptionServiceConsumeEdgeCases:
    @patch("app.services.subscription_service.get_settings")
    def test_consume_insufficient_balance_no_topups_returns_false(self, mock_settings, db, service, free_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=True)
        service.subscribe(db, "user-1", "free", email="test@example.com")

        # Try to consume more than the 600 min allocation with no topups
        ok = service.consume_minutes(db, "user-1", "game-1", 700, 1)
        assert ok is False

        # Verify balance was NOT updated (rollback occurred)
        repo = SubscriptionRepository()
        now = datetime.now(UTC)
        balance = repo.get_balance(db, "user-1", now.year, now.month)
        assert balance is not None
        assert balance.minutes_used == 0

    @patch("app.services.subscription_service.get_settings")
    def test_consume_no_subscription_no_plan(self, mock_settings, db, service):
        """User with no subscription should get 0 allocated, consumption fails if > 0."""
        mock_settings.return_value = MagicMock(enable_monetisation=True)
        ok = service.consume_minutes(db, "user-1", "game-1", 1, 1)
        assert ok is False

    @patch("app.services.subscription_service.get_settings")
    def test_consume_creates_usage_log(self, mock_settings, db, service, free_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=True)
        service.subscribe(db, "user-1", "free", email="test@example.com")
        service.consume_minutes(db, "user-1", "game-1", 50, 3)

        # Verify usage log was created
        from sqlalchemy import select
        logs = list(db.execute(select(MinuteUsageLog).where(MinuteUsageLog.user_id == "user-1")).scalars().all())
        assert len(logs) == 1
        assert logs[0].minutes_consumed == 50
        assert logs[0].team_count == 3
        assert logs[0].source == "subscription"
        assert logs[0].game_id == "game-1"


# ── Service: Reactivate edge cases ──────────────────────────────────────────

class TestSubscriptionServiceReactivateEdgeCases:
    def test_reactivate_not_pending_cancel_raises(self, db, service, free_plan):
        service.subscribe(db, "user-1", "free", email="test@example.com")
        with pytest.raises(ValueError, match="notPendingCancel"):
            service.reactivate_subscription(db, "user-1")

    def test_reactivate_no_subscription_raises(self, db, service):
        with pytest.raises(ValueError, match="notFound"):
            service.reactivate_subscription(db, "user-1")


# ── Service: Change plan edge cases ──────────────────────────────────────────

class TestSubscriptionServiceChangePlanEdgeCases:
    def test_change_plan_no_existing_sub_creates_new(self, db, service, free_plan):
        """When no active sub exists, change_plan falls through to subscribe."""
        result = service.change_plan(db, "user-1", "free", email="test@example.com")
        assert result["plan"] == "free"
        assert result["status"] == "active"
        # Verify subscription exists now
        repo = SubscriptionRepository()
        sub = repo.get_active_subscription(db, "user-1")
        assert sub is not None

    def test_change_plan_nonexistent_target_raises(self, db, service, free_plan):
        service.subscribe(db, "user-1", "free", email="test@example.com")
        with pytest.raises(ValueError, match="notFound"):
            service.change_plan(db, "user-1", "nonexistent", email="test@example.com")

    def test_change_plan_direction_upgrade(self, db, service, free_plan, repo):
        """Changing from free (sort_order=0) to a higher plan = upgrade."""
        # Create a paid plan WITHOUT stripe_price_id to avoid Stripe calls
        repo.create_plan(
            db, slug="premium", name="Premium", monthly_minutes=8000,
            price_cents=1999, currency="eur", is_active=True, sort_order=2,
        )
        db.commit()

        service.subscribe(db, "user-1", "free", email="test@example.com")
        result = service.change_plan(db, "user-1", "premium", email="test@example.com")
        assert result["change"] == "upgrade"
        assert result["plan"] == "premium"

    def test_change_plan_direction_downgrade(self, db, service, repo, pro_plan):
        """Changing from pro to a lower-sort plan = downgrade."""
        # Create a "basic" plan with lower sort_order
        repo.create_plan(
            db, slug="basic", name="Basic", monthly_minutes=100,
            price_cents=0, currency="eur", is_active=True, sort_order=0,
        )
        db.commit()

        # Subscribe directly to pro via repo (bypass Stripe)
        now = datetime.now(UTC).replace(tzinfo=None)
        repo.create_subscription(
            db, user_id="user-1", plan_id=pro_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
        )
        db.commit()

        result = service.change_plan(db, "user-1", "basic", email="test@example.com")
        assert result["change"] == "downgrade"

    def test_change_plan_updates_balance(self, db, service, free_plan, repo):
        """After changing plan, balance allocation is updated for current month."""
        repo.create_plan(
            db, slug="bigger", name="Bigger", monthly_minutes=5000,
            price_cents=0, currency="eur", is_active=True, sort_order=1,
        )
        db.commit()

        service.subscribe(db, "user-1", "free", email="test@example.com")
        now = datetime.now(UTC)
        balance = repo.get_balance(db, "user-1", now.year, now.month)
        assert balance.minutes_allocated == 600

        service.change_plan(db, "user-1", "bigger", email="test@example.com")
        db.refresh(balance)
        assert balance.minutes_allocated == 5000

    @patch("app.services.subscription_service.stripe.checkout.Session.create")
    @patch("app.services.subscription_service.stripe.Customer.modify")
    @patch("app.services.subscription_service.stripe.PaymentMethod.attach")
    def test_subscribe_paid_returns_payment_url(
        self,
        mock_pm_attach,
        mock_customer_modify,
        mock_checkout_create,
        db,
        service,
        repo,
    ):
        repo.create_plan(
            db,
            slug="paid",
            name="Paid",
            monthly_minutes=5000,
            price_cents=1299,
            currency="eur",
            stripe_price_id="price_paid_123",
            is_active=True,
            sort_order=2,
        )
        db.commit()

        mock_checkout_create.return_value = {
            "url": "https://checkout.stripe.test/cs_123",
        }

        with patch.object(service, "_get_or_create_stripe_customer", return_value="cus_123"):
            result = service.subscribe(
                db,
                "user-1",
                "paid",
                email="test@example.com",
                stripe_payment_method_id="pm_123",
            )

        assert result["plan"] == "paid"
        assert result["payment_url"] == "https://checkout.stripe.test/cs_123"
        # Checkout Session is called with subscription mode
        _, kwargs = mock_checkout_create.call_args
        assert kwargs["mode"] == "subscription"
        payments = repo.list_payments(db, user_id="user-1")
        assert len(payments) == 1
        assert payments[0].status == "pending"
        assert payments[0].type == "subscription"

    @patch("app.services.subscription_service.stripe.Subscription.modify")
    @patch("app.services.subscription_service.stripe.Subscription.retrieve")
    def test_change_paid_to_paid_upgrade_returns_payment_url_and_always_invoice(
        self,
        mock_sub_retrieve,
        mock_sub_modify,
        db,
        service,
        repo,
    ):
        old_plan = repo.create_plan(
            db,
            slug="starter",
            name="Starter",
            monthly_minutes=1000,
            price_cents=999,
            currency="eur",
            stripe_price_id="price_starter",
            is_active=True,
            sort_order=1,
        )
        new_plan = repo.create_plan(
            db,
            slug="proplus",
            name="Pro+",
            monthly_minutes=10000,
            price_cents=2999,
            currency="eur",
            stripe_price_id="price_proplus",
            is_active=True,
            sort_order=3,
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        repo.create_subscription(
            db,
            user_id="user-1",
            plan_id=old_plan.id,
            status="active",
            stripe_subscription_id="sub_live_123",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db.commit()

        mock_sub_retrieve.return_value = {"items": {"data": [{"id": "si_123"}]}}
        mock_sub_modify.return_value = {
            "latest_invoice": {
                "id": "inv_upgrade_123",
                "payment_intent": "pi_upgrade_123",
                "hosted_invoice_url": "https://pay.stripe.test/inv_upgrade",
            }
        }

        result = service.change_plan(db, "user-1", new_plan.slug, email="test@example.com")
        assert result["change"] == "upgrade"
        assert result["payment_url"] == "https://pay.stripe.test/inv_upgrade"

        _, kwargs = mock_sub_modify.call_args
        assert kwargs["proration_behavior"] == "always_invoice"
        # payment_behavior / payment_settings are NOT passed to modify
        assert "payment_behavior" not in kwargs
        assert "payment_settings" not in kwargs
        payments = repo.list_payments(db, user_id="user-1")
        assert len(payments) == 1
        assert payments[0].status == "pending"
        assert payments[0].stripe_invoice_id == "inv_upgrade_123"

    @patch("app.services.subscription_service.stripe.Subscription.modify")
    @patch("app.services.subscription_service.stripe.Subscription.retrieve")
    def test_change_paid_to_paid_downgrade_uses_none_proration_and_no_payment_url(
        self,
        mock_sub_retrieve,
        mock_sub_modify,
        db,
        service,
        repo,
    ):
        old_plan = repo.create_plan(
            db,
            slug="proplus",
            name="Pro+",
            monthly_minutes=10000,
            price_cents=2999,
            currency="eur",
            stripe_price_id="price_proplus",
            is_active=True,
            sort_order=3,
        )
        new_plan = repo.create_plan(
            db,
            slug="starter",
            name="Starter",
            monthly_minutes=1000,
            price_cents=999,
            currency="eur",
            stripe_price_id="price_starter",
            is_active=True,
            sort_order=1,
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        repo.create_subscription(
            db,
            user_id="user-1",
            plan_id=old_plan.id,
            status="active",
            stripe_subscription_id="sub_live_456",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db.commit()

        mock_sub_retrieve.return_value = {"items": {"data": [{"id": "si_456"}]}}
        mock_sub_modify.return_value = {"latest_invoice": {"hosted_invoice_url": "https://pay.stripe.test/inv_downgrade"}}

        result = service.change_plan(db, "user-1", new_plan.slug, email="test@example.com")
        assert result["change"] == "downgrade"
        assert result["payment_url"] is None

        _, kwargs = mock_sub_modify.call_args
        assert kwargs["proration_behavior"] == "none"

    @patch("app.services.subscription_service.stripe.checkout.Session.create")
    def test_subscribe_paid_without_payment_method_uses_checkout_session(
        self,
        mock_checkout_create,
        db,
        service,
        repo,
    ):
        """When no payment method is provided, subscribe uses a Checkout Session."""
        repo.create_plan(
            db,
            slug="paid",
            name="Paid",
            monthly_minutes=5000,
            price_cents=1299,
            currency="eur",
            stripe_price_id="price_paid_123",
            is_active=True,
            sort_order=2,
        )
        db.commit()

        mock_checkout_create.return_value = {
            "url": "https://checkout.stripe.test/cs_no_pm",
        }

        with patch.object(service, "_get_or_create_stripe_customer", return_value="cus_123"):
            result = service.subscribe(db, "user-1", "paid", email="test@example.com")

        assert result["status"] == "active"
        assert result["payment_url"] == "https://checkout.stripe.test/cs_no_pm"

        _, kwargs = mock_checkout_create.call_args
        assert kwargs["mode"] == "subscription"
        assert "card" in kwargs["payment_method_types"]
        assert "ideal" in kwargs["payment_method_types"]

    @patch("app.services.subscription_service.stripe.checkout.Session.create")
    def test_change_plan_free_to_paid_uses_checkout_session(
        self,
        mock_checkout_create,
        db,
        service,
        repo,
    ):
        """Free → Paid change plan creates a Checkout Session (no Subscription.create)."""
        old_plan = repo.create_plan(
            db,
            slug="free",
            name="Free",
            monthly_minutes=600,
            price_cents=0,
            currency="eur",
            is_active=True,
            sort_order=0,
        )
        new_plan = repo.create_plan(
            db,
            slug="pro",
            name="Pro",
            monthly_minutes=10000,
            price_cents=2499,
            currency="eur",
            stripe_price_id="price_pro_123",
            is_active=True,
            sort_order=2,
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        repo.create_subscription(
            db,
            user_id="user-1",
            plan_id=old_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db.commit()

        mock_checkout_create.return_value = {
            "url": "https://checkout.stripe.test/cs_upgrade",
        }

        with patch.object(service, "_get_or_create_stripe_customer", return_value="cus_456"):
            result = service.change_plan(db, "user-1", new_plan.slug, email="test@example.com")

        assert result["status"] == "active"
        assert result["change"] == "upgrade"
        assert result["payment_url"] == "https://checkout.stripe.test/cs_upgrade"

        _, kwargs = mock_checkout_create.call_args
        assert kwargs["mode"] == "subscription"
        assert "card" in kwargs["payment_method_types"]

    @patch("app.services.subscription_service.stripe.checkout.Session.create")
    @patch("app.services.subscription_service.stripe.Subscription.modify")
    @patch("app.services.subscription_service.stripe.Subscription.retrieve")
    @patch("app.services.subscription_service.stripe.Subscription.list")
    def test_change_plan_paid_with_missing_local_stripe_id_reuses_existing_subscription(
        self,
        mock_sub_list,
        mock_sub_retrieve,
        mock_sub_modify,
        mock_checkout_create,
        db,
        service,
        repo,
    ):
        old_plan = repo.create_plan(
            db,
            slug="starter",
            name="Starter",
            monthly_minutes=1000,
            price_cents=999,
            currency="eur",
            stripe_price_id="price_starter",
            is_active=True,
            sort_order=1,
        )
        new_plan = repo.create_plan(
            db,
            slug="proplus",
            name="Pro+",
            monthly_minutes=10000,
            price_cents=2999,
            currency="eur",
            stripe_price_id="price_proplus",
            is_active=True,
            sort_order=3,
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db,
            user_id="user-1",
            plan_id=old_plan.id,
            status="active",
            stripe_customer_id="cus_existing",
            stripe_subscription_id=None,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db.commit()

        mock_sub_list.return_value = {
            "data": [
                {"id": "sub_existing_123", "status": "active"},
            ]
        }
        mock_sub_retrieve.return_value = {"items": {"data": [{"id": "si_existing_123"}]}}
        mock_sub_modify.return_value = {"latest_invoice": {"hosted_invoice_url": "https://pay.stripe.test/inv_reuse"}}

        with patch.object(service, "_get_or_create_stripe_customer", return_value="cus_existing"):
            result = service.change_plan(db, "user-1", new_plan.slug, email="test@example.com")

        assert result["change"] == "upgrade"
        assert result["payment_url"] == "https://pay.stripe.test/inv_reuse"
        mock_sub_modify.assert_called_once()
        mock_checkout_create.assert_not_called()
        db.refresh(sub)
        assert sub.stripe_subscription_id == "sub_existing_123"

    @patch("app.services.subscription_service.SubscriptionService._create_payment_method_update_portal_url")
    @patch("app.services.subscription_service.stripe.Subscription.modify")
    @patch("app.services.subscription_service.stripe.Subscription.retrieve")
    def test_change_paid_to_paid_upgrade_fallback_to_portal_on_no_pm(
        self,
        mock_sub_retrieve,
        mock_sub_modify,
        mock_payment_method_portal,
        db,
        service,
        repo,
    ):
        """When Subscription.modify raises 'no default PM', use payment-method portal."""
        old_plan = repo.create_plan(
            db,
            slug="starter",
            name="Starter",
            monthly_minutes=1000,
            price_cents=999,
            currency="eur",
            stripe_price_id="price_starter",
            is_active=True,
            sort_order=1,
        )
        new_plan = repo.create_plan(
            db,
            slug="proplus",
            name="Pro+",
            monthly_minutes=10000,
            price_cents=2999,
            currency="eur",
            stripe_price_id="price_proplus",
            is_active=True,
            sort_order=3,
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        repo.create_subscription(
            db,
            user_id="user-1",
            plan_id=old_plan.id,
            status="active",
            stripe_subscription_id="sub_live_noPM",
            stripe_customer_id="cus_noPM",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db.commit()

        mock_sub_retrieve.return_value = {"items": {"data": [{"id": "si_old"}]}}
        mock_sub_modify.side_effect = stripe.InvalidRequestError(
            "This customer has no attached payment source or default payment method.",
            param=None,
        )
        mock_payment_method_portal.return_value = "https://billing.stripe.test/pm-update"

        result = service.change_plan(db, "user-1", new_plan.slug, email="test@example.com")
        assert result["change"] == "upgrade"
        assert result["payment_url"] == "https://billing.stripe.test/pm-update"
        mock_payment_method_portal.assert_called_once_with("cus_noPM")


# ── Service: Renew edge cases ────────────────────────────────────────────────

class TestSubscriptionServiceRenewEdgeCases:
    def test_renew_missing_plan_returns_early(self, db, service, repo, free_plan):
        """If plan is deleted, renew_period logs warning and returns."""
        service.subscribe(db, "user-1", "free", email="test@example.com")
        sub = repo.get_active_subscription(db, "user-1")

        # Delete the plan so it can't be found
        db.delete(free_plan)
        db.commit()

        # Should not raise
        service.renew_period(db, sub)

        # Period should NOT change because plan was missing
        db.refresh(sub)


# ── Service: Stripe webhook handlers ─────────────────────────────────────────

class TestSubscriptionServiceWebhooks:
    def _make_sub(self, db, repo, free_plan, stripe_sub_id="sub_test_123"):
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
            stripe_subscription_id=stripe_sub_id,
        )
        db.commit()
        return sub

    def test_handle_invoice_paid(self, db, service, repo, free_plan):
        sub = self._make_sub(db, repo, free_plan)
        period_end_ts = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
        event = {
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "id": "inv_123",
                    "subscription": "sub_test_123",
                    "amount_paid": 999,
                    "currency": "eur",
                    "payment_intent": "pi_abc",
                    "lines": {
                        "data": [{"period": {"end": period_end_ts}}],
                    },
                }
            },
        }
        service.handle_stripe_event(db, event)

        # Verify payment was recorded
        payments = repo.list_payments(db, user_id="user-1")
        assert len(payments) == 1
        assert payments[0].amount_cents == 999
        assert payments[0].stripe_invoice_id == "inv_123"
        assert payments[0].status == "succeeded"

        # Verify subscription was updated
        db.refresh(sub)
        assert sub.status == "active"

    def test_handle_invoice_paid_updates_pending_payment(self, db, service, repo, free_plan):
        sub = self._make_sub(db, repo, free_plan)
        repo.create_payment(
            db,
            user_id="user-1",
            type="subscription",
            amount_cents=999,
            currency="EUR",
            status="pending",
            stripe_invoice_id="inv_pending_1",
            stripe_payment_intent_id="pi_pending_1",
            subscription_id=sub.id,
            description="subscription.pending:action=change_plan;previous_plan_slug=free;new_plan_slug=pro",
        )
        db.commit()

        period_end_ts = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
        event = {
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "id": "inv_pending_1",
                    "subscription": "sub_test_123",
                    "amount_paid": 1299,
                    "currency": "eur",
                    "payment_intent": "pi_pending_1",
                    "lines": {
                        "data": [{"period": {"end": period_end_ts}}],
                    },
                }
            },
        }
        service.handle_stripe_event(db, event)

        payments = repo.list_payments(db, user_id="user-1")
        assert len(payments) == 1
        assert payments[0].status == "succeeded"
        assert payments[0].amount_cents == 1299

    def test_handle_invoice_paid_no_subscription_id(self, db, service, repo):
        """Invoice without subscription key should be ignored."""
        event = {
            "type": "invoice.payment_succeeded",
            "data": {"object": {"id": "inv_no_sub"}},
        }
        service.handle_stripe_event(db, event)
        payments = repo.list_payments(db)
        assert len(payments) == 0

    def test_handle_invoice_paid_unknown_subscription(self, db, service, repo):
        """Invoice for unknown subscription ID should be ignored."""
        event = {
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "subscription": "sub_unknown_999",
                    "amount_paid": 100,
                    "currency": "eur",
                }
            },
        }
        service.handle_stripe_event(db, event)
        payments = repo.list_payments(db)
        assert len(payments) == 0

    def test_handle_invoice_failed(self, db, service, repo, free_plan):
        sub = self._make_sub(db, repo, free_plan)
        event = {
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "inv_fail_1",
                    "subscription": "sub_test_123",
                    "amount_due": 999,
                    "currency": "usd",
                }
            },
        }
        service.handle_stripe_event(db, event)

        # Sub should be past_due
        db.refresh(sub)
        assert sub.status == "past_due"

        # Failed payment recorded
        payments = repo.list_payments(db, user_id="user-1")
        assert len(payments) == 1
        assert payments[0].status == "failed"
        assert payments[0].amount_cents == 999

    def test_handle_invoice_failed_rolls_back_to_previous_plan(self, db, service, repo):
        previous_plan = repo.create_plan(
            db,
            slug="starter",
            name="Starter",
            monthly_minutes=1000,
            price_cents=999,
            currency="eur",
            stripe_price_id="price_starter",
            is_active=True,
            sort_order=1,
        )
        new_plan = repo.create_plan(
            db,
            slug="proplus",
            name="Pro+",
            monthly_minutes=10000,
            price_cents=2999,
            currency="eur",
            stripe_price_id="price_proplus",
            is_active=True,
            sort_order=3,
        )
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db,
            user_id="user-1",
            plan_id=new_plan.id,
            status="active",
            stripe_subscription_id="sub_test_rollback",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        repo.create_payment(
            db,
            user_id="user-1",
            type="subscription",
            amount_cents=2999,
            currency="EUR",
            status="pending",
            stripe_invoice_id="inv_fail_rollback",
            stripe_payment_intent_id="pi_fail_rollback",
            subscription_id=sub.id,
            description="subscription.pending:action=change_plan;previous_plan_slug=starter;new_plan_slug=proplus",
        )
        db.commit()

        event = {
            "type": "invoice.payment_failed",
            "data": {
                "object": {
                    "id": "inv_fail_rollback",
                    "subscription": "sub_test_rollback",
                    "amount_due": 2999,
                    "currency": "eur",
                    "payment_intent": "pi_fail_rollback",
                }
            },
        }
        service.handle_stripe_event(db, event)

        db.refresh(sub)
        assert sub.plan_id == previous_plan.id
        assert sub.status == "active"

        payments = repo.list_payments(db, user_id="user-1")
        assert len(payments) == 1
        assert payments[0].status == "failed"

    def test_handle_payment_intent_succeeded_updates_pending_topup(self, db, service, repo):
        payment = repo.create_payment(
            db,
            user_id="user-1",
            type="topup",
            amount_cents=499,
            currency="EUR",
            status="pending",
            stripe_payment_intent_id="pi_topup_123",
            description="Top-up pending",
        )
        db.commit()

        event = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_topup_123",
                    "amount_received": 499,
                    "currency": "eur",
                }
            },
        }
        service.handle_stripe_event(db, event)
        db.refresh(payment)
        assert payment.status == "succeeded"

    def test_handle_checkout_completed_topup_updates_pending_payment_with_pi(self, db, service, repo):
        pkg = repo.create_topup_package(
            db,
            name="500 min",
            minutes=500,
            price_cents=499,
            currency="eur",
            stripe_price_id="price_topup_500",
            is_active=True,
            sort_order=0,
        )
        payment = repo.create_payment(
            db,
            user_id="user-1",
            type="topup",
            amount_cents=499,
            currency="EUR",
            status="pending",
            description="Top-up pending",
        )
        db.commit()

        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "mode": "payment",
                    "payment_status": "unpaid",
                    "payment_intent": "pi_checkout_topup_1",
                    "amount_total": 499,
                    "currency": "eur",
                    "metadata": {
                        "jotigames_user_id": "user-1",
                        "topup_package_id": pkg.id,
                        "payment_record_id": payment.id,
                    },
                }
            },
        }

        service.handle_stripe_event(db, event)
        db.refresh(payment)
        assert payment.stripe_payment_intent_id == "pi_checkout_topup_1"
        assert payment.status == "pending"

    def test_handle_payment_intent_succeeded_creates_topup_purchase(self, db, service, repo):
        pkg = repo.create_topup_package(
            db,
            name="500 min",
            minutes=500,
            price_cents=499,
            currency="eur",
            stripe_price_id="price_topup_500",
            is_active=True,
            sort_order=0,
        )
        payment = repo.create_payment(
            db,
            user_id="user-1",
            type="topup",
            amount_cents=499,
            currency="EUR",
            status="pending",
            description="Top-up pending",
        )
        db.commit()

        event = {
            "type": "payment_intent.succeeded",
            "data": {
                "object": {
                    "id": "pi_topup_checkout_2",
                    "amount_received": 499,
                    "currency": "eur",
                    "metadata": {
                        "jotigames_user_id": "user-1",
                        "topup_package_id": pkg.id,
                        "payment_record_id": payment.id,
                    },
                }
            },
        }

        service.handle_stripe_event(db, event)
        db.refresh(payment)
        assert payment.status == "succeeded"
        assert payment.topup_purchase_id is not None
        purchases = repo.get_active_topups(db, "user-1")
        assert len(purchases) == 1
        assert purchases[0].minutes_remaining == 500

    @patch("app.services.subscription_service.stripe.checkout.Session.retrieve")
    def test_confirm_topup_checkout_session_finalizes_minutes(self, mock_session_retrieve, db, service, repo):
        pkg = repo.create_topup_package(
            db,
            name="500 min",
            minutes=500,
            price_cents=499,
            currency="eur",
            stripe_price_id="price_topup_500",
            is_active=True,
            sort_order=0,
        )
        payment = repo.create_payment(
            db,
            user_id="user-1",
            type="topup",
            amount_cents=499,
            currency="EUR",
            status="pending",
            description="Top-up pending",
        )
        db.commit()

        mock_session_retrieve.return_value = {
            "id": "cs_topup_123",
            "mode": "payment",
            "payment_status": "paid",
            "payment_intent": "pi_topup_confirm_1",
            "amount_total": 499,
            "currency": "eur",
            "metadata": {
                "jotigames_user_id": "user-1",
                "topup_package_id": pkg.id,
                "payment_record_id": payment.id,
            },
        }

        result = service.confirm_topup_checkout_session(db, "user-1", "cs_topup_123")
        assert result["payment_status"] == "paid"

        db.refresh(payment)
        assert payment.status == "succeeded"
        assert payment.topup_purchase_id is not None
        active_topups = repo.get_active_topups(db, "user-1")
        assert len(active_topups) == 1
        assert active_topups[0].minutes_remaining == 500

    @patch("app.services.subscription_service.stripe.checkout.Session.retrieve")
    def test_confirm_topup_checkout_session_rejects_wrong_user(self, mock_session_retrieve, db, service):
        mock_session_retrieve.return_value = {
            "id": "cs_topup_wrong_user",
            "mode": "payment",
            "payment_status": "paid",
            "metadata": {
                "jotigames_user_id": "other-user",
                "topup_package_id": "pkg-1",
                "payment_record_id": "pay-1",
            },
        }

        with pytest.raises(ValueError, match="forbidden"):
            service.confirm_topup_checkout_session(db, "user-1", "cs_topup_wrong_user")

    def test_handle_payment_intent_failed_updates_pending_topup(self, db, service, repo):
        payment = repo.create_payment(
            db,
            user_id="user-1",
            type="topup",
            amount_cents=499,
            currency="EUR",
            status="pending",
            stripe_payment_intent_id="pi_topup_456",
            description="Top-up pending",
        )
        db.commit()

        event = {
            "type": "payment_intent.payment_failed",
            "data": {
                "object": {
                    "id": "pi_topup_456",
                    "amount": 499,
                    "currency": "eur",
                }
            },
        }
        service.handle_stripe_event(db, event)
        db.refresh(payment)
        assert payment.status == "failed"

    def test_handle_invoice_failed_no_sub_id(self, db, service, repo):
        event = {
            "type": "invoice.payment_failed",
            "data": {"object": {"id": "inv_no_sub"}},
        }
        service.handle_stripe_event(db, event)
        payments = repo.list_payments(db)
        assert len(payments) == 0

    def test_handle_subscription_deleted(self, db, service, repo, free_plan):
        sub = self._make_sub(db, repo, free_plan)
        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_test_123"}},
        }
        service.handle_stripe_event(db, event)

        db.refresh(sub)
        assert sub.status == "cancelled"
        assert sub.cancelled_at is not None

    def test_handle_subscription_deleted_unknown(self, db, service, repo):
        """Deleting unknown subscription should be a no-op."""
        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_unknown"}},
        }
        service.handle_stripe_event(db, event)  # Should not raise

    def test_handle_subscription_updated_cancel_at_end(self, db, service, repo, free_plan):
        sub = self._make_sub(db, repo, free_plan)
        event = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_test_123",
                    "cancel_at_period_end": True,
                    "status": "active",
                }
            },
        }
        service.handle_stripe_event(db, event)
        db.refresh(sub)
        assert sub.cancel_at_period_end is True
        assert sub.status == "active"

    def test_handle_subscription_updated_status_mapping(self, db, service, repo, free_plan):
        """Stripe status 'canceled' maps to 'cancelled', 'unpaid' maps to 'past_due'."""
        sub = self._make_sub(db, repo, free_plan)

        # Stripe 'canceled' → our 'cancelled'
        event = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_test_123",
                    "cancel_at_period_end": False,
                    "status": "canceled",
                }
            },
        }
        service.handle_stripe_event(db, event)
        db.refresh(sub)
        assert sub.status == "cancelled"

    def test_handle_subscription_updated_unknown(self, db, service, repo):
        event = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_unknown",
                    "cancel_at_period_end": False,
                    "status": "active",
                }
            },
        }
        service.handle_stripe_event(db, event)  # Should not raise

    @patch("app.services.subscription_service.stripe.Subscription.retrieve")
    def test_handle_checkout_session_completed(self, mock_sub_retrieve, db, service, repo, free_plan):
        """checkout.session.completed links Stripe subscription to local record."""
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
        )
        db.commit()

        period_end_ts = int((now + timedelta(days=30)).timestamp())
        mock_sub_retrieve.return_value = {
            "current_period_end": period_end_ts,
        }

        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "subscription": "sub_checkout_456",
                    "customer": "cus_checkout_789",
                    "metadata": {
                        "jotigames_user_id": "user-1",
                        "plan_slug": "free",
                    },
                }
            },
        }
        service.handle_stripe_event(db, event)

        db.refresh(sub)
        assert sub.stripe_subscription_id == "sub_checkout_456"
        assert sub.stripe_customer_id == "cus_checkout_789"
        assert sub.status == "active"

    @patch("app.services.subscription_service.stripe.Subscription.cancel")
    @patch("app.services.subscription_service.stripe.Subscription.retrieve")
    def test_handle_checkout_session_completed_replaces_old_subscription(
        self, mock_sub_retrieve, mock_sub_cancel, db, service, repo, free_plan
    ):
        """checkout.session.completed cancels old Stripe sub when replace metadata present."""
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            stripe_subscription_id="sub_old_to_replace",
            current_period_start=now, current_period_end=now + timedelta(days=30),
        )
        db.commit()

        mock_sub_retrieve.return_value = {"current_period_end": int((now + timedelta(days=30)).timestamp())}

        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "subscription": "sub_new_replacement",
                    "customer": "cus_replacement",
                    "metadata": {
                        "jotigames_user_id": "user-1",
                        "plan_slug": "free",
                        "replace_stripe_subscription_id": "sub_old_to_replace",
                    },
                }
            },
        }
        service.handle_stripe_event(db, event)

        mock_sub_cancel.assert_called_once_with("sub_old_to_replace")
        db.refresh(sub)
        assert sub.stripe_subscription_id == "sub_new_replacement"

    def test_unknown_event_type_ignored(self, db, service):
        """Unrecognised event types should be silently ignored."""
        event = {"type": "unknown.event.type", "data": {"object": {}}}
        service.handle_stripe_event(db, event)  # Should not raise


# ── Service helpers: _next_period_end ────────────────────────────────────────

class TestNextPeriodEnd:
    def test_normal_month(self):
        start = datetime(2025, 1, 15, 12, 0, 0)
        end = _next_period_end(start)
        assert end.year == 2025
        assert end.month == 2
        assert end.day == 15

    def test_december_rollover(self):
        start = datetime(2025, 12, 10, 8, 30, 0)
        end = _next_period_end(start)
        assert end.year == 2026
        assert end.month == 1
        assert end.day == 10

    def test_day_clamped_to_28(self):
        """Days above 28 are clamped to avoid Feb 30 etc."""
        start = datetime(2025, 1, 31, 12, 0, 0)
        end = _next_period_end(start)
        assert end.day == 28
        assert end.month == 2

    def test_preserves_time(self):
        start = datetime(2025, 3, 15, 14, 30, 45)
        end = _next_period_end(start)
        assert end.hour == 14
        assert end.minute == 30
        assert end.second == 45


# ── Service helpers: _serialize_plan ─────────────────────────────────────────

class TestSerializePlan:
    def test_serialize_plan(self, db, repo, free_plan):
        result = _serialize_plan(free_plan)
        assert result is not None
        assert result["slug"] == "free"
        assert result["monthly_minutes"] == 600
        assert result["price_cents"] == 0
        assert result["currency"] == "eur"
        assert result["is_active"] is True
        assert "id" in result

    def test_serialize_plan_none(self):
        assert _serialize_plan(None) is None

    def test_serialize_plan_unlimited(self, db, repo, unlimited_plan):
        result = _serialize_plan(unlimited_plan)
        assert result["monthly_minutes"] is None


# ── Service helpers: _serialize_subscription ─────────────────────────────────

class TestSerializeSubscription:
    def test_serialize_subscription(self, db, repo, free_plan):
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
        )
        db.commit()

        result = _serialize_subscription(sub)
        assert result is not None
        assert result["plan_id"] == free_plan.id
        assert result["status"] == "active"
        assert result["cancel_at_period_end"] is False
        assert result["cancelled_at"] is None
        assert "current_period_start" in result
        assert "current_period_end" in result

    def test_serialize_subscription_none(self):
        assert _serialize_subscription(None) is None


# ── Super admin serialization helpers ────────────────────────────────────────

class TestSuperAdminSerializationHelpers:
    """Test that the super-admin dict-builders correctly read model attributes
    (validates the field-name fixes applied to super_admin.py)."""

    @staticmethod
    def _get_helpers():
        """Import the helpers from super_admin module."""
        from app.modules.super_admin import SuperAdminModule
        mod = SuperAdminModule()
        return mod

    def test_plan_to_dict(self, db, repo, free_plan):
        mod = self._get_helpers()
        result = mod._plan_to_dict(free_plan)
        assert result["id"] == free_plan.id
        assert result["slug"] == "free"
        assert result["monthly_minutes"] == 600
        assert result["price_cents"] == 0
        assert result["currency"] == "eur"
        assert result["is_active"] is True
        assert result["sort_order"] == 0
        assert result["created_at"] is not None
        assert result["updated_at"] is not None
        # Verify stripe_price_id is included
        assert "stripe_price_id" in result

    def test_plan_to_dict_unlimited(self, db, repo, unlimited_plan):
        mod = self._get_helpers()
        result = mod._plan_to_dict(unlimited_plan)
        assert result["monthly_minutes"] is None

    def test_topup_pkg_to_dict(self, db, repo):
        pkg = repo.create_topup_package(
            db, name="100 min", minutes=100, price_cents=199,
            currency="eur", stripe_price_id="price_topup_100", is_active=True, sort_order=2,
        )
        db.commit()

        mod = self._get_helpers()
        result = mod._topup_pkg_to_dict(pkg)
        assert result["name"] == "100 min"
        assert result["minutes"] == 100
        assert result["price_cents"] == 199
        assert result["currency"] == "eur"
        assert result["stripe_price_id"] == "price_topup_100"
        assert result["is_active"] is True
        assert result["sort_order"] == 2
        assert result["created_at"] is not None
        assert result["updated_at"] is not None

    def test_subscription_to_dict(self, db, repo, free_plan):
        now = datetime.now(UTC).replace(tzinfo=None)
        sub = repo.create_subscription(
            db, user_id="user-1", plan_id=free_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
            stripe_subscription_id="sub_stripe_test",
            stripe_customer_id="cus_stripe_test",
        )
        db.commit()

        mod = self._get_helpers()
        result = mod._subscription_to_dict(sub)
        assert result["id"] == sub.id
        assert result["user_id"] == "user-1"
        assert result["plan_id"] == free_plan.id
        assert result["status"] == "active"
        assert result["stripe_subscription_id"] == "sub_stripe_test"
        assert result["stripe_customer_id"] == "cus_stripe_test"
        assert result["cancel_at_period_end"] is False
        assert result["current_period_start"] is not None
        assert result["current_period_end"] is not None

    def test_payment_to_dict(self, db, repo):
        payment = repo.create_payment(
            db, user_id="user-1", amount_cents=2499, currency="eur",
            type="subscription", status="succeeded",
            stripe_payment_intent_id="pi_test",
            stripe_invoice_id="inv_test",
            subscription_id="sub-id-1",
            topup_purchase_id=None,
            description="Test payment",
        )
        db.commit()

        mod = self._get_helpers()
        result = mod._payment_to_dict(payment)
        assert result["id"] == payment.id
        assert result["user_id"] == "user-1"
        assert result["amount_cents"] == 2499
        assert result["currency"] == "eur"
        assert result["type"] == "subscription"
        assert result["status"] == "succeeded"
        assert result["stripe_payment_intent_id"] == "pi_test"
        assert result["stripe_invoice_id"] == "inv_test"
        assert result["subscription_id"] == "sub-id-1"
        assert result["topup_purchase_id"] is None
        assert result["description"] == "Test payment"
        assert result["created_at"] is not None

    def test_payment_to_dict_topup(self, db, repo):
        payment = repo.create_payment(
            db, user_id="user-1", amount_cents=499, currency="eur",
            type="topup", status="succeeded",
            topup_purchase_id="tp-123",
            description="Top-up: 100 minutes",
        )
        db.commit()

        mod = self._get_helpers()
        result = mod._payment_to_dict(payment)
        assert result["type"] == "topup"
        assert result["topup_purchase_id"] == "tp-123"


# ── Service: subscribe edge cases ────────────────────────────────────────────

class TestSubscriptionServiceSubscribeEdge:
    def test_subscribe_creates_payment_for_paid_plan(self, db, service, repo):
        """Paid plan subscription records a payment (Stripe mocked)."""
        # Create a paid plan without stripe_price_id so Stripe is skipped
        repo.create_plan(
            db, slug="paid-no-stripe", name="Paid No Stripe",
            monthly_minutes=5000, price_cents=999, currency="eur",
            is_active=True, sort_order=1,
        )
        db.commit()

        result = service.subscribe(db, "user-1", "paid-no-stripe", email="test@example.com")
        assert result["status"] == "active"

        # Verify payment was created
        payments = repo.list_payments(db, user_id="user-1")
        assert len(payments) == 1
        assert payments[0].amount_cents == 999
        assert payments[0].type == "subscription"

    def test_subscribe_free_plan_no_payment(self, db, service, repo, free_plan):
        """Free plan subscription does NOT create a payment record."""
        service.subscribe(db, "user-1", "free", email="test@example.com")
        payments = repo.list_payments(db, user_id="user-1")
        assert len(payments) == 0

    def test_subscribe_allocates_unlimited_balance(self, db, service, repo, unlimited_plan):
        """Unlimited plan subscription creates a balance with is_unlimited=True."""
        now = datetime.now(UTC).replace(tzinfo=None)
        # Unlimited plan has stripe_price_id, so create a simpler unlimited plan
        repo.create_plan(
            db, slug="unlimited-free", name="Unlimited Free",
            monthly_minutes=None, price_cents=0, currency="eur",
            is_active=True, sort_order=5,
        )
        db.commit()

        service.subscribe(db, "user-1", "unlimited-free", email="test@example.com")

        now = datetime.now(UTC)
        balance = repo.get_balance(db, "user-1", now.year, now.month)
        assert balance is not None
        assert balance.is_unlimited is True
        assert balance.minutes_allocated == 0

    def test_subscribe_inactive_plan_raises(self, db, service, repo):
        repo.create_plan(
            db, slug="inactive", name="Inactive", monthly_minutes=100,
            price_cents=0, currency="eur", is_active=False, sort_order=99,
        )
        db.commit()
        with pytest.raises(ValueError, match="notFound"):
            service.subscribe(db, "user-1", "inactive", email="test@example.com")


# ── Repository: Default Plan ─────────────────────────────────────────────────


class TestSubscriptionRepositoryDefaultPlan:
    def test_get_default_plan_none(self, db, repo, free_plan):
        """No plan is default initially."""
        assert repo.get_default_plan(db) is None

    def test_set_and_get_default_plan(self, db, repo, free_plan):
        result = repo.set_default_plan(db, free_plan.id)
        db.commit()
        assert result is not None
        assert result.is_default is True
        fetched = repo.get_default_plan(db)
        assert fetched is not None
        assert fetched.id == free_plan.id

    def test_set_default_plan_replaces_previous(self, db, repo, free_plan, pro_plan):
        repo.set_default_plan(db, free_plan.id)
        db.commit()
        repo.set_default_plan(db, pro_plan.id)
        db.commit()
        db.refresh(free_plan)
        db.refresh(pro_plan)
        assert free_plan.is_default is False
        assert pro_plan.is_default is True
        default = repo.get_default_plan(db)
        assert default.id == pro_plan.id

    def test_clear_default_plan(self, db, repo, free_plan):
        repo.set_default_plan(db, free_plan.id)
        db.commit()
        repo.clear_default_plan(db)
        db.commit()
        assert repo.get_default_plan(db) is None

    def test_set_default_plan_nonexistent_returns_none(self, db, repo):
        result = repo.set_default_plan(db, "nonexistent-id")
        assert result is None

    def test_default_plan_must_be_active(self, db, repo):
        """An inactive plan cannot be the default even if is_default is set."""
        plan = repo.create_plan(
            db, slug="inactive", name="Inactive", monthly_minutes=100,
            price_cents=0, currency="eur", is_active=False, sort_order=99,
        )
        db.commit()
        repo.set_default_plan(db, plan.id)
        db.commit()
        # get_default_plan filters for is_active=True
        assert repo.get_default_plan(db) is None


# ── Service: Auto-subscribe default plan ─────────────────────────────────────


class TestAutoSubscribeDefaultPlan:
    @patch("app.services.subscription_service.get_settings")
    def test_auto_subscribe_assigns_default_plan(self, mock_settings, db, repo, service, free_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=True, stripe_secret_key=None)
        repo.set_default_plan(db, free_plan.id)
        db.commit()

        result = service.auto_subscribe_default_plan(db, "user-auto-1")
        assert result is not None
        assert result["plan"] == "free"

        sub = repo.get_active_subscription(db, "user-auto-1")
        assert sub is not None
        assert sub.plan_id == free_plan.id

    @patch("app.services.subscription_service.get_settings")
    def test_auto_subscribe_skips_when_no_default(self, mock_settings, db, service):
        mock_settings.return_value = MagicMock(enable_monetisation=True, stripe_secret_key=None)
        result = service.auto_subscribe_default_plan(db, "user-auto-2")
        assert result is None

    @patch("app.services.subscription_service.get_settings")
    def test_auto_subscribe_skips_when_monetisation_disabled(self, mock_settings, db, repo, service, free_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=False)
        repo.set_default_plan(db, free_plan.id)
        db.commit()
        result = service.auto_subscribe_default_plan(db, "user-auto-3")
        assert result is None

    @patch("app.services.subscription_service.get_settings")
    def test_auto_subscribe_skips_when_user_already_subscribed(self, mock_settings, db, repo, service, free_plan, pro_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=True, stripe_secret_key=None)
        # Manually subscribe to free (no Stripe needed)
        service.subscribe(db, "user-auto-4", "free", email="test@example.com")
        # Now set pro as default
        repo.set_default_plan(db, pro_plan.id)
        db.commit()
        result = service.auto_subscribe_default_plan(db, "user-auto-4")
        assert result is None
        # Verify still on free
        sub = repo.get_active_subscription(db, "user-auto-4")
        assert sub.plan_id == free_plan.id

    @patch("app.services.subscription_service.get_settings")
    def test_auto_subscribe_allocates_minute_balance(self, mock_settings, db, repo, service, free_plan):
        mock_settings.return_value = MagicMock(enable_monetisation=True, stripe_secret_key=None)
        repo.set_default_plan(db, free_plan.id)
        db.commit()

        service.auto_subscribe_default_plan(db, "user-auto-5")
        now = datetime.now(UTC)
        balance = repo.get_balance(db, "user-auto-5", now.year, now.month)
        assert balance is not None
        assert balance.minutes_allocated == 600


# ── Super-admin: Default plan endpoints ──────────────────────────────────────


class TestSuperAdminDefaultPlanDict:
    """Ensure _plan_to_dict includes the is_default field."""

    def test_plan_to_dict_has_is_default(self, db, repo, free_plan):
        from app.modules.super_admin import SuperAdminModule
        d = SuperAdminModule._plan_to_dict(free_plan)
        assert "is_default" in d
        assert d["is_default"] is False

    def test_plan_to_dict_reflects_default(self, db, repo, free_plan):
        from app.modules.super_admin import SuperAdminModule
        repo.set_default_plan(db, free_plan.id)
        db.commit()
        db.refresh(free_plan)
        d = SuperAdminModule._plan_to_dict(free_plan)
        assert d["is_default"] is True


class TestSuperAdminCreatePersistence:
    @staticmethod
    def _route_endpoint(router, path, method):
        method_normalized = method.upper()
        for route in router.routes:
            if getattr(route, "path", None) == path and method_normalized in getattr(route, "methods", set()):
                return route.endpoint
        raise AssertionError(f"Route not found: {method} {path}")

    @staticmethod
    def _principal():
        return AuthenticatedPrincipal(
            principal_type="user",
            principal_id="super-admin-1",
            username="super-admin",
            roles=["ROLE_SUPER_ADMIN"],
        )

    def test_create_plan_endpoint_persists_record(self, db, repo):
        from app.modules.super_admin import SuperAdminModule, SubscriptionPlanCreateRequest

        endpoint = self._route_endpoint(
            SuperAdminModule().build_router(),
            "/super-admin/subscription/plans",
            "POST",
        )
        body = SubscriptionPlanCreateRequest(
            slug="starter",
            name="Starter",
            monthly_minutes=1200,
            price_cents=999,
            currency="eur",
            is_active=True,
            sort_order=5,
        )

        response = endpoint(body=body, principal=self._principal(), db=db)
        assert response.plan["slug"] == "starter"

        persisted = repo.get_plan_by_slug(db, "starter")
        assert persisted is not None
        assert persisted.name == "Starter"

    def test_create_plan_endpoint_rejects_duplicate_slug(self, db, repo, free_plan):
        from app.modules.super_admin import SuperAdminModule, SubscriptionPlanCreateRequest

        endpoint = self._route_endpoint(
            SuperAdminModule().build_router(),
            "/super-admin/subscription/plans",
            "POST",
        )
        body = SubscriptionPlanCreateRequest(
            slug=free_plan.slug,
            name="Duplicate",
            monthly_minutes=500,
            price_cents=0,
            currency="eur",
            is_active=True,
            sort_order=10,
        )

        with pytest.raises(HTTPException) as exc:
            endpoint(body=body, principal=self._principal(), db=db)
        assert exc.value.status_code == 409

    def test_create_topup_endpoint_persists_record(self, db, repo):
        from app.modules.super_admin import SuperAdminModule, TopupPackageCreateRequest

        endpoint = self._route_endpoint(
            SuperAdminModule().build_router(),
            "/super-admin/subscription/topup-packages",
            "POST",
        )
        body = TopupPackageCreateRequest(
            name="500 Minutes",
            minutes=500,
            price_cents=499,
            currency="eur",
            is_active=True,
        )

        response = endpoint(body=body, principal=self._principal(), db=db)
        assert response.package["name"] == "500 Minutes"

        persisted = repo.list_topup_packages(db, active_only=False)
        assert any(pkg.name == "500 Minutes" for pkg in persisted)


# ── Plan Update and Reorder Tests ────────────────────────────────────────────

class TestPlanUpdateAndReorder:
    """Tests for plan update with slug, exclude_unset, and reorder."""

    def test_update_plan_slug(self, db, repo, free_plan):
        """Slug can be updated via update_plan."""
        repo.update_plan(db, free_plan, slug="free-v2")
        db.commit()
        db.refresh(free_plan)
        assert free_plan.slug == "free-v2"

    def test_update_plan_slug_conflict_detected(self, db, repo, free_plan, pro_plan):
        """get_plan_by_slug finds the conflicting plan."""
        existing = repo.get_plan_by_slug(db, pro_plan.slug)
        assert existing is not None
        assert existing.id == pro_plan.id

    def test_update_plan_monthly_minutes_to_none(self, db, repo, free_plan):
        """Setting monthly_minutes to None (unlimited) works via update_plan."""
        repo.update_plan(db, free_plan, monthly_minutes=None)
        db.commit()
        db.refresh(free_plan)
        assert free_plan.monthly_minutes is None

    def test_update_plan_is_active_toggle(self, db, repo, free_plan):
        """is_active can be toggled."""
        assert free_plan.is_active is True
        repo.update_plan(db, free_plan, is_active=False)
        db.commit()
        db.refresh(free_plan)
        assert free_plan.is_active is False

    def test_list_plans_active_only_excludes_inactive(self, db, repo, free_plan, pro_plan):
        """list_plans with active_only=True doesn't return inactive plans."""
        repo.update_plan(db, free_plan, is_active=False)
        db.commit()
        active_plans = repo.list_plans(db, active_only=True)
        slugs = [p.slug for p in active_plans]
        assert "free" not in slugs
        assert "pro" in slugs

    def test_list_plans_all_includes_inactive(self, db, repo, free_plan, pro_plan):
        """list_plans with active_only=False returns all plans."""
        repo.update_plan(db, free_plan, is_active=False)
        db.commit()
        all_plans = repo.list_plans(db, active_only=False)
        slugs = [p.slug for p in all_plans]
        assert "free" in slugs
        assert "pro" in slugs

    def test_reorder_plans(self, db, repo, free_plan, pro_plan):
        """Updating sort_order changes list ordering."""
        repo.update_plan(db, free_plan, sort_order=5)
        repo.update_plan(db, pro_plan, sort_order=1)
        db.commit()
        plans = repo.list_plans(db, active_only=False)
        assert plans[0].slug == "pro"
        assert plans[1].slug == "free"

    def test_update_plan_updates_timestamp(self, db, repo, free_plan):
        """update_plan refreshes updated_at."""
        old_ts = free_plan.updated_at
        import time
        time.sleep(0.05)
        repo.update_plan(db, free_plan, name="Free V2")
        db.commit()
        db.refresh(free_plan)
        assert free_plan.updated_at >= old_ts
        assert free_plan.name == "Free V2"

    def test_update_request_exclude_unset(self):
        """SubscriptionPlanUpdateRequest.model_dump(exclude_unset=True) only includes explicitly set fields."""
        from app.modules.super_admin import SubscriptionPlanUpdateRequest
        body = SubscriptionPlanUpdateRequest(name="New Name")
        dumped = body.model_dump(exclude_unset=True)
        assert "name" in dumped
        assert "slug" not in dumped
        assert "is_active" not in dumped

    def test_update_request_includes_slug(self):
        """SubscriptionPlanUpdateRequest accepts slug field."""
        from app.modules.super_admin import SubscriptionPlanUpdateRequest
        body = SubscriptionPlanUpdateRequest(slug="new-slug", name="New Name")
        dumped = body.model_dump(exclude_unset=True)
        assert dumped["slug"] == "new-slug"
        assert dumped["name"] == "New Name"

    def test_reorder_request_validation(self):
        """ReorderPlansRequest requires at least one plan_id."""
        from app.modules.super_admin import ReorderPlansRequest
        req = ReorderPlansRequest(plan_ids=["id1", "id2"])
        assert req.plan_ids == ["id1", "id2"]

    def test_topup_reorder_request_validation(self):
        """ReorderTopupPackagesRequest accepts ordered package IDs."""
        from app.modules.super_admin import ReorderTopupPackagesRequest
        req = ReorderTopupPackagesRequest(package_ids=["pkg1", "pkg2"])
        assert req.package_ids == ["pkg1", "pkg2"]


# ── API Module: Smoke tests ──────────────────────────────────────────────────

class TestSubscriptionModuleSmoke:
    """Ensure the module can be instantiated and produces a router."""

    def test_module_instantiates(self):
        from app.modules.subscription import SubscriptionModule
        module = SubscriptionModule()
        assert module.name == "subscription"

    def test_module_builds_router(self):
        from app.modules.subscription import SubscriptionModule
        module = SubscriptionModule()
        router = module.build_router()
        route_paths = [r.path for r in router.routes]
        assert "/status" in route_paths or any("/status" in p for p in route_paths)

    def test_app_includes_subscription_routes(self):
        """The create_app function includes subscription endpoints."""
        from app.main import create_app
        app = create_app()
        paths = [r.path for r in app.routes]
        has_subscription = any("/subscription" in p for p in paths)
        assert has_subscription, f"No subscription routes found. Available: {paths[:10]}..."
