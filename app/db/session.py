"""
app/db/session.py
SQLAlchemy engine + session factory.
"""
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

settings = get_settings()

# SQLite in-memory (used in tests) needs special treatment:
#   1. No connection-pool parameters (pool_size / max_overflow) — SQLite ignores pools.
#   2. StaticPool — forces all ORM sessions to share one connection, so tables
#      created during app startup (lifespan) are visible to route handlers.
#   3. check_same_thread=False — required for SQLite + multi-threaded test clients.
# PostgreSQL gets the normal production pool configuration.
_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    engine = create_engine(
        settings.database_url,
        echo=settings.debug,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    engine = create_engine(
        settings.database_url,
        echo=settings.debug,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """Yield a database session and close it on exit."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_all_tables() -> None:
    """Create all tables (dev / migration helper)."""
    from app.db.models import Base  # noqa: F401

    Base.metadata.create_all(bind=engine)
