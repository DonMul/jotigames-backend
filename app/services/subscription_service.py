"""Business logic for subscriptions, game-minute billing, and Stripe integration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, Optional

import stripe
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import MinuteBalance, SubscriptionPlan, UserSubscription
from app.repositories.subscription_repository import SubscriptionRepository

logger = logging.getLogger(__name__)

# Default plan definitions seeded on first run when no plans exist.
DEFAULT_PLANS = [
    {"slug": "free", "name": "Free", "monthly_minutes": 600, "price_cents": 0, "sort_order": 0},
    {"slug": "beginner", "name": "Beginner", "monthly_minutes": 3000, "price_cents": 999, "sort_order": 1},
    {"slug": "pro", "name": "Pro", "monthly_minutes": 10000, "price_cents": 2499, "sort_order": 2},
    {"slug": "unlimited", "name": "Unlimited", "monthly_minutes": None, "price_cents": 4999, "sort_order": 3},
]


class SubscriptionService:
    """Orchestrates subscription lifecycle, Stripe checkout, and minute allocation."""

    def __init__(self, repository: Optional[SubscriptionRepository] = None) -> None:
        self._repo = repository or SubscriptionRepository()

    # ── Stripe helpers ───────────────────────────────────────────────────

    @staticmethod
    def _configure_stripe() -> None:
        settings = get_settings()
        if settings.stripe_secret_key:
            stripe.api_key = settings.stripe_secret_key

    def _get_or_create_stripe_customer(self, db: Session, user_id: str, email: str) -> str:
        """Return existing Stripe customer ID or create one."""
        self._configure_stripe()
        sub = self._repo.get_active_subscription(db, user_id)
        if sub and sub.stripe_customer_id:
            return sub.stripe_customer_id
        customer = stripe.Customer.create(
            email=email,
            metadata={"jotigames_user_id": user_id},
        )
        return customer.id

    @staticmethod
    def _customer_has_default_payment_method(customer_id: str) -> bool:
        """Return True when a Stripe customer has a default payment method/source."""
        customer = stripe.Customer.retrieve(customer_id)
        if isinstance(customer, dict):
            invoice_settings = customer.get("invoice_settings") or {}
            return bool(invoice_settings.get("default_payment_method") or customer.get("default_source"))
        invoice_settings = getattr(customer, "invoice_settings", None)
        default_payment_method = None
        if isinstance(invoice_settings, dict):
            default_payment_method = invoice_settings.get("default_payment_method")
        else:
            default_payment_method = getattr(invoice_settings, "default_payment_method", None)
        return bool(default_payment_method or getattr(customer, "default_source", None))

    @staticmethod
    def _create_payment_method_update_portal_url(customer_id: str) -> str:
        """Create Stripe billing portal session URL for payment method update."""
        settings = get_settings(refresh=True)
        return_url = f"{settings.app_public_base_url.rstrip('/')}/account/subscription"
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
            flow_data={"type": "payment_method_update"},
        )
        if isinstance(session, dict):
            return str(session.get("url", ""))
        return str(getattr(session, "url", ""))

    @staticmethod
    def _subscription_payment_method_types() -> list[str]:
        """Payment method types to expose for Checkout Sessions / invoices."""
        settings = get_settings(refresh=True)
        currency = str(settings.stripe_currency or "eur").lower()

        methods = ["card", "link", "paypal", "bancontact", "ideal", "sepa_debit"]
        if currency != "eur":
            methods = [m for m in methods if m not in {"bancontact", "ideal", "sepa_debit"}]
        return methods

    def _create_checkout_url(
        self,
        *,
        customer_id: str,
        price_id: str,
        user_id: str,
        plan_slug: str,
        previous_plan_slug: Optional[str] = None,
        replace_stripe_subscription_id: Optional[str] = None,
    ) -> str:
        """Create a Stripe Checkout Session for a subscription and return the URL.

        Using Checkout Sessions avoids the 'no default payment method' error
        because the hosted checkout page handles payment-method collection.
        """
        settings = get_settings(refresh=True)
        base_url = settings.app_public_base_url.rstrip("/")

        metadata: Dict[str, str] = {
            "jotigames_user_id": user_id,
            "plan_slug": plan_slug,
        }
        if previous_plan_slug:
            metadata["previous_plan_slug"] = previous_plan_slug
        if replace_stripe_subscription_id:
            metadata["replace_stripe_subscription_id"] = replace_stripe_subscription_id

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            payment_method_types=self._subscription_payment_method_types(),
            success_url=f"{base_url}/account/subscription?checkout=success",
            cancel_url=f"{base_url}/account/subscription",
            subscription_data={"metadata": metadata},
            metadata=metadata,
        )
        return str(self._stripe_value(session, "url", ""))

    def _create_topup_checkout_url(
        self,
        *,
        customer_id: str,
        user_id: str,
        package: Any,
        payment_record_id: str,
    ) -> str:
        settings = get_settings(refresh=True)
        base_url = settings.app_public_base_url.rstrip("/")

        metadata: Dict[str, str] = {
            "jotigames_user_id": user_id,
            "topup_package_id": str(package.id),
            "payment_record_id": payment_record_id,
        }

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="payment",
            line_items=[{"price": str(package.stripe_price_id), "quantity": 1}],
            payment_method_types=self._subscription_payment_method_types(),
            success_url=f"{base_url}/account/subscription?topup=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base_url}/account/subscription?topup=cancelled",
            metadata=metadata,
            payment_intent_data={"metadata": metadata},
        )
        return str(self._stripe_value(session, "url", ""))

    def confirm_topup_checkout_session(self, db: Session, user_id: str, session_id: str) -> Dict[str, Any]:
        self._configure_stripe()
        checkout_session = stripe.checkout.Session.retrieve(session_id)
        metadata = self._stripe_value(checkout_session, "metadata", {}) or {}
        session_user_id = str(metadata.get("jotigames_user_id") or "")
        session_mode = str(self._stripe_value(checkout_session, "mode", "") or "").lower()

        if session_mode != "payment":
            raise ValueError("topup.checkout.invalidMode")
        if session_user_id != str(user_id):
            raise ValueError("topup.checkout.forbidden")

        self._handle_topup_checkout_completed(db, checkout_session)

        payment_status = str(self._stripe_value(checkout_session, "payment_status", "") or "")
        return {
            "session_id": str(self._stripe_value(checkout_session, "id", session_id)),
            "payment_status": payment_status,
        }

    @staticmethod
    def _find_existing_stripe_subscription_id(customer_id: str) -> Optional[str]:
        subscriptions = stripe.Subscription.list(customer=customer_id, status="all", limit=20)
        data = subscriptions.get("data", []) if isinstance(subscriptions, dict) else getattr(subscriptions, "data", [])
        reusable_statuses = {"active", "trialing", "past_due", "unpaid", "incomplete"}
        for item in data:
            status = item.get("status") if isinstance(item, dict) else getattr(item, "status", None)
            if status not in reusable_statuses:
                continue
            subscription_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            if subscription_id:
                return str(subscription_id)
        return None

    @staticmethod
    def _extract_hosted_invoice_url(stripe_subscription: Any) -> Optional[str]:
        """Return Stripe hosted invoice URL from a subscription payload if available."""
        try:
            latest_invoice = None
            if isinstance(stripe_subscription, dict):
                latest_invoice = stripe_subscription.get("latest_invoice")
            else:
                latest_invoice = getattr(stripe_subscription, "latest_invoice", None)

            if not latest_invoice:
                return None

            if isinstance(latest_invoice, dict):
                return latest_invoice.get("hosted_invoice_url")

            invoice_obj = stripe.Invoice.retrieve(latest_invoice)
            if isinstance(invoice_obj, dict):
                return invoice_obj.get("hosted_invoice_url")
            return getattr(invoice_obj, "hosted_invoice_url", None)
        except Exception:
            return None

    @staticmethod
    def _extract_latest_invoice_identifiers(stripe_subscription: Any) -> tuple[Optional[str], Optional[str]]:
        """Return latest invoice id and payment_intent id from a subscription payload."""
        try:
            latest_invoice = None
            if isinstance(stripe_subscription, dict):
                latest_invoice = stripe_subscription.get("latest_invoice")
            else:
                latest_invoice = getattr(stripe_subscription, "latest_invoice", None)

            if not latest_invoice:
                return None, None

            if isinstance(latest_invoice, dict):
                return (
                    latest_invoice.get("id"),
                    latest_invoice.get("payment_intent"),
                )

            if isinstance(latest_invoice, str):
                invoice_obj = stripe.Invoice.retrieve(latest_invoice)
                if isinstance(invoice_obj, dict):
                    return (
                        invoice_obj.get("id"),
                        invoice_obj.get("payment_intent"),
                    )
                return (
                    getattr(invoice_obj, "id", None),
                    getattr(invoice_obj, "payment_intent", None),
                )

            return (
                getattr(latest_invoice, "id", None),
                getattr(latest_invoice, "payment_intent", None),
            )
        except Exception:
            return None, None

    @staticmethod
    def _build_payment_description(
        *,
        action: str,
        previous_plan_slug: Optional[str] = None,
        new_plan_slug: Optional[str] = None,
        fallback: str,
    ) -> str:
        parts = [f"action={action}"]
        if previous_plan_slug:
            parts.append(f"previous_plan_slug={previous_plan_slug}")
        if new_plan_slug:
            parts.append(f"new_plan_slug={new_plan_slug}")
        return "subscription.pending:" + ";".join(parts) if parts else fallback

    @staticmethod
    def _extract_previous_plan_slug(description: Optional[str]) -> Optional[str]:
        if not description or "subscription.pending:" not in description:
            return None
        payload = description.split("subscription.pending:", 1)[1]
        for token in payload.split(";"):
            token = token.strip()
            if token.startswith("previous_plan_slug="):
                value = token.split("=", 1)[1].strip()
                return value or None
        return None

    def _create_pending_subscription_payment(
        self,
        db: Session,
        *,
        sub: UserSubscription,
        amount_cents: int,
        currency: str,
        previous_plan_slug: Optional[str],
        new_plan_slug: str,
        stripe_invoice_id: Optional[str] = None,
        stripe_payment_intent_id: Optional[str] = None,
        action: str = "change_plan",
    ) -> None:
        self._repo.create_payment(
            db,
            user_id=sub.user_id,
            type="subscription",
            amount_cents=amount_cents,
            currency=currency,
            status="pending",
            stripe_invoice_id=stripe_invoice_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
            subscription_id=sub.id,
            description=self._build_payment_description(
                action=action,
                previous_plan_slug=previous_plan_slug,
                new_plan_slug=new_plan_slug,
                fallback="Subscription payment pending",
            ),
        )

    def _find_existing_payment_record_for_invoice(
        self,
        db: Session,
        *,
        sub: UserSubscription,
        stripe_invoice_id: Optional[str],
        stripe_payment_intent_id: Optional[str],
    ) -> Optional[Any]:
        if stripe_invoice_id:
            payment = self._repo.get_payment_by_stripe_invoice_id(db, stripe_invoice_id)
            if payment:
                return payment
        if stripe_payment_intent_id:
            payment = self._repo.get_payment_by_stripe_payment_intent_id(db, stripe_payment_intent_id)
            if payment:
                return payment
        return self._repo.get_latest_pending_payment_for_subscription(db, sub.id)

    @staticmethod
    def _stripe_value(obj: Any, key: str, default: Any = None) -> Any:
        """Read a field from either a Stripe object or a plain dict."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    # ── Plan seed ────────────────────────────────────────────────────────

    def seed_default_plans(self, db: Session) -> None:
        """Create default subscription plans if none exist yet."""
        existing = self._repo.list_plans(db)
        if existing:
            return
        for defn in DEFAULT_PLANS:
            self._repo.create_plan(db, **defn)
        db.commit()
        logger.info("Seeded %d default subscription plans", len(DEFAULT_PLANS))

    def auto_subscribe_default_plan(self, db: Session, user_id: str) -> Optional[Dict[str, Any]]:
        """Subscribe a newly-registered user to the platform default plan.

        Returns the subscription result dict or ``None`` when monetisation is
        disabled or no default plan is configured.  Failures are logged but
        never raised so that the registration flow is not blocked.
        """
        settings = get_settings()
        if not settings.enable_monetisation:
            return None

        default_plan = self._repo.get_default_plan(db)
        if not default_plan:
            return None

        # Skip if user already has an active subscription
        existing = self._repo.get_active_subscription(db, user_id)
        if existing:
            return None

        try:
            result = self.subscribe(db, user_id, default_plan.slug)
            logger.info("Auto-subscribed user %s to default plan '%s'", user_id, default_plan.slug)
            return result
        except Exception:
            logger.exception("Failed to auto-subscribe user %s to default plan '%s'", user_id, default_plan.slug)
            return None

    # ── Subscription lifecycle ───────────────────────────────────────────

    def get_user_subscription_summary(self, db: Session, user_id: str) -> Dict[str, Any]:
        """Return current subscription state + minute balance for a user."""
        settings = get_settings()
        if not settings.enable_monetisation:
            return {
                "monetisation_enabled": False,
                "subscription": None,
                "plan": None,
                "balance": {"minutes_allocated": 0, "minutes_used": 0, "minutes_remaining": None, "is_unlimited": True},
            }

        sub = self._repo.get_active_subscription(db, user_id)
        plan: Optional[SubscriptionPlan] = None
        if sub:
            plan = self._repo.get_plan_by_id(db, sub.plan_id)

        now = datetime.now(UTC).replace(tzinfo=None)
        balance = self._repo.get_balance(db, user_id, now.year, now.month)

        balance_dict: Dict[str, Any]
        if balance:
            remaining = None if balance.is_unlimited else max(0, balance.minutes_allocated - balance.minutes_used)
            balance_dict = {
                "minutes_allocated": balance.minutes_allocated,
                "minutes_used": balance.minutes_used,
                "minutes_remaining": remaining,
                "is_unlimited": balance.is_unlimited,
            }
        else:
            balance_dict = {"minutes_allocated": 0, "minutes_used": 0, "minutes_remaining": 0, "is_unlimited": False}

        # Add top-up remaining
        topups = self._repo.get_active_topups(db, user_id)
        topup_minutes_remaining = sum(t.minutes_remaining for t in topups)
        expiry_buckets: Dict[str, int] = {}
        for topup in topups:
            expires_at = getattr(topup, "expires_at", None)
            if not expires_at:
                continue
            expires_on = expires_at.date().isoformat()
            expiry_buckets[expires_on] = expiry_buckets.get(expires_on, 0) + int(topup.minutes_remaining or 0)
        topup_expiry_breakdown = [
            {"expires_on": expires_on, "minutes_remaining": minutes_remaining}
            for expires_on, minutes_remaining in sorted(expiry_buckets.items())
        ]

        return {
            "monetisation_enabled": True,
            "subscription": _serialize_subscription(sub) if sub else None,
            "plan": _serialize_plan(plan) if plan else None,
            "balance": balance_dict,
            "topup_minutes_remaining": topup_minutes_remaining,
            "topup_expiry_breakdown": topup_expiry_breakdown,
        }

    def subscribe(
        self,
        db: Session,
        user_id: str,
        plan_slug: str,
        *,
        email: str = "",
        stripe_payment_method_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Subscribe a user to a plan. For paid plans, create Stripe subscription."""
        plan = self._repo.get_plan_by_slug(db, plan_slug)
        if not plan or not plan.is_active:
            raise ValueError("subscription.plan.notFound")

        existing = self._repo.get_active_subscription(db, user_id)
        if existing:
            raise ValueError("subscription.alreadyActive")

        now = datetime.now(UTC).replace(tzinfo=None)
        period_end = _next_period_end(now)

        stripe_customer_id: Optional[str] = None
        stripe_subscription_id: Optional[str] = None
        payment_url: Optional[str] = None

        if plan.price_cents > 0 and plan.stripe_price_id:
            self._configure_stripe()
            stripe_customer_id = self._get_or_create_stripe_customer(db, user_id, email)

            if stripe_payment_method_id:
                stripe.PaymentMethod.attach(stripe_payment_method_id, customer=stripe_customer_id)
                stripe.Customer.modify(
                    stripe_customer_id,
                    invoice_settings={"default_payment_method": stripe_payment_method_id},
                )

            # Use Checkout Session – avoids "no default payment method" errors
            payment_url = self._create_checkout_url(
                customer_id=stripe_customer_id,
                price_id=plan.stripe_price_id,
                user_id=user_id,
                plan_slug=plan.slug,
            )
            # stripe_subscription_id will be set by checkout.session.completed webhook

        sub = self._repo.create_subscription(
            db,
            user_id=user_id,
            plan_id=plan.id,
            status="active",
            stripe_customer_id=stripe_customer_id,
            stripe_subscription_id=stripe_subscription_id,
            current_period_start=now,
            current_period_end=period_end,
        )

        # Allocate monthly minutes
        is_unlimited = plan.monthly_minutes is None
        allocated = plan.monthly_minutes or 0
        self._repo.get_or_create_balance(
            db,
            user_id,
            now.year,
            now.month,
            allocated=allocated,
            is_unlimited=is_unlimited,
        )

        # For Stripe-managed plans the payment record is created by the
        # webhook transition from pending -> succeeded/failed.  Only record
        # immediate success locally when there is no Stripe price.
        if plan.price_cents > 0 and plan.stripe_price_id:
            self._create_pending_subscription_payment(
                db,
                sub=sub,
                amount_cents=plan.price_cents,
                currency=plan.currency,
                previous_plan_slug=None,
                new_plan_slug=plan.slug,
                action="subscribe",
            )

        if plan.price_cents > 0 and not plan.stripe_price_id:
            self._repo.create_payment(
                db,
                user_id=user_id,
                type="subscription",
                amount_cents=plan.price_cents,
                currency=plan.currency,
                status="succeeded",
                subscription_id=sub.id,
                description=f"Subscription: {plan.name}",
            )

        db.commit()
        return {
            "subscription_id": sub.id,
            "plan": plan.slug,
            "status": sub.status,
            "payment_url": payment_url,
        }

    def change_plan(
        self,
        db: Session,
        user_id: str,
        new_plan_slug: str,
        *,
        email: str = "",
        stripe_payment_method_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upgrade or downgrade: swap plan on current subscription.

        For Stripe-managed subscriptions the plan change is prorated automatically.
        For free↔paid transitions we create/cancel the Stripe subscription.
        """
        new_plan = self._repo.get_plan_by_slug(db, new_plan_slug)
        if not new_plan or not new_plan.is_active:
            raise ValueError("subscription.plan.notFound")

        sub = self._repo.get_active_subscription(db, user_id)
        if not sub:
            # No active sub → just subscribe
            return self.subscribe(db, user_id, new_plan_slug, email=email, stripe_payment_method_id=stripe_payment_method_id)

        old_plan = self._repo.get_plan_by_id(db, sub.plan_id)

        if old_plan and old_plan.id == new_plan.id:
            raise ValueError("subscription.samePlan")

        now = datetime.now(UTC).replace(tzinfo=None)
        old_sort = old_plan.sort_order if old_plan else 0
        new_sort = new_plan.sort_order if new_plan else 0
        is_upgrade = new_sort > old_sort
        payment_url: Optional[str] = None

        # Stripe plan change (both plans are paid)
        if sub.stripe_subscription_id and new_plan.stripe_price_id:
            self._configure_stripe()
            customer_id = sub.stripe_customer_id
            if not customer_id:
                stripe_sub_existing = stripe.Subscription.retrieve(sub.stripe_subscription_id)
                customer_id = self._stripe_value(stripe_sub_existing, "customer")
            try:
                stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
                stripe_sub = stripe.Subscription.modify(
                    sub.stripe_subscription_id,
                    items=[{
                        "id": stripe_sub["items"]["data"][0]["id"],
                        "price": new_plan.stripe_price_id,
                    }],
                    proration_behavior="always_invoice" if is_upgrade else "none",
                    metadata={
                        "plan_slug": new_plan.slug,
                        "new_plan_slug": new_plan.slug,
                        "previous_plan_slug": old_plan.slug if old_plan else "",
                    },
                    expand=["latest_invoice"],
                )
                if is_upgrade:
                    payment_url = self._extract_hosted_invoice_url(stripe_sub)
                    stripe_invoice_id, stripe_payment_intent_id = self._extract_latest_invoice_identifiers(stripe_sub)
                    self._create_pending_subscription_payment(
                        db,
                        sub=sub,
                        amount_cents=new_plan.price_cents,
                        currency=new_plan.currency,
                        previous_plan_slug=old_plan.slug if old_plan else None,
                        new_plan_slug=new_plan.slug,
                        stripe_invoice_id=stripe_invoice_id,
                        stripe_payment_intent_id=stripe_payment_intent_id,
                        action="change_plan",
                    )
            except stripe.InvalidRequestError as exc:
                if "no attached payment source or default payment method" not in str(exc):
                    raise
                # Keep using the same Stripe subscription. Ask user to add/update
                # payment method in Stripe Billing Portal, then retry change-plan.
                payment_url = self._create_payment_method_update_portal_url(customer_id)

        # Free → Paid or missing local Stripe link: prefer reusing existing Stripe subscription
        elif not sub.stripe_subscription_id and new_plan.price_cents > 0 and new_plan.stripe_price_id:
            self._configure_stripe()
            customer_id = self._get_or_create_stripe_customer(db, user_id, email)

            if stripe_payment_method_id:
                stripe.PaymentMethod.attach(stripe_payment_method_id, customer=customer_id)
                stripe.Customer.modify(
                    customer_id,
                    invoice_settings={"default_payment_method": stripe_payment_method_id},
                )

            existing_stripe_subscription_id: Optional[str] = None
            if old_plan and old_plan.price_cents > 0:
                existing_stripe_subscription_id = self._find_existing_stripe_subscription_id(customer_id)

            if existing_stripe_subscription_id:
                try:
                    stripe_sub = stripe.Subscription.retrieve(existing_stripe_subscription_id)
                    stripe_sub = stripe.Subscription.modify(
                        existing_stripe_subscription_id,
                        items=[{
                            "id": stripe_sub["items"]["data"][0]["id"],
                            "price": new_plan.stripe_price_id,
                        }],
                        proration_behavior="always_invoice" if is_upgrade else "none",
                        metadata={
                            "plan_slug": new_plan.slug,
                            "new_plan_slug": new_plan.slug,
                            "previous_plan_slug": old_plan.slug if old_plan else "",
                        },
                        expand=["latest_invoice"],
                    )
                    if is_upgrade:
                        payment_url = self._extract_hosted_invoice_url(stripe_sub)
                        stripe_invoice_id, stripe_payment_intent_id = self._extract_latest_invoice_identifiers(stripe_sub)
                        self._create_pending_subscription_payment(
                            db,
                            sub=sub,
                            amount_cents=new_plan.price_cents,
                            currency=new_plan.currency,
                            previous_plan_slug=old_plan.slug if old_plan else None,
                            new_plan_slug=new_plan.slug,
                            stripe_invoice_id=stripe_invoice_id,
                            stripe_payment_intent_id=stripe_payment_intent_id,
                            action="change_plan",
                        )
                    self._repo.update_subscription(
                        db,
                        sub,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=existing_stripe_subscription_id,
                    )
                except stripe.InvalidRequestError as exc:
                    if "no attached payment source or default payment method" not in str(exc):
                        raise
                    payment_url = self._create_payment_method_update_portal_url(customer_id)
                    self._repo.update_subscription(
                        db,
                        sub,
                        stripe_customer_id=customer_id,
                        stripe_subscription_id=existing_stripe_subscription_id,
                    )
            else:
                payment_url = self._create_checkout_url(
                    customer_id=customer_id,
                    price_id=new_plan.stripe_price_id,
                    user_id=user_id,
                    plan_slug=new_plan.slug,
                    previous_plan_slug=old_plan.slug if old_plan else None,
                )
                self._repo.update_subscription(
                    db, sub,
                    stripe_customer_id=customer_id,
                )
                if is_upgrade:
                    self._create_pending_subscription_payment(
                        db,
                        sub=sub,
                        amount_cents=new_plan.price_cents,
                        currency=new_plan.currency,
                        previous_plan_slug=old_plan.slug if old_plan else None,
                        new_plan_slug=new_plan.slug,
                        action="change_plan",
                    )

        # Paid → Free: cancel Stripe sub
        elif sub.stripe_subscription_id and new_plan.price_cents == 0:
            self._configure_stripe()
            stripe.Subscription.cancel(sub.stripe_subscription_id)
            self._repo.update_subscription(
                db, sub,
                stripe_subscription_id=None,
            )

        self._repo.update_subscription(
            db, sub,
            plan_id=new_plan.id,
            cancel_at_period_end=False,
            cancelled_at=None,
        )

        # Update balance for current month
        is_unlimited = new_plan.monthly_minutes is None
        allocated = new_plan.monthly_minutes or 0
        balance = self._repo.get_balance(db, user_id, now.year, now.month)
        if balance:
            balance.minutes_allocated = allocated
            balance.is_unlimited = is_unlimited
            balance.updated_at = now
        else:
            self._repo.get_or_create_balance(db, user_id, now.year, now.month, allocated=allocated, is_unlimited=is_unlimited)

        db.commit()
        direction = "upgrade" if is_upgrade else "downgrade"
        return {
            "subscription_id": sub.id,
            "plan": new_plan.slug,
            "status": sub.status,
            "change": direction,
            "payment_url": payment_url,
        }

    def cancel_subscription(self, db: Session, user_id: str, *, immediate: bool = False) -> Dict[str, Any]:
        """Cancel a subscription (at period end by default, or immediately)."""
        sub = self._repo.get_active_subscription(db, user_id)
        if not sub:
            raise ValueError("subscription.notFound")

        now = datetime.now(UTC).replace(tzinfo=None)

        if sub.stripe_subscription_id:
            self._configure_stripe()
            if immediate:
                stripe.Subscription.cancel(sub.stripe_subscription_id)
            else:
                stripe.Subscription.modify(
                    sub.stripe_subscription_id,
                    cancel_at_period_end=True,
                )

        if immediate:
            self._repo.update_subscription(
                db, sub,
                status="cancelled",
                cancelled_at=now,
                cancel_at_period_end=False,
            )
        else:
            self._repo.update_subscription(
                db, sub,
                cancel_at_period_end=True,
                cancelled_at=now,
            )

        db.commit()
        return {"subscription_id": sub.id, "status": sub.status, "cancel_at_period_end": sub.cancel_at_period_end}

    def reactivate_subscription(self, db: Session, user_id: str) -> Dict[str, Any]:
        """Un-cancel a subscription that was set to cancel at period end."""
        sub = self._repo.get_active_subscription(db, user_id)
        if not sub:
            raise ValueError("subscription.notFound")
        if not sub.cancel_at_period_end:
            raise ValueError("subscription.notPendingCancel")

        if sub.stripe_subscription_id:
            self._configure_stripe()
            stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=False)

        self._repo.update_subscription(
            db, sub,
            cancel_at_period_end=False,
            cancelled_at=None,
        )
        db.commit()
        return {"subscription_id": sub.id, "status": sub.status, "cancel_at_period_end": sub.cancel_at_period_end}

    # ── Top-up purchase ──────────────────────────────────────────────────

    def purchase_topup(
        self,
        db: Session,
        user_id: str,
        package_id: str,
        *,
        email: str = "",
        stripe_payment_method_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Buy a one-off minute top-up package (12-month lifetime)."""
        pkg = self._repo.get_topup_package_by_id(db, package_id)
        if not pkg or not pkg.is_active:
            raise ValueError("topup.package.notFound")

        now = datetime.now(UTC).replace(tzinfo=None)
        expires = now + timedelta(days=365)

        stripe_pi_id: Optional[str] = None
        payment_url: Optional[str] = None
        if pkg.price_cents > 0 and pkg.stripe_price_id:
            self._configure_stripe()
            sub = self._repo.get_active_subscription(db, user_id)
            customer_id = sub.stripe_customer_id if sub and sub.stripe_customer_id else self._get_or_create_stripe_customer(db, user_id, email)

            payment = self._repo.create_payment(
                db,
                user_id=user_id,
                type="topup",
                amount_cents=pkg.price_cents,
                currency=pkg.currency,
                status="pending",
                description=f"Top-up: {pkg.name} ({pkg.minutes} minutes)",
            )

            payment_url = self._create_topup_checkout_url(
                customer_id=customer_id,
                user_id=user_id,
                package=pkg,
                payment_record_id=payment.id,
            )

            if sub and not sub.stripe_customer_id:
                self._repo.update_subscription(db, sub, stripe_customer_id=customer_id)

            db.commit()
            return {
                "minutes": pkg.minutes,
                "expires_at": expires.isoformat(),
                "payment_url": payment_url,
            }

        purchase = self._repo.create_topup_purchase(
            db,
            user_id=user_id,
            package_id=pkg.id,
            minutes_total=pkg.minutes,
            minutes_remaining=pkg.minutes,
            stripe_payment_intent_id=stripe_pi_id,
            expires_at=expires,
        )

        if pkg.price_cents > 0:
            self._repo.create_payment(
                db,
                user_id=user_id,
                type="topup",
                amount_cents=pkg.price_cents,
                currency=pkg.currency,
                status="succeeded",
                stripe_payment_intent_id=stripe_pi_id,
                topup_purchase_id=purchase.id,
                description=f"Top-up: {pkg.name} ({pkg.minutes} minutes)",
            )

        db.commit()
        return {
            "purchase_id": purchase.id,
            "minutes": pkg.minutes,
            "expires_at": expires.isoformat(),
            "payment_url": payment_url,
        }

    def expire_elapsed_topups(self, db: Session) -> int:
        now = datetime.now(UTC).replace(tzinfo=None)
        expired = self._repo.expire_topups(db, now)
        db.commit()
        return expired

    # ── Minute consumption ───────────────────────────────────────────────

    def consume_minutes(
        self,
        db: Session,
        user_id: str,
        game_id: str,
        minutes: int,
        team_count: int,
    ) -> bool:
        """Deduct game minutes from subscription balance, then top-ups.

        Returns True if minutes were successfully consumed, False if insufficient.
        """
        settings = get_settings()
        if not settings.enable_monetisation:
            return True

        now = datetime.now(UTC).replace(tzinfo=None)

        # Ensure balance reflects current plan allocation
        sub = self._repo.get_active_subscription(db, user_id)
        plan: Optional[SubscriptionPlan] = None
        if sub:
            plan = self._repo.get_plan_by_id(db, sub.plan_id)
        is_unlimited = plan is not None and plan.monthly_minutes is None
        allocated = (plan.monthly_minutes or 0) if plan else 0

        balance = self._repo.get_or_create_balance(
            db, user_id, now.year, now.month,
            allocated=allocated, is_unlimited=is_unlimited,
        )

        if balance.is_unlimited:
            self._repo.create_usage_log(
                db,
                user_id=user_id,
                game_id=game_id,
                minutes_consumed=minutes,
                team_count=team_count,
                source="subscription",
                balance_id=balance.id,
            )
            db.commit()
            return True

        remaining = balance.minutes_allocated - balance.minutes_used
        from_balance = min(minutes, remaining)
        from_topup = minutes - from_balance

        if from_balance > 0:
            self._repo.increment_minutes_used(db, balance.id, from_balance)
            self._repo.create_usage_log(
                db,
                user_id=user_id,
                game_id=game_id,
                minutes_consumed=from_balance,
                team_count=team_count,
                source="subscription",
                balance_id=balance.id,
            )

        if from_topup > 0:
            topups = self._repo.get_active_topups(db, user_id)
            still_needed = from_topup
            for topup in topups:
                if still_needed <= 0:
                    break
                take = min(still_needed, topup.minutes_remaining)
                self._repo.decrement_topup_minutes(db, topup.id, take)
                self._repo.create_usage_log(
                    db,
                    user_id=user_id,
                    game_id=game_id,
                    minutes_consumed=take,
                    team_count=team_count,
                    source="topup",
                    topup_purchase_id=topup.id,
                )
                still_needed -= take

            if still_needed > 0:
                db.rollback()
                return False

        db.commit()
        return True

    # ── Period renewal ───────────────────────────────────────────────────

    def renew_period(self, db: Session, sub: UserSubscription) -> None:
        """Advance subscription to next period and allocate fresh minutes."""
        plan = self._repo.get_plan_by_id(db, sub.plan_id)
        if not plan:
            logger.warning("Plan %s not found during renewal for sub %s", sub.plan_id, sub.id)
            return

        now = datetime.now(UTC).replace(tzinfo=None)
        new_start = sub.current_period_end
        new_end = _next_period_end(new_start)

        self._repo.update_subscription(
            db, sub,
            current_period_start=new_start,
            current_period_end=new_end,
        )

        is_unlimited = plan.monthly_minutes is None
        allocated = plan.monthly_minutes or 0
        self._repo.get_or_create_balance(
            db, sub.user_id, now.year, now.month,
            allocated=allocated,
            is_unlimited=is_unlimited,
        )

        db.commit()

    def process_pending_cancellations(self, db: Session) -> int:
        """Expire subscriptions whose cancel-at-period-end has been reached."""
        now = datetime.now(UTC).replace(tzinfo=None)
        pending = self._repo.list_subscriptions_pending_cancel(db, now)
        count = 0
        for sub in pending:
            self._repo.update_subscription(db, sub, status="cancelled")
            count += 1
        db.commit()
        return count

    # ── Stripe webhook handling ──────────────────────────────────────────

    def handle_stripe_event(self, db: Session, event: dict) -> None:
        """Process inbound Stripe webhook events."""
        event_type = event.get("type", "")
        data_object = event.get("data", {}).get("object", {})

        if event_type == "checkout.session.completed":
            self._handle_checkout_completed(db, data_object)
        elif event_type == "invoice.payment_succeeded":
            self._handle_invoice_paid(db, data_object)
        elif event_type == "invoice.payment_failed":
            self._handle_invoice_failed(db, data_object)
        elif event_type == "payment_intent.succeeded":
            self._handle_payment_intent_succeeded(db, data_object)
        elif event_type == "payment_intent.payment_failed":
            self._handle_payment_intent_failed(db, data_object)
        elif event_type == "customer.subscription.deleted":
            self._handle_subscription_deleted(db, data_object)
        elif event_type == "customer.subscription.updated":
            self._handle_subscription_updated(db, data_object)

    def _handle_checkout_completed(self, db: Session, checkout_session: dict) -> None:
        """Link Stripe subscription to local record after Checkout Session completes."""
        mode = str(checkout_session.get("mode") or "").strip().lower()
        metadata = checkout_session.get("metadata") or {}

        if mode == "payment" and metadata.get("topup_package_id") and metadata.get("jotigames_user_id"):
            self._handle_topup_checkout_completed(db, checkout_session)
            return

        stripe_sub_id = checkout_session.get("subscription")
        customer_id = checkout_session.get("customer")
        user_id = metadata.get("jotigames_user_id")
        replace_sub_id = metadata.get("replace_stripe_subscription_id")

        if not stripe_sub_id or not user_id:
            return

        # Cancel the old Stripe subscription when this checkout replaces it
        if replace_sub_id:
            try:
                self._configure_stripe()
                stripe.Subscription.cancel(replace_sub_id)
            except Exception:
                logger.warning("Failed to cancel replaced Stripe subscription %s", replace_sub_id)

        sub = self._repo.get_active_subscription(db, user_id)
        if not sub:
            logger.warning("checkout.session.completed: no active subscription for user %s", user_id)
            return

        updates: Dict[str, Any] = {
            "status": "active",
            "stripe_customer_id": customer_id,
            "stripe_subscription_id": stripe_sub_id,
        }

        # Sync billing period from Stripe
        try:
            self._configure_stripe()
            stripe_sub = stripe.Subscription.retrieve(stripe_sub_id)
            period_end = self._stripe_value(stripe_sub, "current_period_end")
            if period_end:
                updates["current_period_end"] = datetime.fromtimestamp(
                    period_end, tz=UTC
                ).replace(tzinfo=None)
        except Exception:
            logger.warning(
                "Failed to sync period from Stripe subscription %s", stripe_sub_id
            )

        self._repo.update_subscription(db, sub, **updates)

        # Allocate / refresh monthly minutes for the plan
        plan = self._repo.get_plan_by_id(db, sub.plan_id)
        if plan:
            now = datetime.now(UTC).replace(tzinfo=None)
            self._repo.get_or_create_balance(
                db,
                user_id,
                now.year,
                now.month,
                allocated=plan.monthly_minutes or 0,
                is_unlimited=plan.monthly_minutes is None,
            )

        db.commit()

    def _handle_topup_checkout_completed(self, db: Session, checkout_session: dict) -> None:
        metadata = checkout_session.get("metadata") or {}
        payment_record_id = metadata.get("payment_record_id")
        payment_intent_id = checkout_session.get("payment_intent")
        amount_total = int(checkout_session.get("amount_total") or 0)
        currency = str(checkout_session.get("currency") or "eur").upper()

        if not payment_record_id:
            return

        payment = self._repo.get_payment_by_id(db, payment_record_id)
        if not payment:
            return

        self._repo.update_payment(
            db,
            payment,
            stripe_payment_intent_id=payment_intent_id,
            amount_cents=amount_total or payment.amount_cents,
            currency=currency or payment.currency,
        )

        if str(checkout_session.get("payment_status") or "").lower() == "paid":
            self._finalize_topup_success(
                db,
                payment=payment,
                user_id=str(metadata.get("jotigames_user_id") or ""),
                package_id=str(metadata.get("topup_package_id") or ""),
                stripe_payment_intent_id=payment_intent_id,
                amount_cents=amount_total,
                currency=currency,
            )
        db.commit()

    def _finalize_topup_success(
        self,
        db: Session,
        *,
        payment: Any,
        user_id: str,
        package_id: str,
        stripe_payment_intent_id: Optional[str],
        amount_cents: int,
        currency: str,
    ) -> None:
        if payment.topup_purchase_id:
            self._repo.update_payment(
                db,
                payment,
                status="succeeded",
                stripe_payment_intent_id=stripe_payment_intent_id,
                amount_cents=amount_cents or payment.amount_cents,
                currency=currency or payment.currency,
            )
            return

        pkg = self._repo.get_topup_package_by_id(db, package_id)
        if not pkg:
            self._repo.update_payment(
                db,
                payment,
                status="failed",
                stripe_payment_intent_id=stripe_payment_intent_id,
                amount_cents=amount_cents or payment.amount_cents,
                currency=currency or payment.currency,
                description=(payment.description or "Top-up") + " [package missing]",
            )
            return

        expires = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=365)
        purchase = self._repo.create_topup_purchase(
            db,
            user_id=user_id,
            package_id=pkg.id,
            minutes_total=pkg.minutes,
            minutes_remaining=pkg.minutes,
            stripe_payment_intent_id=stripe_payment_intent_id,
            expires_at=expires,
        )

        self._repo.update_payment(
            db,
            payment,
            status="succeeded",
            stripe_payment_intent_id=stripe_payment_intent_id,
            topup_purchase_id=purchase.id,
            amount_cents=amount_cents or payment.amount_cents,
            currency=currency or payment.currency,
        )

    def _handle_invoice_paid(self, db: Session, invoice: dict) -> None:
        stripe_sub_id = invoice.get("subscription")
        if not stripe_sub_id:
            return
        sub = self._repo.get_subscription_by_stripe_id(db, stripe_sub_id)
        if not sub:
            return

        amount = invoice.get("amount_paid", 0)
        stripe_invoice_id = invoice.get("id")
        stripe_payment_intent_id = invoice.get("payment_intent")
        payment = self._find_existing_payment_record_for_invoice(
            db,
            sub=sub,
            stripe_invoice_id=stripe_invoice_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
        if payment:
            self._repo.update_payment(
                db,
                payment,
                status="succeeded",
                amount_cents=amount,
                currency=invoice.get("currency", "eur").upper(),
                stripe_invoice_id=stripe_invoice_id,
                stripe_payment_intent_id=stripe_payment_intent_id,
            )
        else:
            self._repo.create_payment(
                db,
                user_id=sub.user_id,
                type="subscription",
                amount_cents=amount,
                currency=invoice.get("currency", "eur").upper(),
                status="succeeded",
                stripe_invoice_id=stripe_invoice_id,
                stripe_payment_intent_id=stripe_payment_intent_id,
                subscription_id=sub.id,
                description="Stripe invoice paid",
            )

        # Advance period
        period_end = invoice.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end")
        if period_end:
            new_end = datetime.fromtimestamp(period_end, tz=UTC).replace(tzinfo=None)
            now = datetime.now(UTC).replace(tzinfo=None)
            self._repo.update_subscription(
                db, sub,
                status="active",
                current_period_start=now,
                current_period_end=new_end,
            )

            plan = self._repo.get_plan_by_id(db, sub.plan_id)
            if plan:
                self._repo.get_or_create_balance(
                    db, sub.user_id, now.year, now.month,
                    allocated=plan.monthly_minutes or 0,
                    is_unlimited=plan.monthly_minutes is None,
                )

        db.commit()

    def _handle_invoice_failed(self, db: Session, invoice: dict) -> None:
        stripe_sub_id = invoice.get("subscription")
        if not stripe_sub_id:
            return
        sub = self._repo.get_subscription_by_stripe_id(db, stripe_sub_id)
        if not sub:
            return

        stripe_invoice_id = invoice.get("id")
        stripe_payment_intent_id = invoice.get("payment_intent")
        payment = self._find_existing_payment_record_for_invoice(
            db,
            sub=sub,
            stripe_invoice_id=stripe_invoice_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
        )
        previous_plan_slug = self._extract_previous_plan_slug(
            payment.description if payment else None
        )

        if payment:
            self._repo.update_payment(
                db,
                payment,
                status="failed",
                amount_cents=invoice.get("amount_due", 0),
                currency=invoice.get("currency", "eur").upper(),
                stripe_invoice_id=stripe_invoice_id,
                stripe_payment_intent_id=stripe_payment_intent_id,
            )
        else:
            self._repo.create_payment(
                db,
                user_id=sub.user_id,
                type="subscription",
                amount_cents=invoice.get("amount_due", 0),
                currency=invoice.get("currency", "eur").upper(),
                status="failed",
                stripe_invoice_id=stripe_invoice_id,
                stripe_payment_intent_id=stripe_payment_intent_id,
                subscription_id=sub.id,
                description="Stripe invoice payment failed",
            )

        rollback_done = False
        if previous_plan_slug:
            previous_plan = self._repo.get_plan_by_slug(db, previous_plan_slug)
            if previous_plan and previous_plan.id != sub.plan_id:
                self._repo.update_subscription(
                    db,
                    sub,
                    plan_id=previous_plan.id,
                    status="active",
                    cancel_at_period_end=False,
                    cancelled_at=None,
                )

                now = datetime.now(UTC).replace(tzinfo=None)
                balance = self._repo.get_balance(db, sub.user_id, now.year, now.month)
                if balance:
                    balance.minutes_allocated = previous_plan.monthly_minutes or 0
                    balance.is_unlimited = previous_plan.monthly_minutes is None
                    balance.updated_at = now
                else:
                    self._repo.get_or_create_balance(
                        db,
                        sub.user_id,
                        now.year,
                        now.month,
                        allocated=previous_plan.monthly_minutes or 0,
                        is_unlimited=previous_plan.monthly_minutes is None,
                    )
                rollback_done = True

        self._repo.update_subscription(db, sub, status="active" if rollback_done else "past_due")
        db.commit()

    def _handle_payment_intent_succeeded(self, db: Session, payment_intent: dict) -> None:
        payment_intent_id = payment_intent.get("id")
        if not payment_intent_id:
            return

        payment = self._repo.get_payment_by_stripe_payment_intent_id(db, payment_intent_id)
        metadata = payment_intent.get("metadata") or {}
        if not payment and metadata.get("payment_record_id"):
            payment = self._repo.get_payment_by_id(db, str(metadata.get("payment_record_id")))
        amount = payment_intent.get("amount_received", payment_intent.get("amount", 0))
        currency = str(payment_intent.get("currency", "eur")).upper()
        if payment:
            package_id = str(metadata.get("topup_package_id") or "")
            user_id = str(metadata.get("jotigames_user_id") or payment.user_id)
            if payment.type == "topup" and package_id:
                self._finalize_topup_success(
                    db,
                    payment=payment,
                    user_id=user_id,
                    package_id=package_id,
                    stripe_payment_intent_id=payment_intent_id,
                    amount_cents=int(amount or 0),
                    currency=currency,
                )
            else:
                self._repo.update_payment(
                    db,
                    payment,
                    status="succeeded",
                    amount_cents=amount,
                    currency=currency,
                )
            db.commit()
            return

        user_id = metadata.get("jotigames_user_id")
        if not user_id:
            return
        payment = self._repo.create_payment(
            db,
            user_id=user_id,
            type="topup",
            amount_cents=amount,
            currency=currency,
            status="succeeded",
            stripe_payment_intent_id=payment_intent_id,
            description="Stripe payment intent succeeded",
        )
        package_id = str(metadata.get("topup_package_id") or "")
        if package_id:
            self._finalize_topup_success(
                db,
                payment=payment,
                user_id=str(user_id),
                package_id=package_id,
                stripe_payment_intent_id=payment_intent_id,
                amount_cents=int(amount or 0),
                currency=currency,
            )
        db.commit()

    def _handle_payment_intent_failed(self, db: Session, payment_intent: dict) -> None:
        payment_intent_id = payment_intent.get("id")
        if not payment_intent_id:
            return

        payment = self._repo.get_payment_by_stripe_payment_intent_id(db, payment_intent_id)
        metadata = payment_intent.get("metadata") or {}
        if not payment and metadata.get("payment_record_id"):
            payment = self._repo.get_payment_by_id(db, str(metadata.get("payment_record_id")))
        amount = payment_intent.get("amount", 0)
        currency = str(payment_intent.get("currency", "eur")).upper()
        if payment:
            self._repo.update_payment(
                db,
                payment,
                status="failed",
                stripe_payment_intent_id=payment_intent_id,
                amount_cents=amount,
                currency=currency,
            )
            db.commit()
            return

        user_id = metadata.get("jotigames_user_id")
        if not user_id:
            return
        self._repo.create_payment(
            db,
            user_id=user_id,
            type="topup",
            amount_cents=amount,
            currency=currency,
            status="failed",
            stripe_payment_intent_id=payment_intent_id,
            description="Stripe payment intent failed",
        )
        db.commit()

    def _handle_subscription_deleted(self, db: Session, stripe_sub: dict) -> None:
        sub = self._repo.get_subscription_by_stripe_id(db, stripe_sub.get("id", ""))
        if not sub:
            return
        now = datetime.now(UTC).replace(tzinfo=None)
        self._repo.update_subscription(db, sub, status="cancelled", cancelled_at=now)
        db.commit()

    def _handle_subscription_updated(self, db: Session, stripe_sub: dict) -> None:
        sub = self._repo.get_subscription_by_stripe_id(db, stripe_sub.get("id", ""))
        if not sub:
            return
        cancel_at_end = stripe_sub.get("cancel_at_period_end", False)
        stripe_status = stripe_sub.get("status", "active")
        status_map = {"active": "active", "past_due": "past_due", "canceled": "cancelled", "unpaid": "past_due"}
        mapped = status_map.get(stripe_status, sub.status)

        self._repo.update_subscription(db, sub, status=mapped, cancel_at_period_end=cancel_at_end)
        db.commit()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _next_period_end(start: datetime) -> datetime:
    """Calculate the next monthly period end from a start timestamp."""
    year = start.year
    month = start.month + 1
    if month > 12:
        month = 1
        year += 1
    day = min(start.day, 28)  # safe across all months
    return start.replace(year=year, month=month, day=day)


def _serialize_plan(plan: Optional[SubscriptionPlan]) -> Optional[Dict[str, Any]]:
    if not plan:
        return None
    return {
        "id": plan.id,
        "slug": plan.slug,
        "name": plan.name,
        "monthly_minutes": plan.monthly_minutes,
        "price_cents": plan.price_cents,
        "currency": plan.currency,
        "is_active": plan.is_active,
        "sort_order": plan.sort_order,
    }


def _serialize_subscription(sub: Optional[UserSubscription]) -> Optional[Dict[str, Any]]:
    if not sub:
        return None
    return {
        "id": sub.id,
        "plan_id": sub.plan_id,
        "status": sub.status,
        "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "cancel_at_period_end": sub.cancel_at_period_end,
        "cancelled_at": sub.cancelled_at.isoformat() if sub.cancelled_at else None,
    }
