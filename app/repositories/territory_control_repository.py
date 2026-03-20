from typing import Any, Dict, Optional

from sqlalchemy import Table, delete, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class TerritoryControlRepository(GameLogicStateRepository):
    def get_territory_zone_table(self, db: DbSession) -> Table:
        return self._get_table(db, "territory_zone")

    def fetch_zones_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_territory_zone_table(db)
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

    def get_zone_by_game_id_and_zone_id(self, db: DbSession, game_id: str, zone_id: str) -> Optional[Dict[str, Any]]:
        table = self.get_territory_zone_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == zone_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def create_zone_without_commit(self, db: DbSession, values: Dict[str, Any]) -> None:
        table = self.get_territory_zone_table(db)
        db.execute(insert(table).values(**values))

    def update_zone_without_commit(self, db: DbSession, game_id: str, zone_id: str, values: Dict[str, Any]) -> None:
        if not values:
            return
        table = self.get_territory_zone_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == zone_id)
            .values(**values)
        )

    def delete_zone_without_commit(self, db: DbSession, game_id: str, zone_id: str) -> None:
        table = self.get_territory_zone_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == zone_id)
        )
