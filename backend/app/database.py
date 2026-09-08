import os
import sqlite3
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_async_engine(settings.database_url, connect_args=connect_args)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


def _sqlite_path_from_url(url: str) -> str | None:
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return url[len(prefix):]
    return None


def _reset_sqlite_if_schema_drifted(db_path: str) -> None:
    """There's no Alembic yet (see README), so a local dev SQLite file just
    keeps whatever schema it had when it was first created - a model gaining
    a new column doesn't retroactively add it, and the app then crashes with
    "no such column" the moment that column is touched. Since local dev data
    is disposable, detect that drift up front and wipe the file instead,
    rather than making the developer remember to delete it by hand after
    every pull. Never applies to Postgres - that needs a real migration tool.
    """
    if db_path == ":memory:" or not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    try:
        drifted = False
        for table in Base.metadata.tables.values():
            existing_columns = {row[1] for row in conn.execute(f"PRAGMA table_info('{table.name}')")}
            if not existing_columns:
                continue  # table doesn't exist yet - create_all() will add it, not drift
            expected_columns = {c.name for c in table.columns}
            if not expected_columns.issubset(existing_columns):
                drifted = True
                break
    finally:
        conn.close()

    if drifted:
        print(
            f"[ConfigCollector] Local dev database schema is out of date (a model has columns "
            f"the SQLite file doesn't) - resetting {db_path}. This is expected after pulling code "
            f"changes; local dev data doesn't persist across schema changes yet."
        )
        os.remove(db_path)


async def init_db() -> None:
    # MVP schema bootstrap - no Alembic yet. See _reset_sqlite_if_schema_drifted
    # for how a stale local dev SQLite schema is handled.
    db_path = _sqlite_path_from_url(settings.database_url)
    if db_path is not None:
        _reset_sqlite_if_schema_drifted(db_path)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
