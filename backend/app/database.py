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


def _table_drifted(conn: sqlite3.Connection, table) -> bool:
    """True if `table`'s actual SQLite structure no longer matches what the
    SQLAlchemy model expects - either a column the model has that the table
    doesn't (the original drift check), or a column that exists in both but
    whose NOT NULL-ness disagrees (e.g. a column relaxed from required to
    optional, like CollectionJobItem.device_id becoming nullable so a
    device could be deleted without dragging its job history down with it -
    SQLite keeps enforcing the old NOT NULL forever otherwise, since
    changing the Python model doesn't retroactively ALTER an existing
    table)."""
    existing = {row[1]: row[3] for row in conn.execute(f"PRAGMA table_info('{table.name}')")}
    if not existing:
        return False  # table doesn't exist yet - create_all() will add it, not drift

    expected_columns = {c.name for c in table.columns}
    if not expected_columns.issubset(existing.keys()):
        return True

    for column in table.columns:
        expected_notnull = 0 if column.nullable else 1
        if existing[column.name] != expected_notnull:
            return True
    return False


def _reset_sqlite_if_schema_drifted(db_path: str) -> None:
    """There's no Alembic yet (see README), so a local dev SQLite file just
    keeps whatever schema it had when it was first created - a model gaining
    a new column (or relaxing an existing one to nullable) doesn't
    retroactively apply to it, and the app then crashes the moment that gap
    is touched. Since local dev data is disposable, detect that drift up
    front and wipe the file instead, rather than making the developer
    remember to delete it by hand after every pull. Never applies to
    Postgres - that needs a real migration tool.
    """
    if db_path == ":memory:" or not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    try:
        drifted = any(_table_drifted(conn, table) for table in Base.metadata.tables.values())
    finally:
        conn.close()

    if drifted:
        print(
            f"[ConfigCollector] Local dev database schema is out of date (a model has columns "
            f"the SQLite file doesn't, or a column's required/optional-ness changed) - resetting "
            f"{db_path}. This is expected after pulling code changes; local dev data doesn't "
            f"persist across schema changes yet."
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
