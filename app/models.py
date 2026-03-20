from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiAuthToken(Base):
    __tablename__ = "api_auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True)
    principal_type: Mapped[str] = mapped_column(String(16), index=True, default="user")
    principal_id: Mapped[str] = mapped_column(String(255), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=False), nullable=True)

    __table_args__ = (
        Index("ix_api_auth_tokens_user_expires", "user_id", "expires_at"),
        Index("ix_api_auth_tokens_principal_expires", "principal_type", "principal_id", "expires_at"),
    )
