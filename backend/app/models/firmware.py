import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_
from app.models.job import JobStatus


class TransferProtocol(str, enum.Enum):
    TFTP = "tftp"
    FTP = "ftp"
    SCP = "scp"


class FirmwareImage(Base_):
    """An uploaded firmware/OS image file, available to push out to devices
    during a push job. The actual bytes live on local disk under
    settings.firmware_storage_dir - only metadata is in the DB, since these
    files can run into the hundreds of MB."""

    __tablename__ = "firmware_images"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    # Sanitized, collision-proofed filename as actually stored on disk -
    # never trusted to be the same as original_filename (which is whatever
    # the uploader's browser sent, unsanitized, kept only for display/
    # re-download).
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Free-text label the uploader can attach (e.g. a version string) -
    # purely for their own organization, never parsed or relied on.
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="firmware_images")


class FirmwareUpgradeJob(Base_):
    """One 'push this firmware image file to these devices' request - a copy
    onto each device's storage, never an install/reload - fanning out to one
    FirmwareUpgradeJobItem per target device; mirrors CollectionJob/
    CollectionJobItem's shape and status lifecycle. (The class/table names
    keep their original "upgrade" wording purely so existing databases
    need no migration - the job itself only ever pushes the file.)"""

    __tablename__ = "firmware_upgrade_jobs"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    # Nullable + SET NULL: deleting the image an old job used shouldn't
    # delete (or block deleting) the job's own history - same reasoning as
    # CollectionJobItem.device_id below.
    firmware_image_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("firmware_images.id", ondelete="SET NULL"), nullable=True
    )
    protocol: Mapped[TransferProtocol] = mapped_column(Enum(TransferProtocol), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="firmware_jobs")
    firmware_image: Mapped["FirmwareImage | None"] = relationship()
    items: Mapped[list["FirmwareUpgradeJobItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="FirmwareUpgradeJobItem.created_at"
    )


class FirmwareUpgradeJobItem(Base_):
    """Per-device outcome of a FirmwareUpgradeJob."""

    __tablename__ = "firmware_upgrade_job_items"

    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("firmware_upgrade_jobs.id"), nullable=False, index=True
    )
    # Nullable so a device can be deleted without dragging its push job
    # history down with it - same reasoning as CollectionJobItem.device_id.
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Same live, human-readable transcript pattern as CollectionJobItem -
    # matters even more here, since a multi-minute image copy onto flash is
    # exactly the kind of thing you don't want to watch blind.
    live_output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["FirmwareUpgradeJob"] = relationship(back_populates="items")
    device: Mapped["Device | None"] = relationship()
