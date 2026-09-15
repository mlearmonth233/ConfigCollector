import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_, UTCDateTime


class ScheduleFrequency(str, enum.Enum):
    ONCE = "once"  # a single run at run_once_at, then the schedule disables itself
    EVERY_N_HOURS = "every_n_hours"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class Schedule(Base_):
    """A recurring 'go collect configs' request - the same thing a user can
    trigger by hand from the Devices page (see api/jobs.py's create_job),
    just run automatically on a timer instead (see tasks.py's
    run_due_schedules, invoked periodically by Celery beat). Each due run
    creates its own ordinary CollectionJob, so it shows up in the Jobs page
    exactly like a manual one."""

    __tablename__ = "schedules"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Comma-separated device UUIDs (same format/helpers as
    # Device.custom_commands) - NULL/empty means "every device currently in
    # the org", re-resolved fresh at each run so a device added after the
    # schedule was created is automatically picked up by future runs.
    device_ids: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which timing columns below apply depends on frequency - see
    # services/scheduling.py (validate_timing / compute_next_run_at) for the
    # exact rules; the columns themselves are all nullable so a schedule of
    # any one frequency simply leaves the others empty.
    frequency: Mapped[ScheduleFrequency] = mapped_column(Enum(ScheduleFrequency), nullable=False)
    # EVERY_N_HOURS only.
    interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # DAILY / WEEKLY / MONTHLY: time of day to run, in `timezone` (below).
    run_at_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_at_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # WEEKLY only: 0 = Monday ... 6 = Sunday.
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # MONTHLY only: 1..31, clamped to the last day of shorter months.
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ONCE only: the single moment to run (stored as UTC).
    run_once_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # IANA timezone name the time-of-day fields are in (e.g.
    # "America/Chicago"); NULL means UTC. Recorded per schedule rather than
    # per org since the person setting one up wants it in *their* local
    # time, and a distributed team may not share one.
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Nullable + SET NULL: a schedule shouldn't disappear (or block deletion
    # of the job it created) just because that job was later cleared - see
    # api/jobs.py's clear_finished_jobs/delete_job.
    last_job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("collection_jobs.id", ondelete="SET NULL"), nullable=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="schedules")
