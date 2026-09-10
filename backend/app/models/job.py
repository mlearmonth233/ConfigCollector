import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    # Item-only: queued for its turn, connecting and logging in (SSH +
    # TACACS+/RADIUS round trip + any MFA challenge/approval). No config
    # commands are sent until this phase succeeds.
    AUTHENTICATING = "authenticating"
    # Item-only: authenticated, now sending show/config commands.
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    # A device the user cancelled - either before it got a chance to start
    # (still PENDING at cancel time, so skipped outright), or partway
    # through its command list (collector.py stops between commands once
    # it notices - see CollectionJob.cancel_requested). Authenticating
    # itself can't be interrupted this way - a live SSH/AAA call is opaque
    # until it returns - so a device still connecting when cancelled
    # finishes that step normally before the next command-loop check stops it.
    CANCELLED = "cancelled"


# An item still in one of these hasn't reached a terminal outcome yet -
# shared between tasks.py's job-finalization check and devices.py's guard
# against deleting a device mid-collection.
ACTIVE_JOB_STATUSES = (JobStatus.PENDING, JobStatus.AUTHENTICATING, JobStatus.RUNNING)


class CollectionJob(Base_):
    """One bulk 'go collect configs' request, fanning out to one
    CollectionJobItem per target device."""

    __tablename__ = "collection_jobs"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set by jobs.py's cancel_job. A device still PENDING at that moment is
    # marked CANCELLED immediately; a device already authenticating or
    # mid-command-list can't be interrupted right away (a live SSH call is
    # opaque until it returns), but collector.py checks this flag between
    # each command in that device's list and stops before running the next
    # one, so a job with several commands left to run responds to a cancel
    # within roughly one command's round trip rather than only ever
    # finishing the entire device untouched.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="jobs")
    items: Mapped[list["CollectionJobItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="CollectionJobItem.created_at"
    )


class CollectionJobItem(Base_):
    """Per-device outcome of a CollectionJob."""

    __tablename__ = "collection_job_items"

    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("collection_jobs.id"), nullable=False)
    # Nullable so a device can be deleted without dragging its job history
    # down with it - see devices.py's delete_device, which nulls this out
    # (rather than deleting the item) for exactly that reason.
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True if the primary credential's login failed and this device only
    # succeeded (or was attempted) using its credential's fallback. Worth
    # surfacing even on success - it usually means the primary AAA path
    # (e.g. TACACS+) is degraded and deserves a look.
    used_fallback_credential: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Live, human-readable transcript of the collection attempt - connecting,
    # authenticating, each command sent and its output - appended to as it
    # happens so a polling UI can show what's going on in real time instead
    # of just a static "authenticating..." status, which matters most for a
    # slow TACACS+/RADIUS round trip or a pending MFA approval.
    live_output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["CollectionJob"] = relationship(back_populates="items")
    device: Mapped["Device | None"] = relationship(back_populates="job_items")
    # cascade: deleting a job (and so its items, per CollectionJob.items'
    # own cascade above) should take each item's snapshot with it too - a
    # snapshot's only reason to exist is as that item's collected result,
    # unlike a device (which the snapshot deliberately outlives - see
    # devices.py's delete_device).
    snapshot: Mapped["ConfigSnapshot | None"] = relationship(
        back_populates="job_item", uselist=False, cascade="all, delete-orphan"
    )
