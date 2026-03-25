from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import Table, delete, insert, select, update
from sqlalchemy.sql.sqltypes import Integer, String

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class MarketCrashRepository(GameLogicStateRepository):
    """Repository for Market Crash config, map entities, inventory, and trades."""

    @staticmethod
    def _first_present(row: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
        """Return first present value among fallback column names."""
        for key in keys:
            if key in row:
                return row.get(key)
        return default

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Convert raw value to float, returning `None` for invalid input."""
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if numeric == numeric else None

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Convert raw value to int with fallback default."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_iso(value: Any) -> str:
        """Normalize datetime-like values into string representation."""
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value or "")

    @staticmethod
    def _value_to_int(value: Any, default: int = 0) -> int:
        """Convert numeric-like values (including Decimal) to int."""
        if isinstance(value, Decimal):
            return int(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _maybe_generate_id(self, table: Table) -> str | None:
        """Generate UUID ids for string-id tables when needed."""
        id_col = self._pick_column(table, ["id"])
        if not id_col:
            return None
        column = table.c[id_col]
        if isinstance(column.type, String):
            return str(uuid4())
        if isinstance(column.type, Integer):
            return None
        return None

    def get_point_table(self, db: DbSession) -> Table:
        """Return reflected Market Crash point table."""
        return self._get_table(db, "market_crash_point")

    def get_resource_table(self, db: DbSession) -> Table:
        """Return reflected Market Crash resource table."""
        return self._get_table(db, "market_crash_resource")

    def get_point_resource_table(self, db: DbSession) -> Table:
        """Return reflected point-resource assignment table."""
        return self._get_table(db, "market_crash_point_resource")

    def get_inventory_table(self, db: DbSession) -> Table:
        """Return reflected team inventory table."""
        return self._get_table(db, "market_crash_inventory")

    def get_trade_table(self, db: DbSession) -> Table:
        """Return reflected trade ledger table."""
        return self._get_table(db, "market_crash_trade")

    def _normalize_team_location(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize team row location fields into API shape."""
        latitude = self._safe_float(self._first_present(row, ["geo_latitude", "geoLatitude", "latitude"]))
        longitude = self._safe_float(self._first_present(row, ["geo_longitude", "geoLongitude", "longitude"]))
        updated_at_raw = self._first_present(row, ["geo_updated_at", "geoUpdatedAt", "updated_at", "updatedAt"])

        return {
            "lat": latitude,
            "lon": longitude,
            "updated_at": self._to_iso(updated_at_raw),
        }

    def fetch_team_locations_by_game_id(self, db: DbSession, game_id: str) -> Dict[str, Dict[str, Any]]:
        """Return all team locations for a game keyed by team id."""
        teams = self.fetch_teams_by_game_id(db, game_id)
        by_team: Dict[str, Dict[str, Any]] = {}
        for team in teams:
            team_id = str(team.get("id") or "").strip()
            if not team_id:
                continue
            by_team[team_id] = self._normalize_team_location(team)
        return by_team

    def get_team_location(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Return normalized location for one team in a game."""
        team = self.get_team_by_game_and_id(db, game_id, team_id)
        if not isinstance(team, dict):
            return {"lat": None, "lon": None, "updated_at": ""}
        return self._normalize_team_location(team)

    def update_team_location_without_commit(self, db: DbSession, game_id: str, team_id: str, *, latitude: float, longitude: float) -> None:
        """Persist team location coordinates without committing transaction."""
        table = self.get_team_table(db)
        lat_col = self._pick_column(table, ["geo_latitude", "geoLatitude", "latitude"])
        lon_col = self._pick_column(table, ["geo_longitude", "geoLongitude", "longitude"])
        updated_col = self._pick_column(table, ["geo_updated_at", "geoUpdatedAt", "updated_at", "updatedAt"])

        updates: Dict[str, Any] = {}
        if lat_col:
            updates[lat_col] = float(latitude)
        if lon_col:
            updates[lon_col] = float(longitude)
        if updated_col:
            updates[updated_col] = datetime.now(UTC).replace(tzinfo=None)
        if not updates:
            return

        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == team_id)
            .values(**updates)
        )

    def get_starting_cash(self, db: DbSession, game_id: str, default: int = 1000) -> int:
        """Return configured starting cash for a game."""
        game = self.get_game_by_id(db, game_id)
        if not isinstance(game, dict):
            return default
        return self._safe_int(
            self._first_present(game, ["market_crash_starting_cash", "marketCrashStartingCash"], default),
            default,
        )

    def fetch_resources_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """Fetch all market resources configured for a game."""
        table = self.get_resource_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def get_resource_by_game_id_and_resource_id(self, db: DbSession, game_id: str, resource_id: str) -> Dict[str, Any] | None:
        """Fetch one resource by game and resource id."""
        table = self.get_resource_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == resource_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def get_resource_by_game_id_and_name(self, db: DbSession, game_id: str, name: str) -> Dict[str, Any] | None:
        """Fetch one resource by game id and unique resource name."""
        table = self.get_resource_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["name"] == name)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def create_resource_without_commit(self, db: DbSession, values: Dict[str, Any]) -> str:
        """Insert a resource record and return generated id."""
        table = self.get_resource_table(db)
        insert_values = dict(values)
        generated_id = self._maybe_generate_id(table)
        if generated_id is not None and "id" in table.c and "id" not in insert_values:
            insert_values["id"] = generated_id
        result = db.execute(insert(table).values(**insert_values).returning(table.c["id"]))
        return str(result.scalar_one())

    def update_resource_without_commit(self, db: DbSession, game_id: str, resource_id: str, values: Dict[str, Any]) -> None:
        """Update one resource record without committing."""
        table = self.get_resource_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == resource_id)
            .values(**values)
        )

    def delete_resource_without_commit(self, db: DbSession, game_id: str, resource_id: str) -> None:
        """Delete one resource record without committing."""
        table = self.get_resource_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == resource_id)
        )

    def fetch_points_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """Fetch all trading points configured for a game."""
        table = self.get_point_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def get_point_by_game_id_and_point_id(self, db: DbSession, game_id: str, point_id: str) -> Dict[str, Any] | None:
        """Fetch one trading point by game and point id."""
        table = self.get_point_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == point_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def create_point_without_commit(self, db: DbSession, values: Dict[str, Any]) -> str:
        """Insert one trading point record and return id."""
        table = self.get_point_table(db)
        insert_values = dict(values)
        generated_id = self._maybe_generate_id(table)
        if generated_id is not None and "id" in table.c and "id" not in insert_values:
            insert_values["id"] = generated_id
        result = db.execute(insert(table).values(**insert_values).returning(table.c["id"]))
        return str(result.scalar_one())

    def update_point_without_commit(self, db: DbSession, game_id: str, point_id: str, values: Dict[str, Any]) -> None:
        """Update one point record without committing."""
        table = self.get_point_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == point_id)
            .values(**values)
        )

    def delete_point_without_commit(self, db: DbSession, game_id: str, point_id: str) -> None:
        """Delete one point record without committing."""
        table = self.get_point_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == point_id)
        )

    def fetch_point_resources_by_point_id(self, db: DbSession, point_id: str) -> list[Dict[str, Any]]:
        """Fetch all resource-price rows for a point."""
        table = self.get_point_resource_table(db)
        rows = db.execute(select(table).where(table.c["point_id"] == point_id)).mappings().all()
        return [dict(row) for row in rows]

    def fetch_point_resources_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """Fetch all point-resource rows scoped to a game."""
        point_table = self.get_point_table(db)
        point_resource_table = self.get_point_resource_table(db)

        rows = (
            db.execute(
                select(point_resource_table)
                .join(point_table, point_resource_table.c["point_id"] == point_table.c["id"])
                .where(point_table.c["game_id"] == game_id)
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def fetch_all_point_resources_with_context(self, db: DbSession) -> list[Dict[str, Any]]:
        """Fetch all point resources joined with point/resource context."""
        point_table = self.get_point_table(db)
        point_resource_table = self.get_point_resource_table(db)
        resource_table = self.get_resource_table(db)

        rows = (
            db.execute(
                select(
                    point_resource_table,
                    point_table.c["game_id"].label("point_game_id"),
                    point_table.c["id"].label("point_table_id"),
                    point_table.c["title"].label("point_title"),
                    point_table.c["latitude"].label("point_latitude"),
                    point_table.c["longitude"].label("point_longitude"),
                    point_table.c["radius_meters"].label("point_radius_meters"),
                    resource_table.c["name"].label("resource_name"),
                )
                .join(point_table, point_resource_table.c["point_id"] == point_table.c["id"])
                .join(resource_table, point_resource_table.c["resource_id"] == resource_table.c["id"])
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def fetch_market_crash_game_ids(self, db: DbSession) -> list[str]:
        """List identifiers for all games of type `market_crash`."""
        game_table = self.get_game_table(db)
        rows = (
            db.execute(
                select(game_table.c["id"]).where(game_table.c["game_type"] == "market_crash")
            )
            .all()
        )
        return [str(row[0]) for row in rows if str(row[0] or "").strip()]

    def get_point_resource(self, db: DbSession, point_id: str, resource_id: str) -> Dict[str, Any] | None:
        """Fetch one point-resource price row by compound key."""
        table = self.get_point_resource_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["point_id"] == point_id)
                .where(table.c["resource_id"] == resource_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def update_point_resource_without_commit(self, db: DbSession, point_resource_id: str, values: Dict[str, Any]) -> None:
        """Update one point-resource row without committing."""
        table = self.get_point_resource_table(db)
        db.execute(
            update(table)
            .where(table.c["id"] == point_resource_id)
            .values(**values)
        )

    def replace_point_resources_without_commit(self, db: DbSession, point_id: str, rows: list[Dict[str, Any]]) -> None:
        """Replace all resource rows for a point atomically within transaction."""
        table = self.get_point_resource_table(db)
        db.execute(delete(table).where(table.c["point_id"] == point_id))
        if rows:
            values = [{**row, "point_id": point_id} for row in rows]
            if "id" in table.c:
                for row in values:
                    if "id" not in row or not row["id"]:
                        maybe_id = self._maybe_generate_id(table)
                        if maybe_id:
                            row["id"] = maybe_id
            db.execute(insert(table), values)

    def fetch_inventory_rows_by_team_id(self, db: DbSession, team_id: str) -> list[Dict[str, Any]]:
        """Fetch inventory rows for one team."""
        table = self.get_inventory_table(db)
        rows = db.execute(select(table).where(table.c["team_id"] == team_id)).mappings().all()
        return [dict(row) for row in rows]

    def get_inventory_row(self, db: DbSession, team_id: str, resource_id: str) -> Dict[str, Any] | None:
        """Fetch one inventory row by team/resource."""
        table = self.get_inventory_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["team_id"] == team_id)
                .where(table.c["resource_id"] == resource_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def create_inventory_row_without_commit(self, db: DbSession, *, team_id: str, resource_id: str, quantity: int) -> None:
        """Insert a new inventory row without committing."""
        table = self.get_inventory_table(db)
        values: Dict[str, Any] = {
            "team_id": team_id,
            "resource_id": resource_id,
            "quantity": int(quantity),
        }
        if "id" in table.c:
            maybe_id = self._maybe_generate_id(table)
            if maybe_id:
                values["id"] = maybe_id
        db.execute(insert(table).values(**values))

    def update_inventory_quantity_without_commit(self, db: DbSession, inventory_id: str, quantity: int) -> None:
        """Update inventory quantity by inventory row id."""
        table = self.get_inventory_table(db)
        db.execute(
            update(table)
            .where(table.c["id"] == inventory_id)
            .values(quantity=int(quantity))
        )

    def upsert_inventory_quantity_without_commit(self, db: DbSession, *, team_id: str, resource_id: str, quantity: int) -> None:
        """Upsert inventory quantity for team/resource key."""
        existing = self.get_inventory_row(db, team_id, resource_id)
        if existing is None:
            self.create_inventory_row_without_commit(db, team_id=team_id, resource_id=resource_id, quantity=quantity)
            return

        inventory_id = str(existing.get("id") or "")
        if not inventory_id:
            return
        self.update_inventory_quantity_without_commit(db, inventory_id, quantity)

    def fetch_trades_by_team_id(self, db: DbSession, team_id: str) -> list[Dict[str, Any]]:
        """Fetch historical trade rows for one team."""
        table = self.get_trade_table(db)
        rows = db.execute(select(table).where(table.c["team_id"] == team_id)).mappings().all()
        return [dict(row) for row in rows]

    def create_trade_without_commit(
        self,
        db: DbSession,
        *,
        point_id: str,
        team_id: str,
        resource_id: str,
        side: str,
        quantity: int,
        unit_price: int,
        total_amount: int,
    ) -> str:
        """Insert a trade ledger row and return created trade id."""
        table = self.get_trade_table(db)

        side_col = self._pick_column(table, ["trade_type", "tradeType", "side", "type"])
        unit_col = self._pick_column(table, ["unit_price", "unitPrice"])
        quantity_col = self._pick_column(table, ["quantity"])
        total_col = self._pick_column(table, ["total_amount", "totalAmount", "total"])
        created_col = self._pick_column(table, ["created_at", "createdAt", "traded_at", "tradedAt"])

        values: Dict[str, Any] = {
            "point_id": point_id,
            "team_id": team_id,
            "resource_id": resource_id,
        }
        if side_col:
            values[side_col] = str(side)
        if quantity_col:
            values[quantity_col] = int(quantity)
        if unit_col:
            values[unit_col] = int(unit_price)
        if total_col:
            values[total_col] = int(total_amount)
        if created_col:
            values[created_col] = datetime.now(UTC).replace(tzinfo=None)

        maybe_id = self._maybe_generate_id(table)
        if maybe_id is not None and "id" in table.c:
            values["id"] = maybe_id

        result = db.execute(insert(table).values(**values).returning(table.c["id"]))
        return str(result.scalar_one())

    def calculate_team_cash(self, db: DbSession, team_id: str, starting_cash: int) -> int:
        """Derive current team cash from starting cash and trade ledger."""
        side_candidates = ["trade_type", "tradeType", "side", "type"]
        total_candidates = ["total_amount", "totalAmount", "total"]

        trades = self.fetch_trades_by_team_id(db, team_id)
        cash = int(starting_cash)
        for trade in trades:
            side = str(self._first_present(trade, side_candidates, "")).strip().lower()
            total = self._value_to_int(self._first_present(trade, total_candidates, 0), 0)
            if side == "buy":
                cash -= total
            elif side == "sell":
                cash += total
        return cash

    def build_inventory_map(self, db: DbSession, team_id: str, resource_name_by_id: Dict[str, str]) -> Dict[str, int]:
        """Build resource-name keyed inventory quantities for a team."""
        rows = self.fetch_inventory_rows_by_team_id(db, team_id)
        inventory: Dict[str, int] = {}
        for row in rows:
            resource_id = str(row.get("resource_id") or "")
            resource_name = resource_name_by_id.get(resource_id, resource_id)
            if not resource_name:
                continue
            inventory[resource_name] = int(row.get("quantity") or 0)
        return inventory

    def set_team_geo_score_without_commit(self, db: DbSession, team_id: str, score: int) -> None:
        """Persist absolute team geo score value without commit."""
        table = self.get_team_table(db)
        score_col = self._pick_column(table, ["geo_score", "geoScore", "score"])
        if not score_col:
            return
        db.execute(
            update(table)
            .where(table.c["id"] == team_id)
            .values(**{score_col: int(score)})
        )
