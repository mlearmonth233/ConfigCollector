import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class FirmwareImage(Base_):
    """One uploaded firmware file, scoped to an org. The file itself lives on
    disk under Settings.firmware_storage_path/<org_id>/<id>_<filename> (see
    services/firmware_storage.py) - this row is just its metadata. Never
    served to a device directly from this row: a push job mints a
    short-lived, single-use SCP grant instead (see services/scp_server.py),
    so the storage_path itself never needs to be exposed outside the backend.
    """

    __tablename__ = "firmware_images"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Free-text label, e.g. "cat9k_iosxe.17.09.05.SPA.bin for the 9300 stack" -
    # entirely for the user's own organization; never parsed by the app.
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # MD5 of the file content - shown to the user and compared against a
    # device's own "verify /md5 flash:..." output after a push completes.
    # MD5 specifically (not SHA-256) because that's what Cisco IOS/IOS-XE's
    # own "verify" command computes.
    md5: Mapped[str] = mapped_column(String(32), nullable=False)
    # Path on disk, relative to Settings.firmware_storage_path.
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="firmware_images")
    jobs: Mapped[list["FirmwareJob"]] = relationship(back_populates="firmware_image")


class FirmwareJobStatus(str, enum.Enum):
    PENDING = "pending"
    # Job-only: the job as a whole is still in progress - set the instant
    # it's created/dispatched, same role CollectionJob.status's RUNNING
    # plays there. Never used on an individual item, which instead reports
    # exactly which of the more granular phases below it's in.
    RUNNING = "running"
    # Item-only: connecting and logging into the device (same meaning as
    # CollectionJobItem's AUTHENTICATING) - no copy command is sent yet.
    AUTHENTICATING = "authenticating"
    # Item-only: authenticated, "copy scp://..." is in flight against the
    # ephemeral grant minted for this device.
    TRANSFERRING = "transferring"
    # Item-only: transfer finished, running "verify /md5" (only reached when
    # FirmwareJob.verify_checksum is set).
    VERIFYING = "verifying"
    # Item-only: transfer (and verification, if requested) succeeded and a
    # reload was requested for this job - the device is being reloaded.
    RELOADING = "reloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# An item still in one of these hasn't reached a terminal outcome yet - same
# purpose as job.py's ACTIVE_JOB_STATUSES (devices.py's delete guard, and
# tasks.py's job-finalization check).
ACTIVE_FIRMWARE_JOB_STATUSES = (
    FirmwareJobStatus.PENDING,
    FirmwareJobStatus.RUNNING,
    FirmwareJobStatus.AUTHENTICATING,
    FirmwareJobStatus.TRANSFERRING,
    FirmwareJobStatus.VERIFYING,
    FirmwareJobStatus.RELOADING,
)


class FirmwareJob(Base_):
    """One bulk 'push this firmware to these devices' request, fanning out to
    one FirmwareJobItem per target device. Unlike CollectionJob, devices are
    processed strictly one at a time, each fully finishing (transfer, any
    verification, any reload) before the next starts - the slow part here is
    the firmware transfer itself, not authentication, so there's no benefit
    to the collection job's authenticate/run pipelining, and running several
    large firmware transfers over the same link at once is worth avoiding by
    default."""

    __tablename__ = "firmware_jobs"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), nullable=False)
    # Nullable + SET NULL so deleting a firmware image doesn't take a job's
    # history down with it - same reasoning as Device/CollectionJobItem.
    firmware_image_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("firmware_images.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Denormalized from the image at job-creation time so this job's history
    # (and each item's checksum verification), and any job still in flight,
    # doesn't depend on the image row (or its file on disk) still existing -
    # api/firmware.py additionally refuses to delete an image an active job
    # still references, as a belt-and-suspenders guard against exactly that.
    firmware_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    firmware_md5: Mapped[str] = mapped_column(String(32), nullable=False)
    firmware_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    firmware_storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    # Filename to write into each device's flash: - defaults to
    # firmware_filename but can be overridden per job (e.g. to shorten a
    # long image name for an older bootloader's filesystem).
    target_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    verify_checksum: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reload_after: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    status: Mapped[FirmwareJobStatus] = mapped_column(
        Enum(FirmwareJobStatus), default=FirmwareJobStatus.PENDING, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Same semantics as CollectionJob.cancel_requested - a device already
    # mid-transfer finishes that step (a live SCP transfer is opaque until it
    # returns) before the queue notices and stops.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="firmware_jobs")
    firmware_image: Mapped["FirmwareImage | None"] = relationship(back_populates="jobs")
    items: Mapped[list["FirmwareJobItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="FirmwareJobItem.created_at"
    )


class FirmwareJobItem(Base_):
    """Per-device outcome of a FirmwareJob."""

    __tablename__ = "firmware_job_items"

    job_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("firmware_jobs.id"), nullable=False, index=True)
    # Nullable so a device can be deleted without dragging its firmware push
    # history down with it - see devices.py's delete_device.
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[FirmwareJobStatus] = mapped_column(
        Enum(FirmwareJobStatus), default=FirmwareJobStatus.PENDING, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    used_fallback_credential: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # None until verification actually runs (FirmwareJob.verify_checksum);
    # True/False after "verify /md5" completes and is compared.
    checksum_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Live, human-readable transcript - same purpose as
    # CollectionJobItem.live_output.
    live_output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["FirmwareJob"] = relationship(back_populates="items")
    device: Mapped["Device | None"] = relationship()
