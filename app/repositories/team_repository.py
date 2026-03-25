import random
from typing import Any, Dict, Optional

from sqlalchemy import MetaData, Table, select

from app.dependencies import DbSession


class TeamRepository:
    def __init__(self) -> None:
        """Initialize metadata container for reflected team table access."""
        self._metadata = MetaData()

    def getTeamTable(self, db: DbSession) -> Table:
        """Return reflected `team` table bound to active session."""
        return Table("team", self._metadata, autoload_with=db.get_bind())

    def fetchTeamsByGameId(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        """Fetch all teams belonging to a game as plain dictionaries."""
        table = self.getTeamTable(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def getTeamByGameIdAndTeamId(self, db: DbSession, game_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        """Fetch team by scoped `(game_id, team_id)` pair."""
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
        """Fetch team by id regardless of game scope."""
        table = self.getTeamTable(db)
        row = db.execute(select(table).where(table.c["id"] == team_id).limit(1)).mappings().first()
        if row is None:
            return None
        return dict(row)

    def hasTeamCodeByGameId(self, db: DbSession, game_id: str, code: str) -> bool:
        """Return whether team code already exists within a game."""
        table = self.getTeamTable(db)
        row = db.execute(
            select(table.c["id"]).where(table.c["game_id"] == game_id).where(table.c["code"] == code).limit(1)
        ).first()
        return row is not None

    def createTeamByValues(self, db: DbSession, values: Dict[str, Any]) -> None:
        """Insert a team row and commit transaction."""
        table = self.getTeamTable(db)
        db.execute(table.insert().values(**values))
        db.commit()

    def updateTeamByGameIdAndTeamId(self, db: DbSession, game_id: str, team_id: str, values: Dict[str, Any]) -> None:
        """Update team fields for scoped `(game_id, team_id)` and commit."""
        table = self.getTeamTable(db)
        db.execute(
            table.update().where(table.c["game_id"] == game_id).where(table.c["id"] == team_id).values(**values)
        )
        db.commit()

    def deleteTeamByGameIdAndTeamId(self, db: DbSession, game_id: str, team_id: str) -> None:
        """Delete team row for scoped `(game_id, team_id)` and commit."""
        table = self.getTeamTable(db)
        db.execute(table.delete().where(table.c["game_id"] == game_id).where(table.c["id"] == team_id))
        db.commit()

    def generateUniqueTeamCodeByGameId(self, db: DbSession, game_id: str) -> str:
        """Generate a unique six-digit team code with bounded retry attempts."""
        for _ in range(20):
            candidate = f"{random.randint(0, 999999):06d}"
            if not self.hasTeamCodeByGameId(db, game_id, candidate):
                return candidate
        raise ValueError("team.create.codeGenerationFailed")
