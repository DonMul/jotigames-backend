from typing import Any, Dict

from sqlalchemy import Table, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class PandemicResponseRepository(GameLogicStateRepository):
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
            "center_lat": self._first_present(game, ["pandemic_response_center_lat", "pandemicResponseCenterLat"], 51.05),
            "center_lon": self._first_present(game, ["pandemic_response_center_lon", "pandemicResponseCenterLon"], 3.72),
            "spawn_area_geojson": self._first_present(game, ["pandemic_response_spawn_area_geojson", "pandemicResponseSpawnAreaGeoJson"], ""),
            "severity_upgrade_seconds": int(self._first_present(game, ["pandemic_response_severity_upgrade_seconds", "pandemicResponseSeverityUpgradeSeconds"], 180) or 180),
            "penalty_percent": int(self._first_present(game, ["pandemic_response_penalty_percent", "pandemicResponsePenaltyPercent"], 10) or 10),
            "target_active_hotspots": int(self._first_present(game, ["pandemic_response_target_active_hotspots", "pandemicResponseTargetActiveHotspots"], 15) or 15),
            "pickup_point_count": int(self._first_present(game, ["pandemic_response_pickup_point_count", "pandemicResponsePickupPointCount"], 4) or 4),
        }

    def update_configuration_without_commit(self, db: DbSession, game_id: str, values: Dict[str, Any]) -> None:
        """Update pandemic config fields across snake/camel schema variants."""
        table = self.get_game_table(db)
        updates: Dict[str, Any] = {}

        column_map = {
            "center_lat": ["pandemic_response_center_lat", "pandemicResponseCenterLat"],
            "center_lon": ["pandemic_response_center_lon", "pandemicResponseCenterLon"],
            "spawn_area_geojson": ["pandemic_response_spawn_area_geojson", "pandemicResponseSpawnAreaGeoJson"],
            "severity_upgrade_seconds": ["pandemic_response_severity_upgrade_seconds", "pandemicResponseSeverityUpgradeSeconds"],
            "penalty_percent": ["pandemic_response_penalty_percent", "pandemicResponsePenaltyPercent"],
            "target_active_hotspots": ["pandemic_response_target_active_hotspots", "pandemicResponseTargetActiveHotspots"],
            "pickup_point_count": ["pandemic_response_pickup_point_count", "pandemicResponsePickupPointCount"],
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

    def fetch_hotspots_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """List hotspots for a game."""
        table = self.get_hotspot_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def fetch_pickups_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """List pickup points for a game."""
        table = self.get_pickup_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]
