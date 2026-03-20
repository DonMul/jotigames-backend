from typing import Any, Dict, Optional

from sqlalchemy import Table, delete, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class GeoHunterRepository(GameLogicStateRepository):
    def get_geo_point_table(self, db: DbSession) -> Table:
        return self._get_table(db, "geo_point")

    def get_geo_choice_table(self, db: DbSession) -> Table:
        return self._get_table(db, "geo_choice")

    def fetch_pois_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_geo_point_table(db)
        rows = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .order_by(table.c["title"].asc())
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def get_poi_by_game_id_and_poi_id(self, db: DbSession, game_id: str, poi_id: str) -> Optional[Dict[str, Any]]:
        table = self.get_geo_point_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == poi_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def fetch_choices_by_poi_ids(self, db: DbSession, poi_ids: list[str]) -> Dict[str, list[Dict[str, Any]]]:
        if not poi_ids:
            return {}

        table = self.get_geo_choice_table(db)
        rows = (
            db.execute(
                select(table)
                .where(table.c["point_id"].in_(poi_ids))
                .order_by(table.c["id"].asc())
            )
            .mappings()
            .all()
        )

        grouped: Dict[str, list[Dict[str, Any]]] = {}
        for row in rows:
            mapped = dict(row)
            point_id = str(mapped.get("point_id") or "")
            grouped.setdefault(point_id, []).append(mapped)
        return grouped

    def create_poi_without_commit(self, db: DbSession, values: Dict[str, Any]) -> None:
        table = self.get_geo_point_table(db)
        db.execute(insert(table).values(**values))

    def update_poi_without_commit(self, db: DbSession, game_id: str, poi_id: str, values: Dict[str, Any]) -> None:
        if not values:
            return
        table = self.get_geo_point_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == poi_id)
            .values(**values)
        )

    def delete_poi_without_commit(self, db: DbSession, game_id: str, poi_id: str) -> None:
        table = self.get_geo_point_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == poi_id)
        )

    def delete_choices_by_poi_without_commit(self, db: DbSession, poi_id: str) -> None:
        table = self.get_geo_choice_table(db)
        db.execute(delete(table).where(table.c["point_id"] == poi_id))

    def create_choices_without_commit(self, db: DbSession, values: list[Dict[str, Any]]) -> None:
        if not values:
            return
        table = self.get_geo_choice_table(db)
        db.execute(insert(table), values)

    def update_retry_settings_without_commit(
        self,
        db: DbSession,
        game_id: str,
        *,
        retry_enabled: bool,
        retry_timeout_seconds: int,
    ) -> None:
        table = self.get_game_table(db)
        values: Dict[str, Any] = {}

        if "geo_hunter_retry_enabled" in table.c:
            values["geo_hunter_retry_enabled"] = bool(retry_enabled)
        elif "geoHunterRetryEnabled" in table.c:
            values["geoHunterRetryEnabled"] = bool(retry_enabled)

        if "geo_hunter_retry_timeout_seconds" in table.c:
            values["geo_hunter_retry_timeout_seconds"] = int(retry_timeout_seconds)
        elif "geoHunterRetryTimeoutSeconds" in table.c:
            values["geoHunterRetryTimeoutSeconds"] = int(retry_timeout_seconds)

        if values:
            db.execute(
                update(table)
                .where(table.c["id"] == game_id)
                .values(**values)
            )
