from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import MetaData, Table, insert

from app.dependencies import DbSession


class CardRepository:
    def __init__(self) -> None:
        self._metadata = MetaData()

    def getCardTable(self, db: DbSession) -> Table:
        return Table("card", self._metadata, autoload_with=db.get_bind())

    def createCardsByValuesWithoutCommit(self, db: DbSession, values_list: List[Dict[str, Any]]) -> None:
        if not values_list:
            return

        table = self.getCardTable(db)
        db.execute(insert(table), values_list)

    @staticmethod
    def buildCardValues(
        *,
        card_id: str,
        game_id: str,
        card_type: str,
        qr_token: str,
        created_at: datetime,
        title: str,
    ) -> Dict[str, Any]:
        return {
            "id": card_id,
            "game_id": game_id,
            "type": card_type,
            "title": title,
            "qr_token": qr_token,
            "created_at": created_at,
            "locked": False,
            "holder_team_id": None,
            "image_path": None,
        }
