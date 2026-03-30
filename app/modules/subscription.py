"""Subscription & billing API module for user-facing subscription management."""

from typing import Any, Dict, Optional

import stripe
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.config import get_settings
from app.dependencies import CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService, _serialize_plan


# ── Request / response models ────────────────────────────────────────────────


class SubscribePlanRequest(BaseModel):
    plan_slug: str = Field(min_length=1, max_length=64)
    stripe_payment_method_id: Optional[str] = None


class ChangePlanRequest(BaseModel):
    plan_slug: str = Field(min_length=1, max_length=64)
    stripe_payment_method_id: Optional[str] = None


class CancelSubscriptionRequest(BaseModel):
    immediate: bool = False


class PurchaseTopupRequest(BaseModel):
    package_id: str = Field(min_length=1, max_length=36)
    stripe_payment_method_id: Optional[str] = None


class ConfirmTopupCheckoutRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=255)


class SubscriptionSummaryResponse(BaseModel):
    monetisation_enabled: bool
    subscription: Optional[Dict[str, Any]] = None
    plan: Optional[Dict[str, Any]] = None
    balance: Dict[str, Any]
    topup_minutes_remaining: int = 0


class PlansListResponse(BaseModel):
    plans: list[Dict[str, Any]]


class TopupPackagesResponse(BaseModel):
    packages: list[Dict[str, Any]]


class SubscriptionActionResponse(BaseModel):
    result: Dict[str, Any]


class MonetisationStatusResponse(BaseModel):
    enabled: bool
    stripe_publishable_key: Optional[str] = None


# ── Module ───────────────────────────────────────────────────────────────────


