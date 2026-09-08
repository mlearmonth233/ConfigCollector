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


sync_engine = create_engine(_to_sync_url(settings.database_url))
SyncSessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False)
