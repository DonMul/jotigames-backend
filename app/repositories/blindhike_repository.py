from typing import Any, Dict

from sqlalchemy import update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class BlindHikeRepository(GameLogicStateRepository):
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
            "target_lat": self._first_present(game, ["blind_hike_target_lat", "blindHikeTargetLat"]),
            "target_lon": self._first_present(game, ["blind_hike_target_lon", "blindHikeTargetLon"]),
            "horizontal_flip": bool(self._first_present(game, ["blind_hike_horizontal_flip", "blindHikeHorizontalFlip"], False)),
            "vertical_flip": bool(self._first_present(game, ["blind_hike_vertical_flip", "blindHikeVerticalFlip"], False)),
            "scale_factor": str(self._first_present(game, ["blind_hike_scale_factor", "blindHikeScaleFactor"], "1.0")),
            "rotation": int(self._first_present(game, ["blind_hike_rotation", "blindHikeRotation"], 0) or 0),
            "max_markers": self._first_present(game, ["blind_hike_max_markers", "blindHikeMaxMarkers"]),
            "marker_cooldown": int(self._first_present(game, ["blind_hike_marker_cooldown", "blindHikeMarkerCooldown"], 0) or 0),
        }

    def update_configuration_without_commit(self, db: DbSession, game_id: str, values: Dict[str, Any]) -> None:
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
