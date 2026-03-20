import random
from typing import Any, Dict, Optional

from sqlalchemy import MetaData, Table, select

from app.dependencies import DbSession


class TeamRepository:
    def __init__(self) -> None:
        self._metadata = MetaData()

    def getTeamTable(self, db: DbSession) -> Table:
        return Table("team", self._metadata, autoload_with=db.get_bind())

    def fetchTeamsByGameId(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.getTeamTable(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def getTeamByGameIdAndTeamId(self, db: DbSession, game_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        table = self.getTeamTable(db)
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

    def getTeamById(self, db: DbSession, team_id: str) -> Optional[Dict[str, Any]]:
        table = self.getTeamTable(db)
        row = db.execute(select(table).where(table.c["id"] == team_id).limit(1)).mappings().first()
        if row is None:
            return None
        return dict(row)

    def hasTeamCodeByGameId(self, db: DbSession, game_id: str, code: str) -> bool:
        table = self.getTeamTable(db)
        row = db.execute(
            select(table.c["id"]).where(table.c["game_id"] == game_id).where(table.c["code"] == code).limit(1)
        ).first()
        return row is not None

    def createTeamByValues(self, db: DbSession, values: Dict[str, Any]) -> None:
        table = self.getTeamTable(db)
        db.execute(table.insert().values(**values))
        db.commit()

    def updateTeamByGameIdAndTeamId(self, db: DbSession, game_id: str, team_id: str, values: Dict[str, Any]) -> None:
        table = self.getTeamTable(db)
        db.execute(
            table.update().where(table.c["game_id"] == game_id).where(table.c["id"] == team_id).values(**values)
        )
        db.commit()

    def deleteTeamByGameIdAndTeamId(self, db: DbSession, game_id: str, team_id: str) -> None:
        table = self.getTeamTable(db)
        db.execute(table.delete().where(table.c["game_id"] == game_id).where(table.c["id"] == team_id))
        db.commit()

    def generateUniqueTeamCodeByGameId(self, db: DbSession, game_id: str) -> str:
        for _ in range(20):
            candidate = f"{random.randint(0, 999999):06d}"
            if not self.hasTeamCodeByGameId(db, game_id, candidate):
                return candidate
        raise ValueError("team.create.codeGenerationFailed")
