from datetime import datetime
import json
from typing import Any, Dict, Optional
from uuid import uuid4

from argon2 import PasswordHasher
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

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


class SuperAdminModule(ApiModule):
    name = "super_admin"
    _password_hasher = PasswordHasher()

    def __init__(self) -> None:
        """Initialize repository dependencies for super-admin control-plane APIs."""
        self._repository = SuperAdminRepository()
        self._game_repository = GameRepository()

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

        return router
