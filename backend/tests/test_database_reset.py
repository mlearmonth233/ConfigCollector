import sqlite3
import tempfile
from pathlib import Path

from app.database import _reset_sqlite_if_schema_drifted, _sqlite_path_from_url


def test_sqlite_path_from_url_relative():
    assert _sqlite_path_from_url("sqlite+aiosqlite:///./configcollector.db") == "./configcollector.db"


def test_sqlite_path_from_url_non_sqlite_returns_none():
    assert _sqlite_path_from_url("postgresql+asyncpg://user:pass@host/db") is None


def test_reset_leaves_up_to_date_schema_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE organizations (id CHAR(36) PRIMARY KEY NOT NULL, name VARCHAR NOT NULL, "
            "created_at DATETIME NOT NULL, snapshot_retention_days INTEGER)"
        )
        conn.commit()
        conn.close()

        _reset_sqlite_if_schema_drifted(db_path)

        assert Path(db_path).exists()
        conn = sqlite3.connect(db_path)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "organizations" in tables


def test_reset_wipes_file_when_a_known_table_is_missing_a_column():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        conn = sqlite3.connect(db_path)
        # collection_job_items, but without the live_output column the
        # current model expects.
        conn.execute(
            "CREATE TABLE collection_job_items (id CHAR(36) PRIMARY KEY, job_id CHAR(36), device_id CHAR(36), "
            "status VARCHAR, error_message TEXT, used_fallback_credential BOOLEAN, "
            "started_at DATETIME, finished_at DATETIME, created_at DATETIME)"
        )
        conn.commit()
        conn.close()

        _reset_sqlite_if_schema_drifted(db_path)

        assert not Path(db_path).exists()


def test_reset_wipes_file_when_a_column_is_notnull_but_should_now_be_nullable():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")
        conn = sqlite3.connect(db_path)
        # Every column the current model expects is present by name (so
        # this isn't just re-triggering the missing-column check above) -
        # but device_id is still declared NOT NULL, exactly as it would be
        # in a database created before device_id was relaxed to nullable
        # (so a device could be deleted without dragging its job history
        # down with it). A model-only change like that doesn't retroactively
        # ALTER an already-created SQLite table, so this must still be
        # caught as drift.
        conn.execute(
            "CREATE TABLE collection_job_items ("
            "id CHAR(36) PRIMARY KEY NOT NULL, job_id CHAR(36) NOT NULL, device_id CHAR(36) NOT NULL, "
            "status VARCHAR NOT NULL, error_message TEXT, used_fallback_credential BOOLEAN NOT NULL, "
            "live_output TEXT NOT NULL, started_at DATETIME, finished_at DATETIME, "
            "created_at DATETIME NOT NULL)"
        )
        conn.commit()
        conn.close()

        _reset_sqlite_if_schema_drifted(db_path)

        assert not Path(db_path).exists()


def test_reset_does_nothing_for_a_file_that_does_not_exist():
    # Should not raise - this is the normal first-run case.
    _reset_sqlite_if_schema_drifted("/tmp/does-not-exist-configcollector-test.db")
