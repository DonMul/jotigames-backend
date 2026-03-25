from typing import Any, Dict, Optional

from sqlalchemy import Table, delete, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class ResourceRunRepository(GameLogicStateRepository):
    def get_resource_run_node_table(self, db: DbSession) -> Table:
        """Return reflected `resource_run_node` table."""
        return self._get_table(db, "resource_run_node")

    def fetch_nodes_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """List resource nodes for a game ordered by title."""
        table = self.get_resource_run_node_table(db)
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

    def get_node_by_game_id_and_node_id(self, db: DbSession, game_id: str, node_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one resource node by scoped game/node identifiers."""
        table = self.get_resource_run_node_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == node_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def create_node_without_commit(self, db: DbSession, values: Dict[str, Any]) -> None:
        """Insert resource node row without committing transaction."""
        table = self.get_resource_run_node_table(db)
        db.execute(insert(table).values(**values))

    def update_node_without_commit(self, db: DbSession, game_id: str, node_id: str, values: Dict[str, Any]) -> None:
        """Update resource node fields without commit."""
        if not values:
            return
        table = self.get_resource_run_node_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == node_id)
            .values(**values)
        )

    def delete_node_without_commit(self, db: DbSession, game_id: str, node_id: str) -> None:
        """Delete resource node by scoped identifiers without commit."""
        table = self.get_resource_run_node_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == node_id)
        )
