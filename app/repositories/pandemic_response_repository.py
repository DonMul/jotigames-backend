from typing import Any, Dict, Optional

from sqlalchemy import Table, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class PandemicResponseRepository(GameLogicStateRepository):
    _CENTER_LAT_COLUMNS = ["pandemic_response_center_lat", "pandemicResponseCenterLat"]
    _CENTER_LON_COLUMNS = ["pandemic_response_center_lon", "pandemicResponseCenterLon"]
    _SPAWN_AREA_COLUMNS = [
        "pandemic_response_spawn_area_geojson",
        "pandemic_response_spawn_area_geo_json",
        "pandemicResponseSpawnAreaGeoJson",
        "pandemicResponseSpawnAreaGeoJSON",
    ]
    _SEVERITY_UPGRADE_COLUMNS = ["pandemic_response_severity_upgrade_seconds", "pandemicResponseSeverityUpgradeSeconds"]
    _PENALTY_PERCENT_COLUMNS = ["pandemic_response_penalty_percent", "pandemicResponsePenaltyPercent"]
    _TARGET_ACTIVE_HOTSPOTS_COLUMNS = ["pandemic_response_target_active_hotspots", "pandemicResponseTargetActiveHotspots"]
    _PICKUP_POINT_COUNT_COLUMNS = ["pandemic_response_pickup_point_count", "pandemicResponsePickupPointCount"]

    @staticmethod
    def _first_present(row: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
        """Return first existing key value from row with optional default."""
        for key in keys:
            if key in row:
                return row.get(key)
        return default

    def get_hotspot_table(self, db: DbSession) -> Table:
        """Return reflected `pandemic_response_hotspot` table."""
        return self._get_table(db, "pandemic_response_hotspot")

    def get_pickup_table(self, db: DbSession) -> Table:
        """Return reflected `pandemic_response_pickup_point` table."""
        return self._get_table(db, "pandemic_response_pickup_point")

    def get_configuration(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        """Build normalized configuration payload from game row columns."""
        game = self.get_game_by_id(db, game_id)
        if game is None:
            return {}

        return {
            "center_lat": self._first_present(game, self._CENTER_LAT_COLUMNS, 51.05),
            "center_lon": self._first_present(game, self._CENTER_LON_COLUMNS, 3.72),
            "spawn_area_geojson": self._first_present(game, self._SPAWN_AREA_COLUMNS, ""),
            "severity_upgrade_seconds": int(self._first_present(game, self._SEVERITY_UPGRADE_COLUMNS, 180) or 180),
            "penalty_percent": int(self._first_present(game, self._PENALTY_PERCENT_COLUMNS, 10) or 10),
            "target_active_hotspots": int(self._first_present(game, self._TARGET_ACTIVE_HOTSPOTS_COLUMNS, 15) or 15),
            "pickup_point_count": int(self._first_present(game, self._PICKUP_POINT_COUNT_COLUMNS, 4) or 4),
        }

    def update_configuration_without_commit(self, db: DbSession, game_id: str, values: Dict[str, Any]) -> None:
        """Update pandemic config fields across snake/camel schema variants."""
        table = self.get_game_table(db)
        updates: Dict[str, Any] = {}

        column_map = {
            "center_lat": self._CENTER_LAT_COLUMNS,
            "center_lon": self._CENTER_LON_COLUMNS,
            "spawn_area_geojson": self._SPAWN_AREA_COLUMNS,
            "severity_upgrade_seconds": self._SEVERITY_UPGRADE_COLUMNS,
            "penalty_percent": self._PENALTY_PERCENT_COLUMNS,
            "target_active_hotspots": self._TARGET_ACTIVE_HOTSPOTS_COLUMNS,
            "pickup_point_count": self._PICKUP_POINT_COUNT_COLUMNS,
        }

        for payload_key, candidates in column_map.items():
            if payload_key not in values:
                continue
            for column_name in candidates:
                if column_name in table.c:
                    updates[column_name] = values[payload_key]
                    break

        if updates:
            game_id_column = self._get_game_id_column(table)
            db.execute(
                update(table)
                .where(table.c[game_id_column] == game_id)
                .values(**updates)
            )

    def fetch_hotspots_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """List hotspots for a game."""
        table = self.get_hotspot_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def get_hotspot_by_game_id_and_hotspot_id(self, db: DbSession, game_id: str, hotspot_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one hotspot by scoped game/hotspot identifiers."""
        table = self.get_hotspot_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == hotspot_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def fetch_pickups_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """List pickup points for a game."""
        table = self.get_pickup_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]