class SubscriptionModule(ApiModule):
    """User-facing subscription management endpoints."""

    name = "subscription"

    def __init__(self) -> None:
        self._service = SubscriptionService()
        self._repo = SubscriptionRepository()

    @staticmethod
    def _require_user(principal: CurrentPrincipal) -> None:
        if principal.principal_type != "user":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="subscription.userRequired")

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/subscription", tags=["subscription"])

        # ── Public: monetisation status ───────────────────────────────────

        @router.get("/status", response_model=MonetisationStatusResponse, summary="Check monetisation status")
        def monetisation_status() -> MonetisationStatusResponse:
            """Return whether monetisation is enabled and the Stripe publishable key."""
            settings = get_settings(refresh=True)
            return MonetisationStatusResponse(
                enabled=settings.enable_monetisation,
                stripe_publishable_key=settings.stripe_publishable_key if settings.enable_monetisation else None,
            )

        # ── Public: list plans ────────────────────────────────────────────

        @router.get("/plans", response_model=PlansListResponse, summary="List subscription plans")
        def list_plans(db: DbSession) -> PlansListResponse:
            """List currently active subscription plans available for signup."""
            settings = get_settings(refresh=True)
            if not settings.enable_monetisation:
                return PlansListResponse(plans=[])
            plans = self._repo.list_plans(db, active_only=True)
            return PlansListResponse(plans=[_serialize_plan(p) for p in plans])

        # ── Public: list top-up packages ──────────────────────────────────

        @router.get("/topup-packages", response_model=TopupPackagesResponse, summary="List top-up packages")
        def list_topup_packages(db: DbSession) -> TopupPackagesResponse:
            settings = get_settings(refresh=True)
            if not settings.enable_monetisation:
                return TopupPackagesResponse(packages=[])
            packages = self._repo.list_topup_packages(db, active_only=True)
            return TopupPackagesResponse(
                packages=[
                    {
                        "id": p.id,
                        "name": p.name,
                        "minutes": p.minutes,
                        "price_cents": p.price_cents,
                        "currency": p.currency,
                        "stripe_price_id": p.stripe_price_id,
                    }
                    for p in packages
                ]
            )

        # ── Auth: my subscription summary ─────────────────────────────────

        @router.get("/me", response_model=SubscriptionSummaryResponse, summary="My subscription")
        def my_subscription(principal: CurrentPrincipal, db: DbSession) -> SubscriptionSummaryResponse:
            """Get current user subscription status and minute balance."""
            self._require_user(principal)
            summary = self._service.get_user_subscription_summary(db, principal.principal_id)
            return SubscriptionSummaryResponse(**summary)

        # ── Auth: subscribe ───────────────────────────────────────────────

        @router.post("/subscribe", response_model=SubscriptionActionResponse, status_code=status.HTTP_201_CREATED, summary="Subscribe to plan")
        def subscribe_to_plan(body: SubscribePlanRequest, principal: CurrentPrincipal, db: DbSession) -> SubscriptionActionResponse:
            self._require_user(principal)
            settings = get_settings(refresh=True)
            if not settings.enable_monetisation:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="subscription.disabled")
            try:
                result = self._service.subscribe(
                    db,
                    principal.principal_id,
                    body.plan_slug,
                    email=principal.username,
                    stripe_payment_method_id=body.stripe_payment_method_id,
                )
                return SubscriptionActionResponse(result=result)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        # ── Auth: change plan (upgrade / downgrade) ───────────────────────

        @router.post("/change-plan", response_model=SubscriptionActionResponse, summary="Change subscription plan")
        def change_plan(body: ChangePlanRequest, principal: CurrentPrincipal, db: DbSession) -> SubscriptionActionResponse:
            self._require_user(principal)
            settings = get_settings(refresh=True)
            if not settings.enable_monetisation:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="subscription.disabled")
            try:
                result = self._service.change_plan(
                    db,
                    principal.principal_id,
                    body.plan_slug,
                    email=principal.username,
                    stripe_payment_method_id=body.stripe_payment_method_id,
                )
                return SubscriptionActionResponse(result=result)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        # ── Auth: cancel ──────────────────────────────────────────────────

        @router.post("/cancel", response_model=SubscriptionActionResponse, summary="Cancel subscription")
        def cancel_subscription(body: CancelSubscriptionRequest, principal: CurrentPrincipal, db: DbSession) -> SubscriptionActionResponse:
            self._require_user(principal)
            try:
                result = self._service.cancel_subscription(db, principal.principal_id, immediate=body.immediate)
                return SubscriptionActionResponse(result=result)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        # ── Auth: reactivate ──────────────────────────────────────────────

        @router.post("/reactivate", response_model=SubscriptionActionResponse, summary="Reactivate pending cancellation")
        def reactivate_subscription(principal: CurrentPrincipal, db: DbSession) -> SubscriptionActionResponse:
            self._require_user(principal)
            try:
                result = self._service.reactivate_subscription(db, principal.principal_id)
                return SubscriptionActionResponse(result=result)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        # ── Auth: purchase top-up ─────────────────────────────────────────

        @router.post("/topup", response_model=SubscriptionActionResponse, status_code=status.HTTP_201_CREATED, summary="Purchase minute top-up")
        def purchase_topup(body: PurchaseTopupRequest, principal: CurrentPrincipal, db: DbSession) -> SubscriptionActionResponse:
            self._require_user(principal)
            settings = get_settings(refresh=True)
            if not settings.enable_monetisation:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="subscription.disabled")
            try:
                result = self._service.purchase_topup(
                    db,
                    principal.principal_id,
                    body.package_id,
                    email=principal.username,
                    stripe_payment_method_id=body.stripe_payment_method_id,
                )
                return SubscriptionActionResponse(result=result)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        @router.post("/topup/confirm", response_model=SubscriptionActionResponse, summary="Confirm top-up checkout session")
        def confirm_topup_checkout(body: ConfirmTopupCheckoutRequest, principal: CurrentPrincipal, db: DbSession) -> SubscriptionActionResponse:
            self._require_user(principal)
            settings = get_settings(refresh=True)
            if not settings.enable_monetisation:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="subscription.disabled")
            try:
                result = self._service.confirm_topup_checkout_session(
                    db,
                    principal.principal_id,
                    body.session_id,
                )
                return SubscriptionActionResponse(result=result)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        # ── Auth: my payment history ──────────────────────────────────────

        @router.get("/my-payments", summary="My payment history")
        def my_payments(
            principal: CurrentPrincipal,
            db: DbSession,
            limit: int = 20,
            offset: int = 0,
        ) -> Dict[str, Any]:
            """Return payment history for the authenticated user."""
            self._require_user(principal)
            records = self._repo.list_payments(db, user_id=principal.principal_id)
            paginated = records[offset:offset + limit]
            return {
                "payments": [
                    {
                        "id": p.id,
                        "amount_cents": p.amount_cents,
                        "currency": p.currency,
                        "type": p.type,
                        "description": p.description,
                        "status": p.status,
                        "created_at": p.created_at.isoformat() if p.created_at else None,
                    }
                    for p in paginated
                ],
                "total": len(records),
            }

        # ── Stripe webhook ────────────────────────────────────────────────

        @router.post("/webhook/stripe")
        async def stripe_webhook(request: Request, db: DbSession) -> Dict[str, str]:
            """Handle inbound Stripe webhooks for subscription lifecycle events."""
            settings = get_settings(refresh=True)
            payload = await request.body()
            sig_header = request.headers.get("stripe-signature", "")

            if settings.stripe_webhook_secret:
                try:
                    event = stripe.Webhook.construct_event(
                        payload, sig_header, settings.stripe_webhook_secret
                    )
                except (stripe.error.SignatureVerificationError, ValueError) as exc:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="webhook.invalidSignature") from exc
            else:
                import json
                event = json.loads(payload)

            self._service.handle_stripe_event(db, event)
            return {"status": "ok"}

        return router
