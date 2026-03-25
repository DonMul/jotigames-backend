from typing import Any, Dict, Optional

from sqlalchemy import Table, delete, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class EchoHuntRepository(GameLogicStateRepository):
    """Persistence helpers for Echo Hunt beacon CRUD operations."""

    def get_echo_hunt_beacon_table(self, db: DbSession) -> Table:
        """Return reflected `echo_hunt_beacon` table."""
        return self._get_table(db, "echo_hunt_beacon")

    def fetch_beacons_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """List beacons for game ordered by title."""
        table = self.get_echo_hunt_beacon_table(db)
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

    def get_beacon_by_game_id_and_beacon_id(self, db: DbSession, game_id: str, beacon_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one beacon by scoped game/beacon identifiers."""
        table = self.get_echo_hunt_beacon_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == beacon_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def create_beacon_without_commit(self, db: DbSession, values: Dict[str, Any]) -> None:
        """Insert beacon row without committing transaction."""
        table = self.get_echo_hunt_beacon_table(db)
        db.execute(insert(table).values(**values))

    def update_beacon_without_commit(self, db: DbSession, game_id: str, beacon_id: str, values: Dict[str, Any]) -> None:
        """Update beacon fields without commit."""
        if not values:
            return
        table = self.get_echo_hunt_beacon_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == beacon_id)
            .values(**values)
        )

    def delete_beacon_without_commit(self, db: DbSession, game_id: str, beacon_id: str) -> None:
        """Delete beacon by scoped identifiers without commit."""
        table = self.get_echo_hunt_beacon_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == beacon_id)
        )
