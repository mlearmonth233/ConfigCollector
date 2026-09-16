import os
import sqlite3
from collections.abc import AsyncGenerator

from sqlalchemy.dialects import sqlite as sqlite_dialect
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


def _add_missing_nullable_columns(conn: sqlite3.Connection, table, existing: dict[str, int]) -> bool:
    """Brings `table` up to date in place when the only drift is new,
    *nullable* columns the model has gained (the common case for a feature
    that adds an optional field - e.g. Schedule's day_of_week/timezone) by
    ALTER TABLE ... ADD COLUMN, which SQLite supports and which preserves
    every existing row. Returns False, changing nothing, if any missing
    column is NOT NULL: SQLite can't add one of those to a table that
    already has rows without a default, so that drift still needs the
    wipe-and-recreate fallback below."""
    missing = [c for c in table.columns if c.name not in existing]
    if not missing:
        return True
    if any(not c.nullable for c in missing):
        return False
    for column in missing:
        column_type = column.type.compile(dialect=sqlite_dialect.dialect())
        conn.execute(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}')
        existing[column.name] = 0
    conn.commit()
    print(
        f"[ConfigCollector] Added new optional column(s) to local dev table '{table.name}': "
        + ", ".join(c.name for c in missing)
    )
    return True


def _table_drifted(conn: sqlite3.Connection, table) -> bool:
    """True if `table`'s actual SQLite structure no longer matches what the
    SQLAlchemy model expects in a way that can't be fixed in place - a
    NOT NULL column the model has that the table doesn't, or a column that
    exists in both but whose NOT NULL-ness disagrees (e.g. a column relaxed
    from required to optional, like CollectionJobItem.device_id becoming
    nullable so a device could be deleted without dragging its job history
    down with it - SQLite keeps enforcing the old NOT NULL forever
    otherwise, since changing the Python model doesn't retroactively ALTER
    an existing table). Missing *nullable* columns are simply added (see
    _add_missing_nullable_columns) and don't count as drift."""
    existing = {row[1]: row[3] for row in conn.execute(f"PRAGMA table_info('{table.name}')")}
    if not existing:
        return False  # table doesn't exist yet - create_all() will add it, not drift

    if not _add_missing_nullable_columns(conn, table, existing):
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
        # Every table is checked (not any()-short-circuited) so each one's
        # in-place column additions happen even if a later table drifts.
        drifted = [table.name for table in Base.metadata.tables.values() if _table_drifted(conn, table)]
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


# --- schema migrations (Alembic) ------------------------------------------------------

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The autogenerated baseline that describes every pre-Alembic database.
BASELINE_REVISION = "7be3e846af07"


def _alembic_config():
    from alembic.config import Config  # noqa: PLC0415 - alembic is only needed at start-up

    cfg = Config(os.path.join(_BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_DIR, "alembic"))
    # Our own logging is already configured; don't let alembic.ini's
    # [loggers] section replace the handlers.
    cfg.set_main_option("skip_logging_config", "1")
    return cfg


def head_revision() -> str:
    from alembic.script import ScriptDirectory  # noqa: PLC0415

    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def migrate_to_head(sync_engine) -> None:
    """Brings the database to the latest migration.

    Databases made before Alembic arrived (plain create_all) have every
    table but no alembic_version. Rather than fail, they are adopted: any
    table they lack is created (the additive part of what create_all did),
    then the database is stamped at the baseline head. From then on every
    schema change ships as a migration and is applied here on start-up.
    Postgres included - this is the upgrade path that did not exist before.
    """
    from alembic import command  # noqa: PLC0415
    from sqlalchemy import inspect  # noqa: PLC0415

    cfg = _alembic_config()
    cfg.set_main_option("sqlalchemy.url", str(sync_engine.url).replace("***", sync_engine.url.password or ""))
    existing = set(inspect(sync_engine).get_table_names())
    if "alembic_version" in existing:
        command.upgrade(cfg, "head")
        return
    if existing - {"sqlite_sequence"}:
        # Adopt: add whatever tables are missing (never drop), stamp at the
        # baseline, then let every later migration run. Migrations after
        # the baseline are written to tolerate tables/columns that
        # create_all already put in place (see alembic/versions).
        Base.metadata.create_all(sync_engine)
        command.stamp(cfg, BASELINE_REVISION)
        print("[ConfigCollector] Existing database adopted by Alembic (stamped at the baseline); applying later migrations.")
    command.upgrade(cfg, "head")


async def init_db() -> None:
    # Local SQLite keeps its convenience behaviour (in-place column adds,
    # wipe on incompatible drift), then Alembic takes over for everything.
    db_path = _sqlite_path_from_url(settings.database_url)
    if db_path is not None:
        _reset_sqlite_if_schema_drifted(db_path)

    import asyncio  # noqa: PLC0415

    from app.db_sync import sync_engine  # noqa: PLC0415 - built from the same DATABASE_URL

    await asyncio.to_thread(migrate_to_head, sync_engine)
