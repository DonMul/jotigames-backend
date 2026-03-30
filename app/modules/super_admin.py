from datetime import datetime, timedelta
import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from argon2 import PasswordHasher
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.dependencies import CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.repositories.game_repository import GameRepository
from app.repositories.super_admin_repository import SuperAdminRepository


class SuperAdminTokenStatusResponse(BaseModel):
    monetization_enabled: bool


class TokenBundlesResponse(BaseModel):
    bundles: list[Dict[str, Any]]


class TokenBundleUpsertRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    token_amount: int = Field(ge=0, le=1_000_000_000)
    price_cents: int = Field(ge=0, le=1_000_000_000)
    currency: str = Field(min_length=3, max_length=3, default="EUR")
    is_active: bool = True
    sort_order: int = 0


class TokenBundleResponse(BaseModel):
    bundle: Dict[str, Any]


class TokenCouponsResponse(BaseModel):
    coupons: list[Dict[str, Any]]


class TokenCouponCreateRequest(BaseModel):
    token_amount: int = Field(default=10, ge=0, le=1_000_000_000)
    infinite_tokens: bool = False
    max_redemptions: Optional[int] = Field(default=None, ge=1, le=1_000_000_000)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    comment: Optional[str] = Field(default=None, max_length=255)
    bulk_amount: int = Field(default=1, ge=1, le=500)


class TokenCouponCreateResponse(BaseModel):
    coupon_ids: list[str]


class TokenRulesResponse(BaseModel):
    rules: list[Dict[str, Any]]


class SuperAdminUsersResponse(BaseModel):
    users: list[Dict[str, Any]]


class SuperAdminUserResponse(BaseModel):
    user: Dict[str, Any]


class SuperAdminUserCreateRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=8, max_length=512)
    roles: list[str] = Field(default_factory=lambda: ["ROLE_USER"])
    is_verified: bool = False


