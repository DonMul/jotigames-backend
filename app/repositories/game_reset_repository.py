from typing import Optional

from sqlalchemy import MetaData, Table, delete, select, update

from app.dependencies import DbSession


class GameResetRepository:
    def __init__(self) -> None:
        self._metadata = MetaData()

    def _getTableOrNone(self, db: DbSession, table_name: str) -> Optional[Table]:
        try:
            return Table(table_name, self._metadata, autoload_with=db.get_bind())
        except Exception:
            return None

    def resetTeamsByGameId(self, db: DbSession, game_id: str, reset_geo_score: bool) -> None:
        team_table = self._getTableOrNone(db, "team")
        if team_table is None:
            return

        values: dict[str, object] = {}
        if "pending_attack" in team_table.c:
            values["pending_attack"] = False
        if "pending_peek" in team_table.c:
            values["pending_peek"] = False
        if "pending_skip" in team_table.c:
            values["pending_skip"] = False
        if reset_geo_score and "geo_score" in team_table.c:
            values["geo_score"] = 0

        if not values:
            return

        db.execute(
            update(team_table)
            .where(team_table.c["game_id"] == game_id)
            .values(**values)
        )

    def resetCardsByGameId(self, db: DbSession, game_id: str) -> None:
        card_table = self._getTableOrNone(db, "card")
        if card_table is None:
            return

        values: dict[str, object] = {}
        if "holder_team_id" in card_table.c:
            values["holder_team_id"] = None
        if "locked" in card_table.c:
            values["locked"] = False

        if not values:
            return

        db.execute(
            update(card_table)
            .where(card_table.c["game_id"] == game_id)
            .values(**values)
        )

    def deleteByGameId(self, db: DbSession, table_name: str, game_id: str) -> None:
        table = self._getTableOrNone(db, table_name)
        if table is None or "game_id" not in table.c:
            return
        db.execute(delete(table).where(table.c["game_id"] == game_id))

    def deleteByParentGameId(
        self,
        db: DbSession,
        table_name: str,
        foreign_key_column: str,
        parent_table_name: str,
        game_id: str,
    ) -> None:
        table = self._getTableOrNone(db, table_name)
        parent_table = self._getTableOrNone(db, parent_table_name)
        if table is None or parent_table is None:
            return
        if foreign_key_column not in table.c or "id" not in parent_table.c or "game_id" not in parent_table.c:
            return

        subquery = select(parent_table.c["id"]).where(parent_table.c["game_id"] == game_id)
        db.execute(delete(table).where(table.c[foreign_key_column].in_(subquery)))

    def deleteByTeamGameId(self, db: DbSession, table_name: str, team_fk_column: str, game_id: str) -> None:
        table = self._getTableOrNone(db, table_name)
        team_table = self._getTableOrNone(db, "team")
        if table is None or team_table is None:
            return
        if team_fk_column not in table.c or "id" not in team_table.c or "game_id" not in team_table.c:
            return

        subquery = select(team_table.c["id"]).where(team_table.c["game_id"] == game_id)
        db.execute(delete(table).where(table.c[team_fk_column].in_(subquery)))

    def resetTerritoryZonesByGameId(self, db: DbSession, game_id: str) -> None:
        zone_table = self._getTableOrNone(db, "territory_zone")
        if zone_table is None or "game_id" not in zone_table.c:
            return

        values: dict[str, object] = {}
        if "owner_team_id" in zone_table.c:
            values["owner_team_id"] = None
        if "captured_at" in zone_table.c:
            values["captured_at"] = None

        if not values:
            return

        db.execute(update(zone_table).where(zone_table.c["game_id"] == game_id).values(**values))

    def resetCodeConspiracyWinnerByGameId(self, db: DbSession, game_id: str) -> None:
        game_table = self._getTableOrNone(db, "game")
        if game_table is None or "id" not in game_table.c or "code_conspiracy_winner_team_id" not in game_table.c:
            return

        db.execute(
            update(game_table)
            .where(game_table.c["id"] == game_id)
            .values(code_conspiracy_winner_team_id=None)
        )

    def deleteCardUsageByGameId(self, db: DbSession, game_id: str) -> None:
        usage_table = self._getTableOrNone(db, "card_usage")
        card_table = self._getTableOrNone(db, "card")
        if usage_table is None or card_table is None:
            return
        if "card_id" not in usage_table.c or "id" not in card_table.c or "game_id" not in card_table.c:
            return

        subquery = select(card_table.c["id"]).where(card_table.c["game_id"] == game_id)
        db.execute(delete(usage_table).where(usage_table.c["card_id"].in_(subquery)))
