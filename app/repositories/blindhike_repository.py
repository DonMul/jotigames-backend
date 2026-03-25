from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Table, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class BlindHikeRepository(GameLogicStateRepository):
    """Persistence helpers for Blind Hike configuration and marker state."""

    @staticmethod
    def _first_present(row: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
        """Return first present key from row for schema-compat reads."""
        for key in keys:
            if key in row:
                return row.get(key)
        return default

    def get_configuration(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        """Build normalized Blind Hike configuration payload for a game."""
        game = self.get_game_by_id(db, game_id)
        if game is None:
            return {}

        return {
            "target_lat": self._first_present(game, ["blind_hike_target_lat", "blindHikeTargetLat"]),
            "target_lon": self._first_present(game, ["blind_hike_target_lon", "blindHikeTargetLon"]),
            "horizontal_flip": bool(self._first_present(game, ["blind_hike_horizontal_flip", "blindHikeHorizontalFlip"], False)),
            "vertical_flip": bool(self._first_present(game, ["blind_hike_vertical_flip", "blindHikeVerticalFlip"], False)),
            "scale_factor": str(self._first_present(game, ["blind_hike_scale_factor", "blindHikeScaleFactor"], "1.0")),
            "rotation": int(self._first_present(game, ["blind_hike_rotation", "blindHikeRotation"], 0) or 0),
            "max_markers": self._first_present(game, ["blind_hike_max_markers", "blindHikeMaxMarkers"]),
            "marker_cooldown": int(self._first_present(game, ["blind_hike_marker_cooldown", "blindHikeMarkerCooldown"], 0) or 0),
            "finish_radius_meters": int(self._first_present(game, ["blind_hike_finish_radius_meters", "blindHikeFinishRadiusMeters"], 25) or 25),
        }

    def update_configuration_without_commit(self, db: DbSession, game_id: str, values: Dict[str, Any]) -> None:
        """Update Blind Hike config columns using snake/camel fallbacks."""
        table = self.get_game_table(db)
        updates: Dict[str, Any] = {}

        column_map = {
            "target_lat": ["blind_hike_target_lat", "blindHikeTargetLat"],
            "target_lon": ["blind_hike_target_lon", "blindHikeTargetLon"],
            "horizontal_flip": ["blind_hike_horizontal_flip", "blindHikeHorizontalFlip"],
            "vertical_flip": ["blind_hike_vertical_flip", "blindHikeVerticalFlip"],
            "scale_factor": ["blind_hike_scale_factor", "blindHikeScaleFactor"],
            "rotation": ["blind_hike_rotation", "blindHikeRotation"],
            "max_markers": ["blind_hike_max_markers", "blindHikeMaxMarkers"],
            "marker_cooldown": ["blind_hike_marker_cooldown", "blindHikeMarkerCooldown"],
            "finish_radius_meters": ["blind_hike_finish_radius_meters", "blindHikeFinishRadiusMeters"],
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

    def get_marker_table(self, db: DbSession) -> Table:
        """Return reflected `blind_hike_marker` table."""
        return self._get_table(db, "blind_hike_marker")

    def create_marker_without_commit(self, db: DbSession, values: Dict[str, Any]) -> None:
        """Insert marker row without committing transaction."""
        table = self.get_marker_table(db)
        db.execute(insert(table).values(**values))

    def fetch_markers_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """List all markers in game ordered by placement time."""
        table = self.get_marker_table(db)
        rows = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .order_by(table.c["placed_at"].asc())
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def fetch_markers_by_team(self, db: DbSession, game_id: str, team_id: str) -> list[Dict[str, Any]]:
        """List team markers ordered by placement time."""
        table = self.get_marker_table(db)
        rows = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["team_id"] == team_id)
                .order_by(table.c["placed_at"].asc())
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def count_markers_by_team(self, db: DbSession, game_id: str, team_id: str) -> int:
        """Count markers placed by team in a game."""
        return len(self.fetch_markers_by_team(db, game_id, team_id))

    def get_last_marker_for_team(self, db: DbSession, game_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        """Fetch most recently placed marker for a team."""
        table = self.get_marker_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["team_id"] == team_id)
                .order_by(table.c["placed_at"].desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def marker_placed_at_datetime(marker: Dict[str, Any]) -> Optional[datetime]:
        """Return marker placement datetime when value is datetime-typed."""
        value = marker.get("placed_at")
        if isinstance(value, datetime):
            return value
        return None
