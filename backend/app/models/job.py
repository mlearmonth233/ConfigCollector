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


class CollectionJob(Base_):
    """One bulk 'go collect configs' request, fanning out to one
    CollectionJobItem per target device."""

    __tablename__ = "collection_jobs"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="jobs")
    items: Mapped[list["CollectionJobItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="CollectionJobItem.created_at"
    )


class CollectionJobItem(Base_):
    """Per-device outcome of a CollectionJob."""

    __tablename__ = "collection_job_items"

    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("collection_jobs.id"), nullable=False)
    device_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("devices.id"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # True if the primary credential's login failed and this device only
    # succeeded (or was attempted) using its credential's fallback. Worth
    # surfacing even on success - it usually means the primary AAA path
    # (e.g. TACACS+) is degraded and deserves a look.
    used_fallback_credential: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["CollectionJob"] = relationship(back_populates="items")
    device: Mapped["Device"] = relationship(back_populates="job_items")
    snapshot: Mapped["ConfigSnapshot | None"] = relationship(back_populates="job_item", uselist=False)
