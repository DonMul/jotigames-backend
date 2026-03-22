import json
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from sqlalchemy import MetaData, Table, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.dependencies import DbSession


class GameLogicStateRepository:
    def __init__(self) -> None:
        self._metadata = MetaData()

    def _get_table(self, db: DbSession, table_name: str) -> Table:
        return Table(table_name, self._metadata, autoload_with=db.get_bind())

    def get_game_table(self, db: DbSession) -> Table:
        return self._get_table(db, "game")

    @staticmethod
    def _pick_column(table: Table, candidates: list[str]) -> Optional[str]:
        for name in candidates:
            if name in table.c:
                return name
        return None

    def get_team_table(self, db: DbSession) -> Table:
        return self._get_table(db, "team")

    def get_game_by_id(self, db: DbSession, game_id: str) -> Optional[Dict[str, Any]]:
        table = self.get_game_table(db)
        row = db.execute(select(table).where(table.c["id"] == game_id).limit(1)).mappings().first()
        if row is None:
            return None
        return dict(row)

    def get_team_by_game_and_id(self, db: DbSession, game_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        table = self.get_team_table(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == team_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def fetch_teams_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.get_team_table(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _deserialize_json_value(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {}
            try:
                decoded = json.loads(stripped)
                if isinstance(decoded, dict):
                    return decoded
            except json.JSONDecodeError:
                return {}
        return {}

    def get_game_settings(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        game = self.get_game_by_id(db, game_id)
        if game is None:
            return {}

        raw_settings = None
        for key in ["settings", "game_settings", "settings_json", "gameSettings"]:
            if key in game:
                raw_settings = game.get(key)
                break

        return self._deserialize_json_value(raw_settings)

    def update_game_settings_without_commit(self, db: DbSession, game_id: str, settings_value: Dict[str, Any]) -> None:
        table = self.get_game_table(db)
        settings_column = self._pick_column(table, ["settings", "game_settings", "settings_json", "gameSettings"])
        if settings_column is None:
            return

        values: Dict[str, Any] = {
            settings_column: settings_value,
        }

        updated_column = self._pick_column(table, ["updated_at", "updatedAt"])
        if updated_column is not None:
            values[updated_column] = datetime.now(UTC).replace(tzinfo=None)

        db.execute(
            update(table)
            .where(table.c["id"] == game_id)
            .values(**values)
        )

    def increment_team_geo_score_without_commit(self, db: DbSession, team_id: str, points: int) -> int:
        team_table = self.get_team_table(db)
        team = db.execute(select(team_table).where(team_table.c["id"] == team_id).limit(1)).mappings().first()
        if team is None:
            return 0

        current = int(team.get("geo_score") or 0)
        updated = current + int(points)
        db.execute(
            update(team_table)
            .where(team_table.c["id"] == team_id)
            .values(geo_score=updated)
        )
        return updated

    @staticmethod
    def commit_changes(db: DbSession) -> None:
        db.commit()

    @staticmethod
    def rollback_on_error(db: DbSession, error: Exception) -> None:
        if isinstance(error, SQLAlchemyError):
            db.rollback()
