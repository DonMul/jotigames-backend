from typing import Any, Dict, Optional

from sqlalchemy import MetaData, Table, delete, insert, or_, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.dependencies import DbSession


class GameRepository:
    def __init__(self) -> None:
        self._metadata = MetaData()

    def getGameTable(self, db: DbSession) -> Table:
        return Table("game", self._metadata, autoload_with=db.get_bind())

    def getGameTypeAvailabilityTable(self, db: DbSession) -> Table:
        return Table("game_type_availability", self._metadata, autoload_with=db.get_bind())

    def getGameManagerTable(self, db: DbSession) -> Table:
        return Table("game_manager", self._metadata, autoload_with=db.get_bind())

    def getGameMasterTable(self, db: DbSession) -> Table:
        return Table("game_master", self._metadata, autoload_with=db.get_bind())

    def fetchGameTypesByEnabled(self, db: DbSession, enabled: bool) -> list[str]:
        table = self.getGameTypeAvailabilityTable(db)
        rows = db.execute(select(table.c["game_type"], table.c["enabled"])).mappings().all()
        return [str(row["game_type"]) for row in rows if bool(row["enabled"]) is enabled]

    def fetchGameTypeAvailability(self, db: DbSession) -> list[Dict[str, Any]]:
        table = self.getGameTypeAvailabilityTable(db)
        rows = db.execute(select(table.c["game_type"], table.c["enabled"])).mappings().all()
        return [
            {
                "game_type": str(row["game_type"]),
                "enabled": bool(row["enabled"]),
            }
            for row in rows
        ]

    def replaceGameTypeAvailabilityWithoutCommit(self, db: DbSession, enabled_game_types: list[str]) -> None:
        table = self.getGameTypeAvailabilityTable(db)
        rows = db.execute(select(table.c["game_type"])).mappings().all()

        existing_types = {str(row["game_type"]) for row in rows}
        enabled_set = {str(game_type).strip() for game_type in enabled_game_types if str(game_type).strip()}

        for game_type in existing_types:
            db.execute(
                update(table)
                .where(table.c["game_type"] == game_type)
                .values(enabled=game_type in enabled_set)
            )

        for game_type in sorted(enabled_set - existing_types):
            db.execute(
                insert(table).values(game_type=game_type, enabled=True)
            )

    def fetchGamesByOwnerId(self, db: DbSession, owner_id: Optional[str] = None) -> list[Dict[str, Any]]:
        table = self.getGameTable(db)
        query = select(table)
        if owner_id and "owner_id" in table.c:
            query = query.where(table.c["owner_id"] == owner_id)
        rows = db.execute(query).mappings().all()
        return [dict(row) for row in rows]

    def fetchGameSummariesByOwnerId(self, db: DbSession, owner_id: str) -> list[Dict[str, Any]]:
        table = self.getGameTable(db)
        query = select(
            table.c["id"],
            table.c["name"],
            table.c["game_type"],
            table.c["start_at"],
            table.c["end_at"],
        )
        if "owner_id" in table.c:
            query = query.where(table.c["owner_id"] == owner_id)
        rows = db.execute(query).mappings().all()
        return [dict(row) for row in rows]

    def fetchGameSummariesByManagerUserId(self, db: DbSession, user_id: str) -> list[Dict[str, Any]]:
        game_table = self.getGameTable(db)
        manager_table = self.getGameManagerTable(db)
        query = (
            select(
                game_table.c["id"],
                game_table.c["name"],
                game_table.c["game_type"],
                game_table.c["start_at"],
                game_table.c["end_at"],
            )
            .select_from(game_table.join(manager_table, manager_table.c["game_id"] == game_table.c["id"]))
            .where(manager_table.c["user_id"] == user_id)
        )
        rows = db.execute(query).mappings().all()
        return [dict(row) for row in rows]

    def fetchGameSummariesByGameMasterUserId(self, db: DbSession, user_id: str) -> list[Dict[str, Any]]:
        game_table = self.getGameTable(db)
        game_master_table = self.getGameMasterTable(db)
        query = (
            select(
                game_table.c["id"],
                game_table.c["name"],
                game_table.c["game_type"],
                game_table.c["start_at"],
                game_table.c["end_at"],
            )
            .select_from(game_table.join(game_master_table, game_master_table.c["game_id"] == game_table.c["id"]))
            .where(game_master_table.c["user_id"] == user_id)
        )
        rows = db.execute(query).mappings().all()
        return [dict(row) for row in rows]

    def isGameOwnerByGameIdAndUserId(self, db: DbSession, game_id: str, user_id: str) -> bool:
        game_table = self.getGameTable(db)
        row = db.execute(
            select(game_table.c["id"])
            .where(game_table.c["id"] == game_id)
            .where(game_table.c["owner_id"] == user_id)
            .limit(1)
        ).first()
        return row is not None

    def hasGameManagerByGameIdAndUserId(self, db: DbSession, game_id: str, user_id: str) -> bool:
        manager_table = self.getGameManagerTable(db)
        row = db.execute(
            select(manager_table.c["game_id"])
            .where(manager_table.c["game_id"] == game_id)
            .where(manager_table.c["user_id"] == user_id)
            .limit(1)
        ).first()
        return row is not None

    def fetchGameManagerUserIdsByGameId(self, db: DbSession, game_id: str) -> list[str]:
        manager_table = self.getGameManagerTable(db)
        rows = db.execute(
            select(manager_table.c["user_id"]).where(manager_table.c["game_id"] == game_id)
        ).all()
        return [str(row[0]) for row in rows]

    def hasGameMasterByGameIdAndUserId(self, db: DbSession, game_id: str, user_id: str) -> bool:
        game_master_table = self.getGameMasterTable(db)
        row = db.execute(
            select(game_master_table.c["game_id"])
            .where(game_master_table.c["game_id"] == game_id)
            .where(game_master_table.c["user_id"] == user_id)
            .limit(1)
        ).first()
        return row is not None

    def fetchGameMasterUserIdsByGameId(self, db: DbSession, game_id: str) -> list[str]:
        game_master_table = self.getGameMasterTable(db)
        rows = db.execute(
            select(game_master_table.c["user_id"]).where(game_master_table.c["game_id"] == game_id)
        ).all()
        return [str(row[0]) for row in rows]

    def createGameManagerByGameIdAndUserId(self, db: DbSession, game_id: str, user_id: str) -> None:
        manager_table = self.getGameManagerTable(db)
        db.execute(insert(manager_table).values(game_id=game_id, user_id=user_id))
        db.commit()

    def createGameMasterByGameIdAndUserId(self, db: DbSession, game_id: str, user_id: str) -> None:
        game_master_table = self.getGameMasterTable(db)
        db.execute(insert(game_master_table).values(game_id=game_id, user_id=user_id))
        db.commit()

    def deleteGameManagerByGameIdAndUserId(self, db: DbSession, game_id: str, user_id: str) -> None:
        manager_table = self.getGameManagerTable(db)
        db.execute(
            delete(manager_table)
            .where(manager_table.c["game_id"] == game_id)
            .where(manager_table.c["user_id"] == user_id)
        )
        db.commit()

    def deleteGameMasterByGameIdAndUserId(self, db: DbSession, game_id: str, user_id: str) -> None:
        game_master_table = self.getGameMasterTable(db)
        db.execute(
            delete(game_master_table)
            .where(game_master_table.c["game_id"] == game_id)
            .where(game_master_table.c["user_id"] == user_id)
        )
        db.commit()

    def getGameById(self, db: DbSession, game_id: str) -> Optional[Dict[str, Any]]:
        table = self.getGameTable(db)
        row = db.execute(select(table).where(table.c["id"] == game_id).limit(1)).mappings().first()
        if row is None:
            return None
        return dict(row)

    def hasGameCode(self, db: DbSession, code: str) -> bool:
        table = self.getGameTable(db)
        row = db.execute(
            select(table.c["id"])
            .where(table.c["code"] == code)
            .limit(1)
        ).first()
        return row is not None

    def createGameByValues(self, db: DbSession, values: Dict[str, Any]) -> None:
        table = self.getGameTable(db)
        db.execute(table.insert().values(**values))
        db.commit()

    def createGameByValuesWithoutCommit(self, db: DbSession, values: Dict[str, Any]) -> None:
        table = self.getGameTable(db)
        db.execute(table.insert().values(**values))

    def updateGameById(self, db: DbSession, game_id: str, values: Dict[str, Any]) -> None:
        table = self.getGameTable(db)
        db.execute(table.update().where(table.c["id"] == game_id).values(**values))
        db.commit()

    def deleteGameById(self, db: DbSession, game_id: str) -> None:
        metadata = MetaData()
        metadata.reflect(bind=db.get_bind())

        team_table = metadata.tables.get("team")
        team_ids: list[str] = []
        if team_table is not None and "id" in team_table.c and "game_id" in team_table.c:
            rows = db.execute(
                select(team_table.c["id"]).where(team_table.c["game_id"] == game_id)
            ).all()
            team_ids = [str(row[0]) for row in rows]

        for table in reversed(metadata.sorted_tables):
            if table.name == "game":
                continue

            predicates = []
            if "game_id" in table.c:
                predicates.append(table.c["game_id"] == game_id)

            if team_ids:
                for fk in table.foreign_key_constraints:
                    for element in fk.elements:
                        referred_table = element.column.table.name if element.column is not None else None
                        if referred_table != "team":
                            continue
                        predicates.append(element.parent.in_(team_ids))

            if not predicates:
                continue

            db.execute(table.delete().where(or_(*predicates)))

        game_table = metadata.tables.get("game")
        if game_table is None:
            game_table = self.getGameTable(db)
        db.execute(game_table.delete().where(game_table.c["id"] == game_id))
        db.commit()

    @staticmethod
    def commitChanges(db: DbSession) -> None:
        db.commit()

    @staticmethod
    def rollbackOnError(db: DbSession, error: Exception) -> None:
        if isinstance(error, SQLAlchemyError):
            db.rollback()
