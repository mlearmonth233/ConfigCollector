import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class ScheduleFrequency(str, enum.Enum):
    EVERY_N_HOURS = "every_n_hours"
    DAILY = "daily"


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

    frequency: Mapped[ScheduleFrequency] = mapped_column(Enum(ScheduleFrequency), nullable=False)
    # Set (only) when frequency == EVERY_N_HOURS.
    interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set (only) when frequency == DAILY - time of day to run, in UTC.
    run_at_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_at_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)

    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Nullable + SET NULL: a schedule shouldn't disappear (or block deletion
    # of the job it created) just because that job was later cleared - see
    # api/jobs.py's clear_finished_jobs/delete_job.
    last_job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("collection_jobs.id", ondelete="SET NULL"), nullable=True
    )

    organization: Mapped["Organization"] = relationship(back_populates="schedules")
