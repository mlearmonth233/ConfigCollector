"""Synchronous SQLAlchemy engine/session for use inside Celery workers.

The FastAPI app uses async SQLAlchemy (app/database.py) end to end, but
Celery's worker model is synchronous, so task code needs its own plain
engine pointed at the same database via a sync-capable driver.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

settings = get_settings()


def _to_sync_url(url: str) -> str:
    return url.replace("+asyncpg", "").replace("+aiosqlite", "")


_sync_url = _to_sync_url(settings.database_url)
# See app/database.py: concurrent writers wait for SQLite's lock.
sync_engine = create_engine(_sync_url, connect_args={"timeout": 30, "check_same_thread": False} if _sync_url.startswith("sqlite") else {})
SyncSessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False)
