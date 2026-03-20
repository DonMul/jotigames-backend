from typing import Any, Dict, Optional

from sqlalchemy import Table, delete, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class CheckpointHeistRepository(GameLogicStateRepository):
    def get_checkpoint_heist_checkpoint_table(self, db: DbSession) -> Table:
        return self._get_table(db, "checkpoint_heist_checkpoint")

    def fetch_checkpoints_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_checkpoint_heist_checkpoint_table(db)
        order_column = table.c[self.get_order_column_name(db)]
        rows = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .order_by(order_column.asc(), table.c["title"].asc())
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def get_checkpoint_by_game_id_and_checkpoint_id(self, db: DbSession, game_id: str, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        table = self.get_checkpoint_heist_checkpoint_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == checkpoint_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def create_checkpoint_without_commit(self, db: DbSession, values: Dict[str, Any]) -> None:
        table = self.get_checkpoint_heist_checkpoint_table(db)
        db.execute(insert(table).values(**values))

    def update_checkpoint_without_commit(self, db: DbSession, game_id: str, checkpoint_id: str, values: Dict[str, Any]) -> None:
        if not values:
            return
        table = self.get_checkpoint_heist_checkpoint_table(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == checkpoint_id)
            .values(**values)
        )

    def delete_checkpoint_without_commit(self, db: DbSession, game_id: str, checkpoint_id: str) -> None:
        table = self.get_checkpoint_heist_checkpoint_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == checkpoint_id)
        )

    def get_next_order_index(self, db: DbSession, game_id: str) -> int:
        checkpoints = self.fetch_checkpoints_by_game_id(db, game_id)
        highest = 0
        for checkpoint in checkpoints:
            value = checkpoint.get("order_index")
            if value is None:
                value = checkpoint.get("sequence_order")
            highest = max(highest, int(value or 0))
        return highest + 1

    def get_order_column_name(self, db: DbSession) -> str:
        table = self.get_checkpoint_heist_checkpoint_table(db)
        return "order_index" if "order_index" in table.c else "sequence_order"

    def reorder_checkpoints_without_commit(self, db: DbSession, game_id: str, ordered_ids: list[str]) -> None:
        table = self.get_checkpoint_heist_checkpoint_table(db)
        order_column_name = "order_index" if "order_index" in table.c else "sequence_order"
        checkpoint_ids = [str(entry) for entry in ordered_ids if str(entry).strip()]

        if not checkpoint_ids:
            return

        current = self.fetch_checkpoints_by_game_id(db, game_id)
        known = {str(item.get("id") or ""): item for item in current}

        sequence = 1
        for checkpoint_id in checkpoint_ids:
            if checkpoint_id not in known:
                continue
            db.execute(
                update(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == checkpoint_id)
                .values(**{order_column_name: sequence})
            )
            sequence += 1
            known.pop(checkpoint_id, None)

        for checkpoint_id in known.keys():
            db.execute(
                update(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == checkpoint_id)
                .values(**{order_column_name: sequence})
            )
            sequence += 1
