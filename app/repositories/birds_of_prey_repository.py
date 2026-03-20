from typing import Any, Dict

from sqlalchemy import update

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
