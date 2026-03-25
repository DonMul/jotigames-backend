from datetime import UTC, datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import Table, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class SuperAdminRepository(GameLogicStateRepository):
    @staticmethod
    def _pick_column(table: Table, candidates: list[str]) -> Optional[str]:
        """Return first existing column name among candidate aliases."""
        for candidate in candidates:
            if candidate in table.c:
                return candidate
        return None

    def get_token_bundle_table(self, db: DbSession) -> Table:
        """Return reflected token bundle table."""
        return self._get_table(db, "token_bundle")

    def get_token_coupon_table(self, db: DbSession) -> Table:
        """Return reflected token coupon table."""
        return self._get_table(db, "token_coupon")

    def get_token_usage_rule_table(self, db: DbSession) -> Table:
        """Return reflected token usage rule table."""
        return self._get_table(db, "token_usage_rule")

    def get_user_table(self, db: DbSession, *, table_name: str) -> Table:
        """Return reflected user table for configured auth schema."""
        return self._get_table(db, table_name)

    def list_users(self, db: DbSession, *, table_name: str) -> list[Dict[str, Any]]:
        """List all users from configured auth table."""
        table = self._get_table(db, table_name)
        rows = db.execute(select(table)).mappings().all()
        return [dict(row) for row in rows]

    def get_user_by_id(
        self,
        db: DbSession,
        *,
        table_name: str,
        id_column: str,
        user_id: str,
    ) -> Dict[str, Any] | None:
        """Fetch single user by configured id column."""
        table = self._get_table(db, table_name)
        row = (
            db.execute(
                select(table)
                .where(table.c[id_column] == user_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def create_user_without_commit(self, db: DbSession, *, table_name: str, values: Dict[str, Any]) -> None:
        """Insert a user row without committing transaction."""
        table = self._get_table(db, table_name)
        db.execute(insert(table).values(**values))

    def update_user_without_commit(
        self,
        db: DbSession,
        *,
        table_name: str,
        id_column: str,
        user_id: str,
        values: Dict[str, Any],
    ) -> None:
        """Update a user row without committing; no-op when values are empty."""
        if not values:
            return
        table = self._get_table(db, table_name)
        db.execute(update(table).where(table.c[id_column] == user_id).values(**values))

    def delete_user_without_commit(
        self,
        db: DbSession,
        *,
        table_name: str,
        id_column: str,
        user_id: str,
    ) -> None:
        """Delete a user row without committing transaction."""
        table = self._get_table(db, table_name)
        db.execute(table.delete().where(table.c[id_column] == user_id))

    def has_token_tables(self, db: DbSession) -> bool:
        """Check whether token monetization tables exist in current schema."""
        try:
            self.get_token_bundle_table(db)
            self.get_token_coupon_table(db)
            self.get_token_usage_rule_table(db)
            return True
        except Exception:
            return False

    def list_bundles(self, db: DbSession) -> list[Dict[str, Any]]:
        """List token bundles with stable sorting by available order column."""
        table = self.get_token_bundle_table(db)
        sort_column = self._pick_column(table, ["sort_order", "sortOrder", "token_amount", "tokenAmount", "name", "id"]) or "id"
        rows = db.execute(select(table).order_by(table.c[sort_column])).mappings().all()
        return [dict(row) for row in rows]

    def get_bundle_by_id(self, db: DbSession, bundle_id: str) -> Dict[str, Any] | None:
        """Fetch token bundle by identifier."""
        table = self.get_token_bundle_table(db)
        row = db.execute(select(table).where(table.c["id"] == bundle_id).limit(1)).mappings().first()
        return dict(row) if row else None

    def create_bundle_without_commit(self, db: DbSession, payload: Dict[str, Any]) -> str:
        """Create token bundle from payload and return generated bundle id."""
        table = self.get_token_bundle_table(db)
        values: Dict[str, Any] = {"id": str(uuid4())}

        values[self._pick_column(table, ["name"]) or "name"] = str(payload.get("name") or "").strip()
        values[self._pick_column(table, ["token_amount", "tokenAmount"]) or "token_amount"] = int(payload.get("token_amount") or 0)
        values[self._pick_column(table, ["price_cents", "priceCents"]) or "price_cents"] = int(payload.get("price_cents") or 0)
        values[self._pick_column(table, ["currency"]) or "currency"] = str(payload.get("currency") or "EUR").upper()[:3]
        values[self._pick_column(table, ["is_active", "isActive"]) or "is_active"] = bool(payload.get("is_active", True))
        values[self._pick_column(table, ["sort_order", "sortOrder"]) or "sort_order"] = int(payload.get("sort_order") or 0)

        now = datetime.now(UTC).replace(tzinfo=None)
        created_column = self._pick_column(table, ["created_at", "createdAt"])
        updated_column = self._pick_column(table, ["updated_at", "updatedAt"])
        if created_column:
            values[created_column] = now
        if updated_column:
            values[updated_column] = now

        db.execute(insert(table).values(**values))
        return str(values["id"])

    def update_bundle_without_commit(self, db: DbSession, bundle_id: str, payload: Dict[str, Any]) -> None:
        """Update token bundle mutable fields without committing transaction."""
        table = self.get_token_bundle_table(db)
        values: Dict[str, Any] = {}

        if "name" in payload:
            values[self._pick_column(table, ["name"]) or "name"] = str(payload.get("name") or "").strip()
        if "token_amount" in payload:
            values[self._pick_column(table, ["token_amount", "tokenAmount"]) or "token_amount"] = int(payload.get("token_amount") or 0)
        if "price_cents" in payload:
            values[self._pick_column(table, ["price_cents", "priceCents"]) or "price_cents"] = int(payload.get("price_cents") or 0)
        if "currency" in payload:
            values[self._pick_column(table, ["currency"]) or "currency"] = str(payload.get("currency") or "EUR").upper()[:3]
        if "is_active" in payload:
            values[self._pick_column(table, ["is_active", "isActive"]) or "is_active"] = bool(payload.get("is_active", True))
        if "sort_order" in payload:
            values[self._pick_column(table, ["sort_order", "sortOrder"]) or "sort_order"] = int(payload.get("sort_order") or 0)

        updated_column = self._pick_column(table, ["updated_at", "updatedAt"])
        if updated_column:
            values[updated_column] = datetime.now(UTC).replace(tzinfo=None)

        db.execute(update(table).where(table.c["id"] == bundle_id).values(**values))

    def list_coupons(self, db: DbSession) -> list[Dict[str, Any]]:
        """List token coupons newest-first using best available timestamp column."""
        table = self.get_token_coupon_table(db)
        created_column = self._pick_column(table, ["created_at", "createdAt", "id"]) or "id"
        rows = db.execute(select(table).order_by(table.c[created_column].desc())).mappings().all()
        return [dict(row) for row in rows]

    def create_coupons_without_commit(self, db: DbSession, payload: Dict[str, Any], amount: int, creator_user_id: Optional[str]) -> list[str]:
        """Create one or more coupon rows and return created coupon ids."""
        table = self.get_token_coupon_table(db)
        created_ids: list[str] = []

        for _ in range(max(1, amount)):
            coupon_id = str(uuid4())
            coupon_code = str(uuid4()).upper()
            values: Dict[str, Any] = {
                "id": coupon_id,
                self._pick_column(table, ["code"]) or "code": coupon_code,
                self._pick_column(table, ["comment"]) or "comment": (str(payload.get("comment") or "").strip() or None),
                self._pick_column(table, ["infinite_tokens", "infiniteTokens"]) or "infinite_tokens": bool(payload.get("infinite_tokens", False)),
                self._pick_column(table, ["max_redemptions", "maxRedemptions"]) or "max_redemptions": payload.get("max_redemptions"),
            }

            token_amount_column = self._pick_column(table, ["token_amount", "tokenAmount"])
            if token_amount_column:
                values[token_amount_column] = None if bool(payload.get("infinite_tokens", False)) else str(payload.get("token_amount") or "0")

            valid_from_column = self._pick_column(table, ["valid_from", "validFrom"])
            valid_until_column = self._pick_column(table, ["valid_until", "validUntil"])
            if valid_from_column:
                values[valid_from_column] = payload.get("valid_from")
            if valid_until_column:
                values[valid_until_column] = payload.get("valid_until")

            created_by_column = self._pick_column(table, ["created_by_id", "createdBy_id", "createdById"])
            if created_by_column:
                values[created_by_column] = creator_user_id

            created_column = self._pick_column(table, ["created_at", "createdAt"])
            if created_column:
                values[created_column] = datetime.now(UTC).replace(tzinfo=None)

            db.execute(insert(table).values(**values))
            created_ids.append(coupon_id)

        return created_ids

    def list_rules(self, db: DbSession) -> list[Dict[str, Any]]:
        """List token usage rules with stable object-key ordering."""
        table = self.get_token_usage_rule_table(db)
        object_key_column = self._pick_column(table, ["object_key", "objectKey", "id"]) or "id"
        rows = db.execute(select(table).order_by(table.c[object_key_column])).mappings().all()
        return [dict(row) for row in rows]

    def get_rule_by_id(self, db: DbSession, rule_id: str) -> Dict[str, Any] | None:
        """Fetch token usage rule by identifier."""
        table = self.get_token_usage_rule_table(db)
        row = db.execute(select(table).where(table.c["id"] == rule_id).limit(1)).mappings().first()
        return dict(row) if row else None

    def create_rule_without_commit(self, db: DbSession, payload: Dict[str, Any]) -> str:
        """Create token usage rule and return generated rule id."""
        table = self.get_token_usage_rule_table(db)
        values: Dict[str, Any] = {
            "id": str(uuid4()),
            self._pick_column(table, ["object_key", "objectKey"]) or "object_key": str(payload.get("object_key") or "").strip(),
            self._pick_column(table, ["label_key", "labelKey"]) or "label_key": str(payload.get("label_key") or "tokens.rule.custom").strip(),
            self._pick_column(table, ["game_type", "gameType"]) or "game_type": (str(payload.get("game_type") or "").strip() or None),
            self._pick_column(table, ["unit_size", "unitSize"]) or "unit_size": max(1, int(payload.get("unit_size") or 1)),
            self._pick_column(table, ["tokens_per_unit", "tokensPerUnit"]) or "tokens_per_unit": str(max(0, int(round(float(payload.get("tokens_per_unit") or 1))))),
            self._pick_column(table, ["is_active", "isActive"]) or "is_active": bool(payload.get("is_active", True)),
        }
        db.execute(insert(table).values(**values))
        return str(values["id"])

    def update_rule_without_commit(self, db: DbSession, rule_id: str, payload: Dict[str, Any]) -> None:
        """Update token usage rule fields without committing transaction."""
        table = self.get_token_usage_rule_table(db)
        values: Dict[str, Any] = {}

        if "label_key" in payload:
            values[self._pick_column(table, ["label_key", "labelKey"]) or "label_key"] = str(payload.get("label_key") or "tokens.rule.custom").strip()
        if "game_type" in payload:
            values[self._pick_column(table, ["game_type", "gameType"]) or "game_type"] = str(payload.get("game_type") or "").strip() or None
        if "unit_size" in payload:
            values[self._pick_column(table, ["unit_size", "unitSize"]) or "unit_size"] = max(1, int(payload.get("unit_size") or 1))
        if "tokens_per_unit" in payload:
            values[self._pick_column(table, ["tokens_per_unit", "tokensPerUnit"]) or "tokens_per_unit"] = str(max(0, int(round(float(payload.get("tokens_per_unit") or 1)))))
        if "is_active" in payload:
            values[self._pick_column(table, ["is_active", "isActive"]) or "is_active"] = bool(payload.get("is_active", True))

        db.execute(update(table).where(table.c["id"] == rule_id).values(**values))
