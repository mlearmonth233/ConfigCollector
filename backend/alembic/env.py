"""Alembic environment: migrates the database the app is configured for.

Runs against a synchronous engine built from DATABASE_URL (asyncpg and
aiosqlite driver suffixes stripped, exactly as app/db_sync.py does), with
the app's own metadata as the autogenerate target. SQLite gets batch mode
so ALTER TABLE operations that SQLite cannot do natively (dropping or
altering a column) are rewritten as copy-and-rename.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401 - registers every model on Base.metadata
from app.config import get_settings
from app.database import Base
from app.db_sync import _to_sync_url

config = context.config
if config.config_file_name is not None and not config.get_main_option("skip_logging_config"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    override = config.get_main_option("sqlalchemy.url")
    return override or _to_sync_url(get_settings().database_url)


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _database_url()
    connectable = engine_from_config({"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
