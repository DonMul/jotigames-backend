from typing import Any, Dict

from sqlalchemy import Table, delete, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class MarketCrashRepository(GameLogicStateRepository):
    def get_point_table(self, db: DbSession) -> Table:
        return self._get_table(db, "market_crash_point")

    def get_resource_table(self, db: DbSession) -> Table:
        return self._get_table(db, "market_crash_resource")

    def get_point_resource_table(self, db: DbSession) -> Table:
        return self._get_table(db, "market_crash_point_resource")

    def fetch_resources_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_resource_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def get_resource_by_game_id_and_resource_id(self, db: DbSession, game_id: str, resource_id: str) -> Dict[str, Any] | None:
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
        table = self.get_resource_table(db)
        result = db.execute(insert(table).values(**values).returning(table.c["id"]))
        return str(result.scalar_one())

    def update_resource_without_commit(self, db: DbSession, game_id: str, resource_id: str, values: Dict[str, Any]) -> None:
        table = self.get_resource_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == resource_id)
            .values(**values)
        )

    def delete_resource_without_commit(self, db: DbSession, game_id: str, resource_id: str) -> None:
        table = self.get_resource_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == resource_id)
        )

    def fetch_points_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_point_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def get_point_by_game_id_and_point_id(self, db: DbSession, game_id: str, point_id: str) -> Dict[str, Any] | None:
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
        table = self.get_point_table(db)
        result = db.execute(insert(table).values(**values).returning(table.c["id"]))
        return str(result.scalar_one())

    def update_point_without_commit(self, db: DbSession, game_id: str, point_id: str, values: Dict[str, Any]) -> None:
        table = self.get_point_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == point_id)
            .values(**values)
        )

    def delete_point_without_commit(self, db: DbSession, game_id: str, point_id: str) -> None:
        table = self.get_point_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == point_id)
        )

    def fetch_point_resources_by_point_id(self, db: DbSession, point_id: str) -> list[Dict[str, Any]]:
        table = self.get_point_resource_table(db)
        rows = db.execute(select(table).where(table.c["point_id"] == point_id)).mappings().all()
        return [dict(row) for row in rows]

    def replace_point_resources_without_commit(self, db: DbSession, point_id: str, rows: list[Dict[str, Any]]) -> None:
        table = self.get_point_resource_table(db)
        db.execute(delete(table).where(table.c["point_id"] == point_id))
        if rows:
            values = [{**row, "point_id": point_id} for row in rows]
            db.execute(insert(table), values)