class SuperAdminUserUpdateRequest(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = Field(default=None, min_length=3, max_length=120)
    password: Optional[str] = Field(default=None, min_length=8, max_length=512)
    roles: Optional[list[str]] = None
    is_verified: Optional[bool] = None


class SuperAdminMessageResponse(BaseModel):
    message_key: str


class TokenRuleCreateRequest(BaseModel):
    object_key: str = Field(min_length=1, max_length=64)
    label_key: str = Field(default="tokens.rule.custom", min_length=1, max_length=120)
    game_type: Optional[str] = Field(default=None, max_length=64)
    unit_size: int = Field(default=1, ge=1, le=1_000_000)
    tokens_per_unit: float = Field(default=1.0, ge=0)
    is_active: bool = True


class TokenRuleUpdateRequest(BaseModel):
    label_key: str = Field(default="tokens.rule.custom", min_length=1, max_length=120)
    game_type: Optional[str] = Field(default=None, max_length=64)
    unit_size: int = Field(default=1, ge=1, le=1_000_000)
    tokens_per_unit: float = Field(default=1.0, ge=0)
    is_active: bool = True


class TokenRuleResponse(BaseModel):
    rule: Dict[str, Any]


# ── Subscription / monetisation request & response models ─────────────────


class SubscriptionPlanCreateRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    monthly_minutes: Optional[int] = Field(default=None, ge=0)
    price_cents: int = Field(ge=0)
    currency: str = Field(default="eur", min_length=3, max_length=3)
    stripe_price_id: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0


class SubscriptionPlanUpdateRequest(BaseModel):
    slug: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    monthly_minutes: Optional[int] = Field(default=None, ge=0)
    price_cents: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    stripe_price_id: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class ReorderPlansRequest(BaseModel):
    plan_ids: List[str] = Field(min_length=1, description="Ordered list of plan IDs")


class SubscriptionPlansListResponse(BaseModel):
    plans: List[Dict[str, Any]]


class SubscriptionPlanResponse(BaseModel):
    plan: Dict[str, Any]


class TopupPackageCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    minutes: int = Field(ge=1)
    price_cents: int = Field(ge=0)
    currency: str = Field(default="eur", min_length=3, max_length=3)
    stripe_price_id: Optional[str] = Field(default=None, max_length=255)
    is_active: bool = True


class TopupPackageUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    minutes: Optional[int] = Field(default=None, ge=1)
    price_cents: Optional[int] = Field(default=None, ge=0)
    currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    stripe_price_id: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class ReorderTopupPackagesRequest(BaseModel):
    package_ids: List[str] = Field(min_length=1, description="Ordered list of top-up package IDs")


class TopupPackagesListResponse(BaseModel):
    packages: List[Dict[str, Any]]


class TopupPackageResponse(BaseModel):
    package: Dict[str, Any]


class AdminSubscriptionsListResponse(BaseModel):
    subscriptions: List[Dict[str, Any]]


class DefaultPlanRequest(BaseModel):
    plan_id: Optional[str] = Field(default=None, description="Plan ID to set as default, or null to clear")


class DefaultPlanResponse(BaseModel):
    default_plan: Optional[Dict[str, Any]] = None


class RevenueStatsResponse(BaseModel):
    summary: List[Dict[str, Any]]
    projected: Dict[str, Any]
    total_subscriptions: int
    active_subscriptions: int


class AdminUserSubscriptionResponse(BaseModel):
    subscription: Optional[Dict[str, Any]]
    balance: Dict[str, Any]
    topup_minutes: int
    payment_url: Optional[str] = None


class AdminSetUserSubscriptionRequest(BaseModel):
    plan_id: str = Field(min_length=1, description="Target subscription plan ID for the user")


class SuperAdminModule(ApiModule):
    name = "super_admin"
    _password_hasher = PasswordHasher()

    def __init__(self) -> None:
        """Initialize repository dependencies for super-admin control-plane APIs."""
        self._repository = SuperAdminRepository()
        self._game_repository = GameRepository()

        # Lazy imports to avoid circular deps – only used when routes hit
        from app.repositories.subscription_repository import SubscriptionRepository
        from app.services.subscription_service import SubscriptionService

        self._sub_repo = SubscriptionRepository()
        self._sub_service = SubscriptionService()

    @staticmethod
    def _ensure_super_admin(principal: CurrentPrincipal) -> None:
        """Guard helper that enforces admin-or-above access."""
        if principal.principal_type != "user" or not principal.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auth.user.superAdminRequired")

    @staticmethod
    def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize repository row values into JSON-friendly output payloads.

        Datetime values are converted to ISO strings while other values are
        passed through unchanged.
        """
        serialized: Dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, datetime):
                serialized[key] = value.isoformat()
            else:
                serialized[key] = value
        return serialized

    @staticmethod
    def _first_available_value(row: Dict[str, Any], candidates: list[str]) -> Any:
        """Return first available value for schema-compatible key fallbacks."""
        for candidate in candidates:
            if candidate in row:
                return row.get(candidate)
        return None

    @staticmethod
    def _normalize_roles(value: Any) -> list[str]:
        """Normalize role storage values from multiple DB serialization formats."""
        if value is None:
            return []
        if isinstance(value, list):
            return [str(role) for role in value if str(role).strip()]
        if isinstance(value, tuple):
            return [str(role) for role in value if str(role).strip()]

        raw = str(value).strip()
        if not raw:
            return []
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(role) for role in parsed if str(role).strip()]
            except json.JSONDecodeError:
                pass
        if "," in raw:
            return [part.strip() for part in raw.split(",") if part.strip()]
        return [raw]

    @staticmethod
    def _ensure_allowed_roles(roles: list[str]) -> list[str]:
        """Validate and normalize assignable user roles for admin mutations.

        Only `ROLE_USER`, `ROLE_ADMIN` and `ROLE_SUPER_ADMIN` are accepted.
        `ROLE_USER` is always enforced as base capability.
        """
        allowed = {"ROLE_USER", "ROLE_ADMIN", "ROLE_SUPER_ADMIN"}
        normalized = [str(role).strip() for role in roles if str(role).strip()]
        for role in normalized:
            if role not in allowed:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth.user.invalidRole")
        if "ROLE_USER" not in normalized:
            normalized.append("ROLE_USER")
        deduped: list[str] = []
        seen: set[str] = set()
        for role in normalized:
            if role in seen:
                continue
            seen.add(role)
            deduped.append(role)
        return deduped

    def _serialize_user_record(self, row: Dict[str, Any], settings: Any) -> Dict[str, Any]:
        """Map raw user table rows to stable API user response shape."""
        serialized = self._serialize_row(row)
        roles_raw = self._first_available_value(serialized, [settings.auth_user_roles_column, "roles"])
        return {
            "id": str(self._first_available_value(serialized, [settings.auth_user_id_column, "id"]) or ""),
            "email": str(self._first_available_value(serialized, [settings.auth_username_column, "email"]) or ""),
            "username": str(self._first_available_value(serialized, [settings.auth_user_display_name_column, "username"]) or ""),
            "roles": self._normalize_roles(roles_raw),
            "is_verified": bool(
                self._first_available_value(
                    serialized,
                    [settings.auth_user_is_verified_column, "is_verified", "isVerified"],
                )
            ),
            "created_at": self._first_available_value(serialized, ["created_at", "createdAt"]),
            "updated_at": self._first_available_value(serialized, ["updated_at", "updatedAt"]),
            "last_login_at": self._first_available_value(serialized, ["last_login_at", "lastLoginAt", "last_login", "lastLogin"]),
            "pending_email": self._first_available_value(serialized, [settings.auth_user_pending_email_column, "pending_email", "pendingEmail"]),
        }

    def build_router(self) -> APIRouter:
        """Build super-admin router with token economy and user governance APIs."""
        router = APIRouter(prefix="/super-admin", tags=["super-admin"])

        @router.get("/tokens", response_model=SuperAdminTokenStatusResponse, summary="Super admin token dashboard status")
        def token_status(principal: CurrentPrincipal, db: DbSession) -> SuperAdminTokenStatusResponse:
            """Return monetization readiness by checking token-table availability."""
            self._ensure_super_admin(principal)
            return SuperAdminTokenStatusResponse(monetization_enabled=self._repository.has_token_tables(db))

        @router.get("/tokens/bundles", response_model=TokenBundlesResponse, summary="Super admin list token bundles")
        def list_bundles(principal: CurrentPrincipal, db: DbSession) -> TokenBundlesResponse:
            """List all configured token bundles for storefront configuration."""
            self._ensure_super_admin(principal)
            bundles = [self._serialize_row(row) for row in self._repository.list_bundles(db)]
            return TokenBundlesResponse(bundles=bundles)

        @router.post("/tokens/bundles", response_model=TokenBundleResponse, status_code=status.HTTP_201_CREATED, summary="Super admin create token bundle")
        def create_bundle(body: TokenBundleUpsertRequest, principal: CurrentPrincipal, db: DbSession) -> TokenBundleResponse:
            """Create a new token bundle and return the persisted record."""
            self._ensure_super_admin(principal)

            try:
                bundle_id = self._repository.create_bundle_without_commit(db, body.model_dump())
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token.bundle.createFailed") from error

            bundle = self._repository.get_bundle_by_id(db, bundle_id)
            if bundle is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="token.bundle.fetchFailed")
            return TokenBundleResponse(bundle=self._serialize_row(bundle))

        @router.put("/tokens/bundles/{bundle_id}", response_model=TokenBundleResponse, summary="Super admin update token bundle")
        def update_bundle(bundle_id: str, body: TokenBundleUpsertRequest, principal: CurrentPrincipal, db: DbSession) -> TokenBundleResponse:
            """Update an existing token bundle by identifier."""
            self._ensure_super_admin(principal)

            existing = self._repository.get_bundle_by_id(db, bundle_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token.bundle.notFound")

            try:
                self._repository.update_bundle_without_commit(db, bundle_id, body.model_dump())
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token.bundle.updateFailed") from error

            bundle = self._repository.get_bundle_by_id(db, bundle_id)
            if bundle is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="token.bundle.fetchFailed")
            return TokenBundleResponse(bundle=self._serialize_row(bundle))

        @router.get("/tokens/coupons", response_model=TokenCouponsResponse, summary="Super admin list token coupons")
        def list_coupons(principal: CurrentPrincipal, db: DbSession) -> TokenCouponsResponse:
            """List issued token coupons including validity and redemption metadata."""
            self._ensure_super_admin(principal)
            coupons = [self._serialize_row(row) for row in self._repository.list_coupons(db)]
            return TokenCouponsResponse(coupons=coupons)

        @router.post("/tokens/coupons", response_model=TokenCouponCreateResponse, status_code=status.HTTP_201_CREATED, summary="Super admin create token coupons")
        def create_coupons(body: TokenCouponCreateRequest, principal: CurrentPrincipal, db: DbSession) -> TokenCouponCreateResponse:
            """Create one or more coupon codes in a single bulk operation."""
            self._ensure_super_admin(principal)

            payload = body.model_dump()
            payload["valid_from"] = body.valid_from
            payload["valid_until"] = body.valid_until

            creator_user_id = principal.principal_id if principal.principal_type == "user" else None

            try:
                coupon_ids = self._repository.create_coupons_without_commit(
                    db,
                    payload,
                    int(body.bulk_amount),
                    creator_user_id,
                )
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token.coupon.createFailed") from error

            return TokenCouponCreateResponse(coupon_ids=coupon_ids)

        @router.get("/tokens/rules", response_model=TokenRulesResponse, summary="Super admin list token rules")
        def list_rules(principal: CurrentPrincipal, db: DbSession) -> TokenRulesResponse:
            """List token earning rules used by game/object-based reward logic."""
            self._ensure_super_admin(principal)
            rules = [self._serialize_row(row) for row in self._repository.list_rules(db)]
            return TokenRulesResponse(rules=rules)

        @router.post("/tokens/rules", response_model=TokenRuleResponse, status_code=status.HTTP_201_CREATED, summary="Super admin create token rule")
        def create_rule(body: TokenRuleCreateRequest, principal: CurrentPrincipal, db: DbSession) -> TokenRuleResponse:
            """Create a token accrual rule for supported gameplay objects/types."""
            self._ensure_super_admin(principal)

            payload = body.model_dump()
            payload["tokens_per_unit"] = body.tokens_per_unit

            try:
                rule_id = self._repository.create_rule_without_commit(db, payload)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token.rule.createFailed") from error

            rule = self._repository.get_rule_by_id(db, rule_id)
            if rule is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="token.rule.fetchFailed")
            return TokenRuleResponse(rule=self._serialize_row(rule))

        @router.put("/tokens/rules/{rule_id}", response_model=TokenRuleResponse, summary="Super admin update token rule")
        def update_rule(rule_id: str, body: TokenRuleUpdateRequest, principal: CurrentPrincipal, db: DbSession) -> TokenRuleResponse:
            """Update an existing token rule with new economics or targeting values."""
            self._ensure_super_admin(principal)

            existing = self._repository.get_rule_by_id(db, rule_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token.rule.notFound")

            payload = body.model_dump()
            payload["tokens_per_unit"] = body.tokens_per_unit

            try:
                self._repository.update_rule_without_commit(db, rule_id, payload)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="token.rule.updateFailed") from error

            rule = self._repository.get_rule_by_id(db, rule_id)
            if rule is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="token.rule.fetchFailed")
            return TokenRuleResponse(rule=self._serialize_row(rule))

        @router.get("/game-types", response_model=Dict[str, Any], summary="Super admin game type availability map")
        def super_admin_game_types(principal: CurrentPrincipal, db: DbSession) -> Dict[str, Any]:
            """Return global game-type availability used by platform administration."""
            self._ensure_super_admin(principal)
            rows = self._game_repository.fetchGameTypeAvailability(db)
            return {
                "game_types": rows,
            }

        @router.get("/users", response_model=SuperAdminUsersResponse, summary="Super admin list users")
        def super_admin_users(principal: CurrentPrincipal, db: DbSession) -> SuperAdminUsersResponse:
            """List platform user accounts in normalized and sorted response format."""
            self._ensure_super_admin(principal)

            settings = get_settings()
            rows = self._repository.list_users(db, table_name=settings.auth_users_table)
            users = [self._serialize_user_record(row, settings) for row in rows]

            users.sort(key=lambda item: (str(item.get("email") or "").lower(), str(item.get("id") or "")))
            return SuperAdminUsersResponse(users=users)

        @router.get("/users/{user_id}", response_model=SuperAdminUserResponse, summary="Super admin get user")
        def super_admin_get_user(user_id: str, principal: CurrentPrincipal, db: DbSession) -> SuperAdminUserResponse:
            """Fetch a single user record by id for detailed inspection/editing."""
            self._ensure_super_admin(principal)
            settings = get_settings()
            row = self._repository.get_user_by_id(
                db,
                table_name=settings.auth_users_table,
                id_column=settings.auth_user_id_column,
                user_id=user_id,
            )
            if row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auth.user.notFound")
            return SuperAdminUserResponse(user=self._serialize_user_record(row, settings))

        @router.post("/users", response_model=SuperAdminUserResponse, status_code=status.HTTP_201_CREATED, summary="Super admin create user")
        def super_admin_create_user(body: SuperAdminUserCreateRequest, principal: CurrentPrincipal, db: DbSession) -> SuperAdminUserResponse:
            """Create a user account with validated uniqueness and allowed roles."""
            self._ensure_super_admin(principal)
            settings = get_settings()

            existing_rows = self._repository.list_users(db, table_name=settings.auth_users_table)
            email_normalized = str(body.email).strip().lower()
            username_normalized = str(body.username).strip().lower()
            for row in existing_rows:
                row_email = str(row.get(settings.auth_username_column) or row.get("email") or "").strip().lower()
                row_username = str(row.get(settings.auth_user_display_name_column) or row.get("username") or "").strip().lower()
                if row_email == email_normalized:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth.register.emailAlreadyUsed")
                if row_username == username_normalized:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth.register.usernameAlreadyUsed")

            user_table = self._repository.get_user_table(db, table_name=settings.auth_users_table)
            values: Dict[str, Any] = {
                settings.auth_user_id_column: str(uuid4()),
                settings.auth_username_column: str(body.email).strip(),
                settings.auth_user_display_name_column: str(body.username).strip(),
                settings.auth_password_column: self._password_hasher.hash(str(body.password)),
                settings.auth_user_roles_column: json.dumps(self._ensure_allowed_roles(body.roles)),
            }

            if settings.auth_user_is_verified_column in user_table.c:
                values[settings.auth_user_is_verified_column] = bool(body.is_verified)
            if "created_at" in user_table.c:
                values["created_at"] = datetime.utcnow()
            if "updated_at" in user_table.c:
                values["updated_at"] = datetime.utcnow()

            try:
                self._repository.create_user_without_commit(db, table_name=settings.auth_users_table, values=values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth.user.createFailed") from error

            created = self._repository.get_user_by_id(
                db,
                table_name=settings.auth_users_table,
                id_column=settings.auth_user_id_column,
                user_id=str(values[settings.auth_user_id_column]),
            )
            if created is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="auth.user.fetchFailed")

            # Auto-subscribe to default plan (best-effort, never blocks user creation)
            self._sub_service.auto_subscribe_default_plan(db, str(values[settings.auth_user_id_column]))

            return SuperAdminUserResponse(user=self._serialize_user_record(created, settings))

        @router.put("/users/{user_id}", response_model=SuperAdminUserResponse, summary="Super admin update user")
        def super_admin_update_user(user_id: str, body: SuperAdminUserUpdateRequest, principal: CurrentPrincipal, db: DbSession) -> SuperAdminUserResponse:
            """Update mutable user fields including credentials, roles, and flags."""
            self._ensure_super_admin(principal)
            settings = get_settings()

            existing = self._repository.get_user_by_id(
                db,
                table_name=settings.auth_users_table,
                id_column=settings.auth_user_id_column,
                user_id=user_id,
            )
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auth.user.notFound")

            values: Dict[str, Any] = {}
            if body.email is not None:
                values[settings.auth_username_column] = str(body.email).strip()
            if body.username is not None:
                values[settings.auth_user_display_name_column] = str(body.username).strip()
            if body.password is not None:
                values[settings.auth_password_column] = self._password_hasher.hash(str(body.password))
            if body.roles is not None:
                values[settings.auth_user_roles_column] = json.dumps(self._ensure_allowed_roles(body.roles))

            user_table = self._repository.get_user_table(db, table_name=settings.auth_users_table)
            if body.is_verified is not None and settings.auth_user_is_verified_column in user_table.c:
                values[settings.auth_user_is_verified_column] = bool(body.is_verified)
            if "updated_at" in user_table.c:
                values["updated_at"] = datetime.utcnow()

            try:
                self._repository.update_user_without_commit(
                    db,
                    table_name=settings.auth_users_table,
                    id_column=settings.auth_user_id_column,
                    user_id=user_id,
                    values=values,
                )
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth.user.updateFailed") from error

            updated = self._repository.get_user_by_id(
                db,
                table_name=settings.auth_users_table,
                id_column=settings.auth_user_id_column,
                user_id=user_id,
            )
            if updated is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="auth.user.fetchFailed")
            return SuperAdminUserResponse(user=self._serialize_user_record(updated, settings))

        @router.get("/users/{user_id}/games", summary="Super admin list games owned by user")
        def super_admin_user_games(user_id: str, principal: CurrentPrincipal, db: DbSession) -> Dict[str, Any]:
            """List game summaries for games owned by a specific user."""
            self._ensure_super_admin(principal)
            games = self._game_repository.fetchGameSummariesByOwnerId(db, user_id)
            serialized = [self._serialize_row(g) for g in games]
            return {"games": serialized}

        @router.delete("/users/{user_id}", response_model=SuperAdminMessageResponse, summary="Super admin delete user")
        def super_admin_delete_user(user_id: str, principal: CurrentPrincipal, db: DbSession) -> SuperAdminMessageResponse:
            """Delete a user account while preventing self-deletion by caller."""
            self._ensure_super_admin(principal)
            if str(principal.principal_id) == str(user_id):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth.user.cannotDeleteSelf")

            settings = get_settings()
            existing = self._repository.get_user_by_id(
                db,
                table_name=settings.auth_users_table,
                id_column=settings.auth_user_id_column,
                user_id=user_id,
            )
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auth.user.notFound")

            try:
                self._repository.delete_user_without_commit(
                    db,
                    table_name=settings.auth_users_table,
                    id_column=settings.auth_user_id_column,
                    user_id=user_id,
                )
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="auth.user.deleteFailed") from error

            return SuperAdminMessageResponse(message_key="auth.user.deleted")

        # ── Subscription plan management ──────────────────────────────────

        @router.get("/subscription/plans", response_model=SubscriptionPlansListResponse, summary="List all subscription plans")
        def admin_list_plans(principal: CurrentPrincipal, db: DbSession) -> SubscriptionPlansListResponse:
            self._ensure_super_admin(principal)
            plans = self._sub_repo.list_plans(db, active_only=False)
            return SubscriptionPlansListResponse(
                plans=[self._serialize_row(self._plan_to_dict(p)) for p in plans]
            )

        @router.post("/subscription/plans", response_model=SubscriptionPlanResponse, status_code=status.HTTP_201_CREATED, summary="Create subscription plan")
        def admin_create_plan(body: SubscriptionPlanCreateRequest, principal: CurrentPrincipal, db: DbSession) -> SubscriptionPlanResponse:
            self._ensure_super_admin(principal)
            existing = self._sub_repo.get_plan_by_slug(db, body.slug)
            if existing is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="subscription.slugConflict")
            plan = self._sub_repo.create_plan(
                db,
                slug=body.slug,
                name=body.name,
                monthly_minutes=body.monthly_minutes,
                price_cents=body.price_cents,
                currency=body.currency,
                stripe_price_id=body.stripe_price_id,
                is_active=body.is_active,
                sort_order=body.sort_order,
            )
            try:
                db.commit()
            except IntegrityError as error:
                db.rollback()
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="subscription.slugConflict") from error
            except Exception as error:
                db.rollback()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="subscription.planCreateFailed") from error
            db.refresh(plan)
            return SubscriptionPlanResponse(plan=self._serialize_row(self._plan_to_dict(plan)))

        @router.put("/subscription/plans/reorder", response_model=SubscriptionPlansListResponse, summary="Reorder subscription plans")
        def admin_reorder_plans(body: ReorderPlansRequest, principal: CurrentPrincipal, db: DbSession) -> SubscriptionPlansListResponse:
            self._ensure_super_admin(principal)
            for idx, plan_id in enumerate(body.plan_ids):
                plan = self._sub_repo.get_plan_by_id(db, plan_id)
                if plan is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription.planNotFound")
                self._sub_repo.update_plan(db, plan, sort_order=idx)
            db.commit()
            plans = self._sub_repo.list_plans(db, active_only=False)
            return SubscriptionPlansListResponse(
                plans=[self._serialize_row(self._plan_to_dict(p)) for p in plans]
            )

        @router.put("/subscription/plans/{plan_id}", response_model=SubscriptionPlanResponse, summary="Update subscription plan")
        def admin_update_plan(plan_id: str, body: SubscriptionPlanUpdateRequest, principal: CurrentPrincipal, db: DbSession) -> SubscriptionPlanResponse:
            self._ensure_super_admin(principal)
            plan = self._sub_repo.get_plan_by_id(db, plan_id)
            if plan is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription.planNotFound")
            updates = body.model_dump(exclude_unset=True)
            if "slug" in updates and updates["slug"] != plan.slug:
                existing = self._sub_repo.get_plan_by_slug(db, updates["slug"])
                if existing and existing.id != plan.id:
                    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="subscription.slugConflict")
            self._sub_repo.update_plan(db, plan, **updates)
            db.commit()
            db.refresh(plan)
            return SubscriptionPlanResponse(plan=self._serialize_row(self._plan_to_dict(plan)))

        @router.post("/subscription/seed-plans", response_model=SuperAdminMessageResponse, summary="Seed default plans")
        def admin_seed_plans(principal: CurrentPrincipal, db: DbSession) -> SuperAdminMessageResponse:
            self._ensure_super_admin(principal)
            self._sub_service.seed_default_plans(db)
            return SuperAdminMessageResponse(message_key="subscription.planSeeded")

        # ── Default plan for new users ────────────────────────────────────

        @router.get("/subscription/default-plan", response_model=DefaultPlanResponse, summary="Get default plan for new users")
        def admin_get_default_plan(principal: CurrentPrincipal, db: DbSession) -> DefaultPlanResponse:
            self._ensure_super_admin(principal)
            plan = self._sub_repo.get_default_plan(db)
            return DefaultPlanResponse(
                default_plan=self._serialize_row(self._plan_to_dict(plan)) if plan else None
            )

        @router.put("/subscription/default-plan", response_model=DefaultPlanResponse, summary="Set default plan for new users")
        def admin_set_default_plan(body: DefaultPlanRequest, principal: CurrentPrincipal, db: DbSession) -> DefaultPlanResponse:
            self._ensure_super_admin(principal)
            if body.plan_id is None:
                self._sub_repo.clear_default_plan(db)
                db.commit()
                return DefaultPlanResponse(default_plan=None)
            plan = self._sub_repo.set_default_plan(db, body.plan_id)
            if not plan:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription.planNotFound")
            db.commit()
            return DefaultPlanResponse(
                default_plan=self._serialize_row(self._plan_to_dict(plan))
            )

        # ── Top-up package management ─────────────────────────────────────

        @router.get("/subscription/topup-packages", response_model=TopupPackagesListResponse, summary="List top-up packages")
        def admin_list_topup_packages(principal: CurrentPrincipal, db: DbSession) -> TopupPackagesListResponse:
            self._ensure_super_admin(principal)
            packages = self._sub_repo.list_topup_packages(db, active_only=False)
            return TopupPackagesListResponse(
                packages=[self._serialize_row(self._topup_pkg_to_dict(p)) for p in packages]
            )

        @router.post("/subscription/topup-packages", response_model=TopupPackageResponse, status_code=status.HTTP_201_CREATED, summary="Create top-up package")
        def admin_create_topup_package(body: TopupPackageCreateRequest, principal: CurrentPrincipal, db: DbSession) -> TopupPackageResponse:
            self._ensure_super_admin(principal)
            pkg = self._sub_repo.create_topup_package(
                db,
                name=body.name,
                minutes=body.minutes,
                price_cents=body.price_cents,
                currency=body.currency,
                stripe_price_id=body.stripe_price_id,
                is_active=body.is_active,
            )
            try:
                db.commit()
            except Exception as error:
                db.rollback()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="subscription.topupPackageCreateFailed") from error
            db.refresh(pkg)
            return TopupPackageResponse(package=self._serialize_row(self._topup_pkg_to_dict(pkg)))

        @router.put("/subscription/topup-packages/reorder", response_model=TopupPackagesListResponse, summary="Reorder top-up packages")
        def admin_reorder_topup_packages(body: ReorderTopupPackagesRequest, principal: CurrentPrincipal, db: DbSession) -> TopupPackagesListResponse:
            self._ensure_super_admin(principal)
            for idx, package_id in enumerate(body.package_ids):
                pkg = self._sub_repo.get_topup_package_by_id(db, package_id)
                if pkg is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription.topupPackageNotFound")
                self._sub_repo.update_topup_package(db, pkg, sort_order=idx)
            db.commit()
            packages = self._sub_repo.list_topup_packages(db, active_only=False)
            return TopupPackagesListResponse(
                packages=[self._serialize_row(self._topup_pkg_to_dict(p)) for p in packages]
            )

        @router.put("/subscription/topup-packages/{package_id}", response_model=TopupPackageResponse, summary="Update top-up package")
        def admin_update_topup_package(package_id: str, body: TopupPackageUpdateRequest, principal: CurrentPrincipal, db: DbSession) -> TopupPackageResponse:
            self._ensure_super_admin(principal)
            pkg = self._sub_repo.get_topup_package_by_id(db, package_id)
            if pkg is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription.topupPackageNotFound")
            updates = body.model_dump(exclude_unset=True)
            self._sub_repo.update_topup_package(db, pkg, **updates)
            db.commit()
            db.refresh(pkg)
            return TopupPackageResponse(package=self._serialize_row(self._topup_pkg_to_dict(pkg)))

        # ── Subscriptions overview & user subscription management ─────────

        @router.get("/subscription/subscriptions", response_model=AdminSubscriptionsListResponse, summary="List all user subscriptions")
        def admin_list_subscriptions(principal: CurrentPrincipal, db: DbSession) -> AdminSubscriptionsListResponse:
            self._ensure_super_admin(principal)
            subs = self._sub_repo.list_all_subscriptions(db)
            return AdminSubscriptionsListResponse(
                subscriptions=[self._serialize_row(self._subscription_to_dict(s)) for s in subs]
            )

        @router.get("/subscription/users/{user_id}", response_model=AdminUserSubscriptionResponse, summary="View user subscription")
        def admin_get_user_subscription(user_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminUserSubscriptionResponse:
            self._ensure_super_admin(principal)
            summary = self._sub_service.get_user_subscription_summary(db, user_id)
            return AdminUserSubscriptionResponse(
                subscription=summary.get("subscription"),
                balance=summary["balance"],
                topup_minutes=summary.get("topup_minutes_remaining", 0),
            )

        @router.put("/subscription/users/{user_id}", response_model=AdminUserSubscriptionResponse, summary="Set user subscription plan")
        def admin_set_user_subscription(
            user_id: str,
            body: AdminSetUserSubscriptionRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> AdminUserSubscriptionResponse:
            self._ensure_super_admin(principal)
            settings = get_settings()

            user_row = self._repository.get_user_by_id(
                db,
                table_name=settings.auth_users_table,
                id_column=settings.auth_user_id_column,
                user_id=user_id,
            )
            if user_row is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="auth.user.notFound")

            plan = self._sub_repo.get_plan_by_id(db, body.plan_id)
            if plan is None or not plan.is_active:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription.planNotFound")

            email_value = str(user_row.get(settings.auth_username_column) or user_row.get("email") or "").strip()

            payment_url: Optional[str] = None
            try:
                result = self._sub_service.change_plan(db, user_id, plan.slug, email=email_value)
                payment_url = result.get("payment_url")
            except ValueError as error:
                detail = str(error)
                if detail == "subscription.samePlan":
                    payment_url = None
                elif detail == "subscription.plan.notFound":
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subscription.planNotFound") from error
                else:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail) from error

            summary = self._sub_service.get_user_subscription_summary(db, user_id)
            return AdminUserSubscriptionResponse(
                subscription=summary.get("subscription"),
                balance=summary["balance"],
                topup_minutes=summary.get("topup_minutes_remaining", 0),
                payment_url=payment_url,
            )

        # ── Revenue statistics ────────────────────────────────────────────

        @router.get("/subscription/revenue", response_model=RevenueStatsResponse, summary="Revenue statistics")
        def admin_revenue_stats(principal: CurrentPrincipal, db: DbSession) -> RevenueStatsResponse:
            self._ensure_super_admin(principal)
            summary = self._sub_repo.revenue_summary(db)
            projected_until = datetime.utcnow() + timedelta(days=30)
            projected = self._sub_repo.projected_revenue(db, until=projected_until)
            all_subs = self._sub_repo.list_all_subscriptions(db)
            active_count = sum(1 for s in all_subs if s.status == "active")
            return RevenueStatsResponse(
                summary=[self._serialize_row(g) for g in summary.get("groups", [])],
                projected=projected if projected else {"total_cents": 0, "subscription_count": 0},
                total_subscriptions=len(all_subs),
                active_subscriptions=active_count,
            )

        # ── Payments log ──────────────────────────────────────────────────

        @router.get("/subscription/payments", summary="List payment records")
        def admin_list_payments(
            principal: CurrentPrincipal,
            db: DbSession,
            user_id: Optional[str] = None,
            limit: int = 100,
            offset: int = 0,
        ) -> Dict[str, Any]:
            self._ensure_super_admin(principal)
            payments = self._sub_repo.list_payments(db, user_id=user_id)
            # Apply offset/limit in-memory for simplicity
            paginated = payments[offset:offset + limit]
            return {
                "payments": [self._serialize_row(self._payment_to_dict(p)) for p in paginated],
            }

        return router

    # ── Helpers for serialising subscription ORM objects ──────────────────

    @staticmethod
    def _plan_to_dict(plan) -> Dict[str, Any]:
        return {
            "id": plan.id,
            "slug": plan.slug,
            "name": plan.name,
            "monthly_minutes": plan.monthly_minutes,
            "price_cents": plan.price_cents,
            "currency": plan.currency,
            "stripe_price_id": plan.stripe_price_id,
            "is_active": plan.is_active,
            "is_default": plan.is_default,
            "sort_order": plan.sort_order,
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
        }

    @staticmethod
    def _topup_pkg_to_dict(pkg) -> Dict[str, Any]:
        return {
            "id": pkg.id,
            "name": pkg.name,
            "minutes": pkg.minutes,
            "price_cents": pkg.price_cents,
            "currency": pkg.currency,
            "stripe_price_id": pkg.stripe_price_id,
            "is_active": pkg.is_active,
            "sort_order": pkg.sort_order,
            "created_at": pkg.created_at,
            "updated_at": pkg.updated_at,
        }

    @staticmethod
    def _subscription_to_dict(sub) -> Dict[str, Any]:
        return {
            "id": sub.id,
            "user_id": sub.user_id,
            "plan_id": sub.plan_id,
            "status": sub.status,
            "stripe_subscription_id": sub.stripe_subscription_id,
            "stripe_customer_id": sub.stripe_customer_id,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "created_at": sub.created_at,
            "updated_at": sub.updated_at,
        }

    @staticmethod
    def _payment_to_dict(payment) -> Dict[str, Any]:
        return {
            "id": payment.id,
            "user_id": payment.user_id,
            "amount_cents": payment.amount_cents,
            "currency": payment.currency,
            "type": payment.type,
            "stripe_payment_intent_id": payment.stripe_payment_intent_id,
            "stripe_invoice_id": payment.stripe_invoice_id,
            "subscription_id": payment.subscription_id,
            "topup_purchase_id": payment.topup_purchase_id,
            "description": payment.description,
            "status": payment.status,
            "created_at": payment.created_at,
        }
