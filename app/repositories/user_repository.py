from typing import Optional

from sqlalchemy import MetaData, Table, select

from app.dependencies import DbSession


class UserRepository:
    def __init__(self) -> None:
        self._metadata = MetaData()

    def getUserTable(self, db: DbSession) -> Table:
        return Table("user", self._metadata, autoload_with=db.get_bind())

    def getUserIdByEmail(self, db: DbSession, email: str) -> Optional[str]:
        user_table = self.getUserTable(db)
        row = db.execute(select(user_table.c["id"]).where(user_table.c["email"] == email).limit(1)).first()
        if row is None:
            return None
        return str(row[0])

    def getUserDisplayNameById(self, db: DbSession, user_id: str) -> Optional[str]:
        user_table = self.getUserTable(db)
        row = (
            db.execute(
                select(user_table.c["username"], user_table.c["email"]) 
                .where(user_table.c["id"] == user_id)
                .limit(1)
            )
            .first()
        )
        if row is None:
            return None

        username = str(row[0] or "").strip()
        if username:
            return username
        email = str(row[1] or "").strip()
        if email:
            return email
        return None

    def getUserEmailById(self, db: DbSession, user_id: str) -> Optional[str]:
        user_table = self.getUserTable(db)
        row = (
            db.execute(
                select(user_table.c["email"])
                .where(user_table.c["id"] == user_id)
                .limit(1)
            )
            .first()
        )
        if row is None:
            return None

        email = str(row[0] or "").strip()
        if email:
            return email
        return None
