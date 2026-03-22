from datetime import UTC, datetime
from typing import Any, Dict, Optional

from sqlalchemy import select, update, insert

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class BirdsOfPreyRepository(GameLogicStateRepository):
    @staticmethod
    def _first_present(row: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
        for key in keys:
            if key in row:
                return row.get(key)
        return default

    def get_configuration(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        game = self.get_game_by_id(db, game_id)
        if game is None:
            return {}

        return {
            "visibility_radius_meters": int(self._first_present(game, ["birds_of_prey_visibility_radius_meters", "birdsOfPreyVisibilityRadiusMeters"], 100) or 100),
            "protection_radius_meters": int(self._first_present(game, ["birds_of_prey_protection_radius_meters", "birdsOfPreyProtectionRadiusMeters"], 50) or 50),
            "auto_drop_seconds": int(self._first_present(game, ["birds_of_prey_auto_drop_seconds", "birdsOfPreyAutoDropSeconds"], 300) or 300),
        }

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if numeric == numeric else None

    def _extract_team_location(self, row: Dict[str, Any]) -> Dict[str, Any]:
        latitude = self._safe_float(self._first_present(row, ["geo_latitude", "geoLatitude", "latitude"]))
        longitude = self._safe_float(self._first_present(row, ["geo_longitude", "geoLongitude", "longitude"]))
        updated_at_raw = self._first_present(row, ["geo_updated_at", "geoUpdatedAt", "updated_at", "updatedAt"])

        updated_at = ""
        if isinstance(updated_at_raw, datetime):
            updated_at = updated_at_raw.isoformat()
        else:
            updated_at = str(updated_at_raw or "")

        return {
            "lat": latitude,
            "lon": longitude,
            "updated_at": updated_at,
        }

    def get_team_location(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        team = self.get_team_by_game_and_id(db, game_id, team_id)
        if not isinstance(team, dict):
            return {
                "lat": None,
                "lon": None,
                "updated_at": "",
            }
        return self._extract_team_location(team)

    def fetch_team_locations_by_game_id(self, db: DbSession, game_id: str) -> Dict[str, Dict[str, Any]]:
        teams = self.fetch_teams_by_game_id(db, game_id)
        by_team: Dict[str, Dict[str, Any]] = {}
        for team in teams:
            team_id = str(team.get("id") or "").strip()
            if not team_id:
                continue
            by_team[team_id] = self._extract_team_location(team)
        return by_team

    def update_team_location_without_commit(self, db: DbSession, game_id: str, team_id: str, *, latitude: float, longitude: float) -> None:
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

    def fetch_active_team_ids_for_game(self, db: DbSession, game_id: str) -> list[str]:
        table = self.get_team_table(db)
        rows = db.execute(select(table.c["id"]).where(table.c["game_id"] == game_id)).all()
        return [str(row[0]) for row in rows if str(row[0] or "").strip()]

    def get_egg_table(self, db: DbSession):
        return self._get_table(db, "birds_of_prey_egg")

    @staticmethod
    def _to_iso(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value or "")

    def _normalize_egg_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(self._first_present(row, ["id"], "") or ""),
            "game_id": str(self._first_present(row, ["game_id", "gameId"], "") or ""),
            "owner_team_id": str(self._first_present(row, ["owner_team_id", "ownerTeamId"], "") or ""),
            "lat": self._safe_float(self._first_present(row, ["latitude"])),
            "lon": self._safe_float(self._first_present(row, ["longitude"])),
            "dropped_at": self._to_iso(self._first_present(row, ["dropped_at", "droppedAt"])),
            "automatic": bool(self._first_present(row, ["dropped_automatically", "droppedAutomatically"], False)),
            "destroyed_at": self._to_iso(self._first_present(row, ["destroyed_at", "destroyedAt"])),
            "destroyed_by_team_id": str(self._first_present(row, ["destroyed_by_team_id", "destroyedByTeamId"], "") or ""),
        }

    def fetch_active_eggs_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_egg_table(db)
        game_col = self._pick_column(table, ["game_id", "gameId"])
        destroyed_col = self._pick_column(table, ["destroyed_at", "destroyedAt"])
        if not game_col or not destroyed_col:
            return []

        rows = (
            db.execute(
                select(table)
                .where(table.c[game_col] == game_id)
                .where(table.c[destroyed_col].is_(None))
            )
            .mappings()
            .all()
        )
        return [self._normalize_egg_row(dict(row)) for row in rows]

    def get_active_egg_by_id(self, db: DbSession, game_id: str, egg_id: str) -> Optional[Dict[str, Any]]:
        table = self.get_egg_table(db)
        game_col = self._pick_column(table, ["game_id", "gameId"])
        destroyed_col = self._pick_column(table, ["destroyed_at", "destroyedAt"])
        id_col = self._pick_column(table, ["id"])
        if not game_col or not destroyed_col or not id_col:
            return None

        row = (
            db.execute(
                select(table)
                .where(table.c[game_col] == game_id)
                .where(table.c[id_col] == egg_id)
                .where(table.c[destroyed_col].is_(None))
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return self._normalize_egg_row(dict(row))

    def insert_egg_without_commit(
        self,
        db: DbSession,
        *,
        egg_id: str,
        game_id: str,
        owner_team_id: str,
        latitude: float,
        longitude: float,
        automatic: bool,
    ) -> None:
        table = self.get_egg_table(db)

        values: Dict[str, Any] = {}
        id_col = self._pick_column(table, ["id"])
        game_col = self._pick_column(table, ["game_id", "gameId"])
        owner_col = self._pick_column(table, ["owner_team_id", "ownerTeamId"])
        lat_col = self._pick_column(table, ["latitude"])
        lon_col = self._pick_column(table, ["longitude"])
        dropped_at_col = self._pick_column(table, ["dropped_at", "droppedAt"])
        auto_col = self._pick_column(table, ["dropped_automatically", "droppedAutomatically"])

        if id_col:
            values[id_col] = egg_id
        if game_col:
            values[game_col] = game_id
        if owner_col:
            values[owner_col] = owner_team_id
        if lat_col:
            values[lat_col] = float(latitude)
        if lon_col:
            values[lon_col] = float(longitude)
        if dropped_at_col:
            values[dropped_at_col] = datetime.now(UTC).replace(tzinfo=None)
        if auto_col:
            values[auto_col] = bool(automatic)

        db.execute(insert(table).values(**values))

    def mark_egg_destroyed_without_commit(self, db: DbSession, *, egg_id: str, destroyed_by_team_id: str) -> bool:
        table = self.get_egg_table(db)
        id_col = self._pick_column(table, ["id"])
        destroyed_at_col = self._pick_column(table, ["destroyed_at", "destroyedAt"])
        destroyed_by_col = self._pick_column(table, ["destroyed_by_team_id", "destroyedByTeamId"])
        if not id_col or not destroyed_at_col:
            return False

        updates: Dict[str, Any] = {
            destroyed_at_col: datetime.now(UTC).replace(tzinfo=None),
        }
        if destroyed_by_col:
            updates[destroyed_by_col] = destroyed_by_team_id

        result = db.execute(
            update(table)
            .where(table.c[id_col] == egg_id)
            .where(table.c[destroyed_at_col].is_(None))
            .values(**updates)
        )
        return int(result.rowcount or 0) > 0

    def get_last_drop_at_for_team(self, db: DbSession, *, game_id: str, team_id: str) -> Optional[datetime]:
        table = self.get_egg_table(db)
        game_col = self._pick_column(table, ["game_id", "gameId"])
        owner_col = self._pick_column(table, ["owner_team_id", "ownerTeamId"])
        dropped_at_col = self._pick_column(table, ["dropped_at", "droppedAt"])
        if not game_col or not owner_col or not dropped_at_col:
            return None

        rows = (
            db.execute(
                select(table.c[dropped_at_col])
                .where(table.c[game_col] == game_id)
                .where(table.c[owner_col] == team_id)
                .order_by(table.c[dropped_at_col].desc())
                .limit(1)
            )
            .all()
        )
        if not rows:
            return None
        raw = rows[0][0]
        if isinstance(raw, datetime):
            return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
        return None

    def update_configuration_without_commit(self, db: DbSession, game_id: str, values: Dict[str, Any]) -> None:
        table = self.get_game_table(db)
        updates: Dict[str, Any] = {}

        column_map = {
            "visibility_radius_meters": ["birds_of_prey_visibility_radius_meters", "birdsOfPreyVisibilityRadiusMeters"],
            "protection_radius_meters": ["birds_of_prey_protection_radius_meters", "birdsOfPreyProtectionRadiusMeters"],
            "auto_drop_seconds": ["birds_of_prey_auto_drop_seconds", "birdsOfPreyAutoDropSeconds"],
        }

        for payload_key, candidates in column_map.items():
            if payload_key not in values:
                continue
            for column_name in candidates:
                if column_name in table.c:
                    updates[column_name] = values[payload_key]
                    break

        if updates:
            db.execute(
                update(table)
                .where(table.c["id"] == game_id)
                .values(**updates)
            )
