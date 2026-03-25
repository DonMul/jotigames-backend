from datetime import UTC, datetime
from typing import Any, Dict, Optional

from sqlalchemy import MetaData, Table, insert, select
from sqlalchemy.exc import SQLAlchemyError

from app.dependencies import DbSession


class GameChatRepository:
    def __init__(self) -> None:
        """Initialize metadata for reflected chat/message tables."""
        self._metadata = MetaData()

    def _getTable(self, db: DbSession, table_name: str) -> Table:
        """Return reflected table object by name for current DB bind."""
        return Table(table_name, self._metadata, autoload_with=db.get_bind())

    def getGameChatMessageTable(self, db: DbSession) -> Table:
        """Return reflected `game_chat_message` table."""
        return self._getTable(db, "game_chat_message")

    def getTeamMessageTable(self, db: DbSession) -> Table:
        """Return reflected `team_message` table."""
        return self._getTable(db, "team_message")

    def createGameChatMessageWithoutCommit(self, db: DbSession, values: Dict[str, Any]) -> None:
        """Insert game chat message row without committing transaction."""
        table = self.getGameChatMessageTable(db)
        db.execute(insert(table).values(**values))

    def fetchGameChatMessagesByGameId(self, db: DbSession, game_id: str, *, limit: int) -> list[Dict[str, Any]]:
        """Fetch most recent game chat messages in chronological order.

        Records are queried descending for efficiency and reversed before
        returning so clients receive oldest-to-newest message order.
        """
        table = self.getGameChatMessageTable(db)
        rows = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .order_by(table.c["created_at"].desc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        items = [dict(row) for row in rows]
        items.reverse()
        return items

    def createTeamMessageWithoutCommit(
        self,
        db: DbSession,
        *,
        message_id: str,
        game_id: str,
        team_id: str,
        created_by_id: Optional[str],
        message: str,
    ) -> None:
        """Insert admin-to-team message row without committing transaction."""
        table = self.getTeamMessageTable(db)
        db.execute(
            insert(table).values(
                id=message_id,
                game_id=game_id,
                team_id=team_id,
                created_by_id=created_by_id,
                message=message,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

    @staticmethod
    def commitChanges(db: DbSession) -> None:
        """Commit current transaction."""
        db.commit()

    @staticmethod
    def rollbackOnError(db: DbSession, error: Exception) -> None:
        """Rollback only on SQLAlchemy-level errors."""
        if isinstance(error, SQLAlchemyError):
            db.rollback()
