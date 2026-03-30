from typing import Any, Dict, Optional

from sqlalchemy import Table, delete, insert, select, update

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


class GeoHunterRepository(GameLogicStateRepository):
    @staticmethod
    def normalize_visibility_mode(value: Any) -> str:
        """Normalize visibility mode to supported values."""
        raw = str(value or "").strip().lower()
        if raw == "in_range_only":
            return "in_range_only"
        return "all_visible"

    def get_visibility_mode(self, db: DbSession, game_id: str) -> str:
        """Read GeoHunter visibility mode from game row with settings fallback."""
        game = self.get_game_by_id(db, game_id) or {}
        raw_mode = game.get("geo_hunter_visibility_mode")
        if raw_mode is None:
            raw_mode = game.get("geoHunterVisibilityMode")
        if raw_mode is not None:
            return self.normalize_visibility_mode(raw_mode)

        settings = self.get_game_settings(db, game_id)
        return self.normalize_visibility_mode(settings.get("geohunter_visibility_mode"))

    def update_visibility_mode_without_commit(self, db: DbSession, game_id: str, *, visibility_mode: str) -> None:
        """Persist GeoHunter visibility mode in game row or settings fallback."""
        normalized_mode = self.normalize_visibility_mode(visibility_mode)
        game_table = self.get_game_table(db)
        values: Dict[str, Any] = {}

        if "geo_hunter_visibility_mode" in game_table.c:
            values["geo_hunter_visibility_mode"] = normalized_mode
        elif "geoHunterVisibilityMode" in game_table.c:
            values["geoHunterVisibilityMode"] = normalized_mode

        if values:
            db.execute(
                update(game_table)
                .where(game_table.c["id"] == game_id)
                .values(**values)
            )
            return

        settings = self.get_game_settings(db, game_id)
        settings["geohunter_visibility_mode"] = normalized_mode
        self.update_game_settings_without_commit(db, game_id, settings)

    def get_geo_point_table(self, db: DbSession) -> Table:
        """Return reflected `geo_point` table."""
        return self._get_table(db, "geo_point")

    def get_geo_choice_table(self, db: DbSession) -> Table:
        """Return reflected `geo_choice` table."""
        return self._get_table(db, "geo_choice")

    def get_geo_submission_table(self, db: DbSession) -> Table:
        """Return reflected `geo_submission` table."""
        return self._get_table(db, "geo_submission")

    @staticmethod
    def _submission_column_name(table: Table, *candidates: str) -> str:
        """Resolve one required column name from candidate aliases."""
        for candidate in candidates:
            if candidate in table.c:
                return candidate
        raise KeyError(f"submission column not found: {', '.join(candidates)}")

    def _submission_point_column(self, table: Table) -> str:
        return self._submission_column_name(table, "point_id", "pointId")

    def _submission_team_column(self, table: Table) -> str:
        return self._submission_column_name(table, "team_id", "teamId")

    def _submission_submitted_at_column(self, table: Table) -> str:
        return self._submission_column_name(table, "submitted_at", "submittedAt")

    def _submission_is_correct_column(self, table: Table) -> str:
        return self._submission_column_name(table, "is_correct", "isCorrect")

    def _submission_points_awarded_column(self, table: Table) -> str:
        return self._submission_column_name(table, "points_awarded", "pointsAwarded")

    def _submission_answer_text_column(self, table: Table) -> Optional[str]:
        return self._pick_column(table, ["answer_text", "answerText"])

    def _submission_selected_choice_ids_column(self, table: Table) -> Optional[str]:
        return self._pick_column(table, ["selected_choice_ids", "selectedChoiceIds"])

    def get_submission_by_team_and_poi(self, db: DbSession, *, team_id: str, poi_id: str) -> Optional[Dict[str, Any]]:
        """Fetch latest stored submission row for one team/POI pair."""
        table = self.get_geo_submission_table(db)
        point_col = self._submission_point_column(table)
        team_col = self._submission_team_column(table)
        submitted_at_col = self._submission_submitted_at_column(table)

        row = (
            db.execute(
                select(table)
                .where(table.c[point_col] == poi_id)
                .where(table.c[team_col] == team_id)
                .order_by(table.c[submitted_at_col].desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def fetch_submissions_by_team_and_poi_ids(self, db: DbSession, *, team_id: str, poi_ids: list[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch latest submission rows indexed by POI id for one team."""
        if not poi_ids:
            return {}

        table = self.get_geo_submission_table(db)
        point_col = self._submission_point_column(table)
        team_col = self._submission_team_column(table)
        submitted_at_col = self._submission_submitted_at_column(table)

        rows = (
            db.execute(
                select(table)
                .where(table.c[team_col] == team_id)
                .where(table.c[point_col].in_(poi_ids))
                .order_by(table.c[submitted_at_col].desc())
            )
            .mappings()
            .all()
        )

        mapped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            record = dict(row)
            point_id = str(record.get(point_col) or "")
            if not point_id or point_id in mapped:
                continue
            mapped[point_id] = record
        return mapped

    def create_submission_without_commit(self, db: DbSession, values: Dict[str, Any]) -> None:
        """Insert a geo_submission row without commit."""
        table = self.get_geo_submission_table(db)
        db.execute(insert(table).values(**values))

    def update_submission_without_commit(self, db: DbSession, submission_id: str, values: Dict[str, Any]) -> None:
        """Update geo_submission row by id without commit."""
        if not values:
            return
        table = self.get_geo_submission_table(db)
        if "id" not in table.c:
            return
        db.execute(
            update(table)
            .where(table.c["id"] == submission_id)
            .values(**values)
        )

    def update_submission_by_team_and_poi_without_commit(
        self,
        db: DbSession,
        *,
        team_id: str,
        poi_id: str,
        values: Dict[str, Any],
    ) -> None:
        """Update geo_submission row by team/POI pair without commit."""
        if not values:
            return
        table = self.get_geo_submission_table(db)
        point_col = self._submission_point_column(table)
        team_col = self._submission_team_column(table)
        db.execute(
            update(table)
            .where(table.c[point_col] == poi_id)
            .where(table.c[team_col] == team_id)
            .values(**values)
        )

    def fetch_pois_by_game_id(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """List POIs for a game ordered by title."""
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
        """Fetch one POI by scoped game/poi identifiers."""
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
        """Fetch and group choices by POI id for batch serialization."""
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
        """Insert POI row without committing transaction."""
        table = self.get_geo_point_table(db)
        db.execute(insert(table).values(**values))

    def update_poi_without_commit(self, db: DbSession, game_id: str, poi_id: str, values: Dict[str, Any]) -> None:
        """Update POI fields by scoped identifiers without commit."""
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
        """Delete POI by scoped identifiers without commit."""
        table = self.get_geo_point_table(db)
        db.execute(
            delete(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == poi_id)
        )

    def delete_choices_by_poi_without_commit(self, db: DbSession, poi_id: str) -> None:
        """Delete all choice rows belonging to one POI without commit."""
        table = self.get_geo_choice_table(db)
        db.execute(delete(table).where(table.c["point_id"] == poi_id))

    def create_choices_without_commit(self, db: DbSession, values: list[Dict[str, Any]]) -> None:
        """Bulk insert POI choices without committing transaction."""
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
        """Persist retry settings with support for schema naming variants."""
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
