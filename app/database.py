from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings, normalize_database_url


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for ORM model definitions."""

    pass


settings = get_settings()
engine = create_engine(normalize_database_url(settings.database_url), pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db_session() -> Generator[Session, None, None]:
    """Yield a request-scoped database session and always close afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
