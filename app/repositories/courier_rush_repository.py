from typing import Any, Dict

from sqlalchemy import Table, delete, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class CourierRushRepository(GameLogicStateRepository):
    @staticmethod
    def _first_present(row: Dict[str, Any], keys: list[str], default: Any = None) -> Any:
        for key in keys:
            if key in row:
                return row.get(key)
        return default

    def get_pickup_table(self, db: DbSession) -> Table:
        return self._get_table(db, "courier_rush_pickup_point")

    def get_dropoff_table(self, db: DbSession) -> Table:
        return self._get_table(db, "courier_rush_dropoff_point")

    def get_configuration(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        game = self.get_game_by_id(db, game_id)
        if game is None:
            return {}

        return {
            "pickup_mode": "random" if bool(self._first_present(game, ["courier_rush_pickup_randomized", "courierRushPickupRandomized"], False)) else "predefined",
            "dropoff_mode": "random" if bool(self._first_present(game, ["courier_rush_dropoff_randomized", "courierRushDropoffRandomized"], True)) else "fixed",
            "max_active_pickups": int(self._first_present(game, ["courier_rush_max_active_pickups", "courierRushMaxActivePickups"], 3) or 3),
            "pickup_spawn_area_geojson": self._first_present(game, ["courier_rush_pickup_spawn_area_geojson", "courierRushPickupSpawnAreaGeoJson"]),
        }

    def update_configuration_without_commit(self, db: DbSession, game_id: str, values: Dict[str, Any]) -> None:
        table = self.get_game_table(db)
        updates: Dict[str, Any] = {}

        column_map = {
            "pickup_mode": ["courier_rush_pickup_randomized", "courierRushPickupRandomized"],
            "dropoff_mode": ["courier_rush_dropoff_randomized", "courierRushDropoffRandomized"],
            "max_active_pickups": ["courier_rush_max_active_pickups", "courierRushMaxActivePickups"],
            "pickup_spawn_area_geojson": ["courier_rush_pickup_spawn_area_geojson", "courierRushPickupSpawnAreaGeoJson"],
        }

        for payload_key, candidates in column_map.items():
            if payload_key not in values:
                continue

            value = values[payload_key]
            if payload_key == "pickup_mode":
                value = str(value).strip().lower() == "random"
            elif payload_key == "dropoff_mode":
                value = str(value).strip().lower() != "fixed"

            for column_name in candidates:
                if column_name in table.c:
                    updates[column_name] = value
                    break

        if updates:
            db.execute(
                update(table)
                .where(table.c["id"] == game_id)
                .values(**updates)
            )

    def fetch_pickups_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_pickup_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def get_pickup_by_game_id_and_pickup_id(self, db: DbSession, game_id: str, pickup_id: str) -> Dict[str, Any] | None:
        table = self.get_pickup_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == pickup_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def create_pickup_without_commit(self, db: DbSession, values: Dict[str, Any]) -> str:
        table = self.get_pickup_table(db)
        result = db.execute(insert(table).values(**values).returning(table.c["id"]))
        return str(result.scalar_one())

    def update_pickup_without_commit(self, db: DbSession, game_id: str, pickup_id: str, values: Dict[str, Any]) -> None:
        table = self.get_pickup_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == pickup_id)
            .values(**values)
        )

    def delete_pickup_without_commit(self, db: DbSession, game_id: str, pickup_id: str) -> None:
        table = self.get_pickup_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == pickup_id)
        )

    def fetch_dropoffs_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_dropoff_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def get_dropoff_by_game_id_and_dropoff_id(self, db: DbSession, game_id: str, dropoff_id: str) -> Dict[str, Any] | None:
        table = self.get_dropoff_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == dropoff_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    def create_dropoff_without_commit(self, db: DbSession, values: Dict[str, Any]) -> str:
        table = self.get_dropoff_table(db)
        result = db.execute(insert(table).values(**values).returning(table.c["id"]))
        return str(result.scalar_one())

    def update_dropoff_without_commit(self, db: DbSession, game_id: str, dropoff_id: str, values: Dict[str, Any]) -> None:
        table = self.get_dropoff_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == dropoff_id)
            .values(**values)
        )

    def delete_dropoff_without_commit(self, db: DbSession, game_id: str, dropoff_id: str) -> None:
        table = self.get_dropoff_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == dropoff_id)
        )
