import json
import ast
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from sqlalchemy import MetaData, Table, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.sqltypes import JSON as SqlAlchemyJson

from app.dependencies import DbSession


class GameLogicStateRepository:
    """Common repository helpers for game-state JSON and team score updates."""

    _SETTINGS_COLUMN_CANDIDATES = ["settings", "game_settings", "settings_json", "gameSettings"]
    _GAME_ID_COLUMN_CANDIDATES = ["id", "game_id", "gameId"]
    _TEAM_ID_COLUMN_CANDIDATES = ["id", "team_id", "teamId"]
    _TEAM_GAME_ID_COLUMN_CANDIDATES = ["game_id", "gameId"]

    def __init__(self) -> None:
        """Initialize reflection metadata used for dynamic table access."""
        self._metadata = MetaData()

    def _get_table(self, db: DbSession, table_name: str) -> Table:
        """Reflect and return a table by name using the active DB connection."""
        return Table(table_name, self._metadata, autoload_with=db.get_bind())

    def get_game_table(self, db: DbSession) -> Table:
        """Return reflected metadata for the `game` table."""
        return self._get_table(db, "game")

    @staticmethod
    def _pick_column(table: Table, candidates: list[str]) -> Optional[str]:
        """Resolve the first existing column name from candidate aliases."""
        for name in candidates:
            if name in table.c:
                return name
        return None

    def get_team_table(self, db: DbSession) -> Table:
        """Return reflected metadata for the `team` table."""
        return self._get_table(db, "team")

    def _get_game_id_column(self, table: Table) -> str:
        """Resolve game table primary key column name with legacy compatibility."""
        name = self._pick_column(table, self._GAME_ID_COLUMN_CANDIDATES)
        if not name:
            raise KeyError("game id column not found")
        return name

    def _get_team_id_column(self, table: Table) -> str:
        """Resolve team table primary key column name with legacy compatibility."""
        name = self._pick_column(table, self._TEAM_ID_COLUMN_CANDIDATES)
        if not name:
            raise KeyError("team id column not found")
        return name

    def _get_team_game_id_column(self, table: Table) -> str:
        """Resolve team table game foreign key column name with legacy compatibility."""
        name = self._pick_column(table, self._TEAM_GAME_ID_COLUMN_CANDIDATES)
        if not name:
            raise KeyError("team game_id column not found")
        return name

    def get_game_by_id(self, db: DbSession, game_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a game row by id as a plain dictionary."""
        table = self.get_game_table(db)
        game_id_col = self._get_game_id_column(table)
        row = db.execute(select(table).where(table.c[game_id_col] == game_id).limit(1)).mappings().first()
        if row is None:
            return None
        return dict(row)

    def get_team_by_game_and_id(self, db: DbSession, game_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        """Fetch one team row scoped to a game id and team id."""
        table = self.get_team_table(db)
        team_id_col = self._get_team_id_column(table)
        team_game_id_col = self._get_team_game_id_column(table)
        row = (
            db.execute(
                select(table)
                .where(table.c[team_game_id_col] == game_id)
                .where(table.c[team_id_col] == team_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def fetch_teams_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """Fetch all team rows for a specific game."""
        table = self.get_team_table(db)
        team_game_id_col = self._get_team_game_id_column(table)
        rows = db.execute(select(table).where(table.c[team_game_id_col] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _deserialize_json_value(value: Any) -> Dict[str, Any]:
        """Decode settings-like JSON values into dictionaries safely."""
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, (bytes, bytearray)):
            try:
                value = value.decode("utf-8")
            except Exception:
                return {}
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {}
            try:
                decoded = json.loads(stripped)
                if isinstance(decoded, dict):
                    return decoded
            except json.JSONDecodeError:
                try:
                    decoded = ast.literal_eval(stripped)
                except (ValueError, SyntaxError):
                    return {}
                if isinstance(decoded, dict):
                    return dict(decoded)
                return {}
        return {}

    def get_game_settings(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        """Return decoded game settings from whichever settings column is populated."""
        game = self.get_game_by_id(db, game_id)
        if game is None:
            return {}

        raw_settings = None
        for key in self._SETTINGS_COLUMN_CANDIDATES:
            if key not in game:
                continue
            value = game.get(key)
            if isinstance(value, str) and not value.strip():
                continue
            if value is None:
                continue
            raw_settings = value
            break
        if raw_settings is None:
            for key in self._SETTINGS_COLUMN_CANDIDATES:
                if key in game:
                    raw_settings = game.get(key)
                    break

        return self._deserialize_json_value(raw_settings)

    def update_game_settings_without_commit(self, db: DbSession, game_id: str, settings_value: Dict[str, Any]) -> None:
        """Write normalized game settings and updated timestamp without commit."""
        table = self.get_game_table(db)
        settings_column = self._pick_column(table, self._SETTINGS_COLUMN_CANDIDATES)
        if settings_column is None:
            return

        current_game_row = self.get_game_by_id(db, game_id) or {}
        for key in self._SETTINGS_COLUMN_CANDIDATES:
            if key not in table.c:
                continue
            value = current_game_row.get(key)
            if isinstance(value, str) and not value.strip():
                continue
            if value is None:
                continue
            settings_column = key
            break

        write_value: Any = settings_value
        column_type = table.c[settings_column].type
        is_json_column = isinstance(column_type, SqlAlchemyJson)
        if not is_json_column:
            write_value = json.dumps(settings_value, ensure_ascii=False)

        values: Dict[str, Any] = {
            settings_column: write_value,
        }

        updated_column = self._pick_column(table, ["updated_at", "updatedAt"])
        if updated_column is not None:
            values[updated_column] = datetime.now(UTC).replace(tzinfo=None)

        db.execute(
            update(table)
            .where(table.c[self._get_game_id_column(table)] == game_id)
            .values(**values)
        )

    def increment_team_geo_score_without_commit(self, db: DbSession, team_id: str, points: int) -> int:
        """Increment and persist one team's geo score without committing."""
        team_table = self.get_team_table(db)
        team_id_col = self._get_team_id_column(team_table)
        team = db.execute(select(team_table).where(team_table.c[team_id_col] == team_id).limit(1)).mappings().first()
        if team is None:
            return 0

        current = int(team.get("geo_score") or 0)
        updated = current + int(points)
        db.execute(
            update(team_table)
            .where(team_table.c[team_id_col] == team_id)
            .values(geo_score=updated)
        )
        return updated

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Convert raw value to float with `None` fallback."""
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if numeric == numeric else None

    @staticmethod
    def _to_iso(value: Any) -> str:
        """Normalize datetime-like values into string representation."""
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value or "")

    def _extract_team_location(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize team row location fields into API shape."""
        latitude = self._safe_float(row.get("geo_latitude", row.get("geoLatitude", row.get("latitude"))))
        longitude = self._safe_float(row.get("geo_longitude", row.get("geoLongitude", row.get("longitude"))))
        updated_at_raw = row.get("geo_updated_at", row.get("geoUpdatedAt", row.get("updated_at", row.get("updatedAt"))))
        return {
            "lat": latitude,
            "lon": longitude,
            "updated_at": self._to_iso(updated_at_raw),
        }

    def get_team_location(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Return normalized location for one team in a game."""
        team = self.get_team_by_game_and_id(db, game_id, team_id)
        if not isinstance(team, dict):
            return {"lat": None, "lon": None, "updated_at": ""}
        return self._extract_team_location(team)

    def update_team_location_without_commit(self, db: DbSession, game_id: str, team_id: str, *, latitude: float, longitude: float) -> None:
        """Persist team location coordinates without committing transaction."""
        table = self.get_team_table(db)
        team_id_col = self._get_team_id_column(table)
        team_game_id_col = self._get_team_game_id_column(table)
        lat_col = self._pick_column(table, ["geo_latitude", "geoLatitude", "latitude"])
        lon_col = self._pick_column(table, ["geo_longitude", "geoLongitude", "longitude"])
        updated_col = self._pick_column(table, ["geo_updated_at", "geoUpdatedAt", "updated_at", "updatedAt"])

        updates: Dict[str, Any] = {}
        if lat_col:
            updates[lat_col] = float(latitude)
        if lon_col:
            updates[lon_col] = float(longitude)
        if updated_col:
            updates[updated_col] = datetime.now(UTC).replace(tzinfo=None)
        if not updates:
            return

        db.execute(
            update(table)
            .where(table.c[team_game_id_col] == game_id)
            .where(table.c[team_id_col] == team_id)
            .values(**updates)
        )

    @staticmethod
    def commit_changes(db: DbSession) -> None:
        """Commit the active transaction on the provided DB session."""
        db.commit()

    @staticmethod
    def rollback_on_error(db: DbSession, error: Exception) -> None:
        """Rollback only for SQLAlchemy-originated errors."""
        if isinstance(error, SQLAlchemyError):
            db.rollback()
