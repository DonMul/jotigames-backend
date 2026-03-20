from datetime import UTC, datetime
from typing import Any, Dict, Optional

from sqlalchemy import MetaData, Table, and_, delete, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.dependencies import DbSession


class ExplodingKittensRepository:
    def __init__(self) -> None:
        self._metadata = MetaData()

    def _getTable(self, db: DbSession, table_name: str) -> Table:
        return Table(table_name, self._metadata, autoload_with=db.get_bind())

    def getCardTable(self, db: DbSession) -> Table:
        return self._getTable(db, "card")

    def getCardActionTable(self, db: DbSession) -> Table:
        return self._getTable(db, "card_action")

    def getCardUsageTable(self, db: DbSession) -> Table:
        return self._getTable(db, "card_usage")

    def getTeamTable(self, db: DbSession) -> Table:
        return self._getTable(db, "team")

    def getGameTable(self, db: DbSession) -> Table:
        return self._getTable(db, "game")

    def fetchCardsByGameId(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.getCardTable(db)
        rows = db.execute(select(table).where(table.c["game_id"] == game_id)).mappings().all()
        return [dict(row) for row in rows]

    def getCardByGameIdAndCardId(self, db: DbSession, game_id: str, card_id: str) -> Optional[Dict[str, Any]]:
        table = self.getCardTable(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["id"] == card_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def getCardByGameIdAndQrToken(self, db: DbSession, game_id: str, qr_token: str) -> Optional[Dict[str, Any]]:
        table = self.getCardTable(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["qr_token"] == qr_token)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def fetchHandCardsByTeamId(self, db: DbSession, team_id: str) -> list[Dict[str, Any]]:
        table = self.getCardTable(db)
        rows = db.execute(select(table).where(table.c["holder_team_id"] == team_id).order_by(table.c["type"].asc())).mappings().all()
        return [dict(row) for row in rows]

    def fetchFirstHandCardByTeamIdAndType(self, db: DbSession, team_id: str, card_type: str) -> Optional[Dict[str, Any]]:
        table = self.getCardTable(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["holder_team_id"] == team_id)
                .where(table.c["type"] == card_type)
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def fetchFirstAvailableCardByGameIdAndType(self, db: DbSession, game_id: str, card_type: str) -> Optional[Dict[str, Any]]:
        table = self.getCardTable(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["type"] == card_type)
                .where(table.c["holder_team_id"].is_(None))
                .where(table.c["locked"] == False)
                .order_by(table.c["created_at"].asc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def fetchAvailableCardsByGameId(self, db: DbSession, game_id: str) -> list[Dict[str, Any]]:
        table = self.getCardTable(db)
        rows = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["holder_team_id"].is_(None))
                .where(table.c["locked"] == False)
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def createCardsByValuesWithoutCommit(self, db: DbSession, cards: list[Dict[str, Any]]) -> None:
        if not cards:
            return
        table = self.getCardTable(db)
        db.execute(insert(table), cards)

    def createCardByValuesWithoutCommit(self, db: DbSession, card_values: Dict[str, Any]) -> None:
        table = self.getCardTable(db)
        db.execute(insert(table).values(**card_values))

    def updateCardByGameIdAndCardIdWithoutCommit(self, db: DbSession, game_id: str, card_id: str, values: Dict[str, Any]) -> None:
        if not values:
            return
        table = self.getCardTable(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == card_id)
            .values(**values)
        )

    def deleteCardByGameIdAndCardIdWithoutCommit(self, db: DbSession, game_id: str, card_id: str) -> None:
        table = self.getCardTable(db)
        db.execute(delete(table).where(table.c["game_id"] == game_id).where(table.c["id"] == card_id))

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

    def updateTeamByGameIdAndTeamIdWithoutCommit(self, db: DbSession, game_id: str, team_id: str, values: Dict[str, Any]) -> None:
        if not values:
            return
        table = self.getTeamTable(db)
        db.execute(
            update(table)
            .where(table.c["game_id"] == game_id)
            .where(table.c["id"] == team_id)
            .values(**values)
        )

    def createCardUsageWithoutCommit(
        self,
        db: DbSession,
        *,
        usage_id: str,
        card_id: str,
        team_id: str,
        event_type: str,
    ) -> None:
        table = self.getCardUsageTable(db)
        db.execute(
            insert(table).values(
                id=usage_id,
                card_id=card_id,
                team_id=team_id,
                event_type=event_type,
                used_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

    def wasCardUsedByTeamForEvent(self, db: DbSession, card_id: str, team_id: str, event_type: str) -> bool:
        table = self.getCardUsageTable(db)
        row = (
            db.execute(
                select(table.c["id"])
                .where(table.c["card_id"] == card_id)
                .where(table.c["team_id"] == team_id)
                .where(table.c["event_type"] == event_type)
                .limit(1)
            )
            .first()
        )
        return row is not None

    def createCardActionWithoutCommit(self, db: DbSession, values: Dict[str, Any]) -> None:
        table = self.getCardActionTable(db)
        db.execute(insert(table).values(**values))

    def getPendingActionByIdForTeam(
        self,
        db: DbSession,
        *,
        action_id: str,
        game_id: str,
        target_team_id: str,
    ) -> Optional[Dict[str, Any]]:
        table = self.getCardActionTable(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["id"] == action_id)
                .where(table.c["game_id"] == game_id)
                .where(table.c["target_team_id"] == target_team_id)
                .where(table.c["status"] == "pending")
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def getPendingActionById(
        self,
        db: DbSession,
        *,
        action_id: str,
        game_id: str,
    ) -> Optional[Dict[str, Any]]:
        table = self.getCardActionTable(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["id"] == action_id)
                .where(table.c["game_id"] == game_id)
                .where(table.c["status"] == "pending")
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def fetchPendingActionsByTeam(self, db: DbSession, *, game_id: str, target_team_id: str) -> list[Dict[str, Any]]:
        table = self.getCardActionTable(db)
        rows = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["target_team_id"] == target_team_id)
                .where(table.c["status"] == "pending")
                .order_by(table.c["created_at"].desc())
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def fetchPendingActionsByGame(self, db: DbSession, *, game_id: str) -> list[Dict[str, Any]]:
        table = self.getCardActionTable(db)
        rows = (
            db.execute(
                select(table)
                .where(table.c["game_id"] == game_id)
                .where(table.c["status"] == "pending")
                .order_by(table.c["created_at"].desc())
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def fetchOldestPendingActionOlderThan(self, db: DbSession, *, older_than: datetime) -> Optional[Dict[str, Any]]:
        table = self.getCardActionTable(db)
        row = (
            db.execute(
                select(table)
                .where(table.c["status"] == "pending")
                .where(table.c["created_at"] <= older_than)
                .order_by(table.c["created_at"].asc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return dict(row)

    def updateActionStatusWithoutCommit(self, db: DbSession, action_id: str, status_value: str) -> None:
        table = self.getCardActionTable(db)
        db.execute(
            update(table)
            .where(table.c["id"] == action_id)
            .values(status=status_value, resolved_at=datetime.now(UTC).replace(tzinfo=None))
        )

    def getGameById(self, db: DbSession, game_id: str) -> Optional[Dict[str, Any]]:
        table = self.getGameTable(db)
        row = db.execute(select(table).where(table.c["id"] == game_id).limit(1)).mappings().first()
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def commitChanges(db: DbSession) -> None:
        db.commit()

    @staticmethod
    def rollbackOnError(db: DbSession, error: Exception) -> None:
        if isinstance(error, SQLAlchemyError):
            db.rollback()
