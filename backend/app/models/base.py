import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import CHAR, TypeDecorator

from app.database import Base


class UTCDateTime(TypeDecorator):
    """A timestamp that always comes back from the database tz-aware, in
    UTC - whatever the backend. Postgres's timestamptz already does this;
    SQLite (local dev/tests) has no timezone concept at all and hands the
    stored value back *naive*, which then serializes to JSON with no
    offset ("2026-09-18T07:30:00") and gets parsed by every browser as
    *local* time - so a schedule due at 07:30 UTC showed as 7:30am to a
    Chicago user (really 2:30am), and every job/snapshot timestamp was
    off by the user's UTC offset. Every timestamp the app stores is UTC by
    construction (datetime.now(timezone.utc) / func.now()), so attaching
    UTC on the way out is a statement of fact, not a conversion."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            # Normalize to UTC so a value handed in with some other offset
            # is stored (and later re-read) as the same instant.
            return value.astimezone(timezone.utc)
        return value  # naive: by app convention already UTC

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class GUID(TypeDecorator):
    """Platform-independent UUID stored as a 36-char string.

    Postgres has a native UUID type, but sqlite (used for local/dev/testing)
    does not, so we store the string form everywhere for portability.
    """

    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return uuid.UUID(value)


class Base_(Base):
    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    # Bumped by SQLAlchemy on every UPDATE of the row. For job items this is
    # the "last sign of life" (each live_output append or status change
    # refreshes it) that services/job_reaper.py uses to tell a long but
    # healthy collection from one whose worker died mid-device.
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, onupdate=func.now())
