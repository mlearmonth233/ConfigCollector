"""Alembic wiring: a fresh database is built by migrations and matches the
models; a pre-Alembic database is adopted by stamping it at head."""

import os
import tempfile

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app import database
from app.database import Base


@pytest.fixture
def sqlite_url():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)  # a truly fresh file
    yield f"sqlite:///{path}"
    if os.path.exists(path):
        os.remove(path)


def _schema_diff(engine) -> list:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": False})
        return compare_metadata(ctx, Base.metadata)


def test_fresh_database_is_built_by_migrations_and_matches_models(sqlite_url):
    engine = create_engine(sqlite_url)
    database.migrate_to_head(engine)

    tables = set(inspect(engine).get_table_names())
    assert "alembic_version" in tables
    assert {"organizations", "devices", "collection_jobs", "ping_samples", "snmp_alerts"} <= tables
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == database.head_revision()

    # The baseline migration must describe exactly what the models describe:
    # no missing or extra tables, columns or indexes.
    structural = [d for d in _schema_diff(engine) if not (isinstance(d, tuple) and d[0].startswith("modify_"))]
    assert structural == [], structural

    # Running again is a no-op.
    database.migrate_to_head(engine)


def test_pre_alembic_database_is_stamped_not_rebuilt(sqlite_url):
    engine = create_engine(sqlite_url)
    Base.metadata.create_all(engine)  # how every database before this change was made
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO organizations (id, name, created_at) VALUES ('11111111-1111-1111-1111-111111111111', 'Legacy Org', CURRENT_TIMESTAMP)"))

    database.migrate_to_head(engine)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == database.head_revision()
        assert conn.execute(text("SELECT name FROM organizations")).scalar() == "Legacy Org"  # data untouched


def test_pre_alembic_database_missing_a_table_gets_it_added(sqlite_url):
    """An older create_all database that predates a table (here: the ping
    monitor's) still gets that table, exactly as create_all used to add it."""
    engine = create_engine(sqlite_url)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE ping_samples"))
    assert "ping_samples" not in inspect(engine).get_table_names()

    database.migrate_to_head(engine)
    assert "ping_samples" in inspect(engine).get_table_names()
