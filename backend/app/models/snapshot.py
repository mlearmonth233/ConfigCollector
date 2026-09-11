import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class ConfigSnapshot(Base_):
    """The actual collected configuration text for one device at one point
    in time."""

    __tablename__ = "config_snapshots"

    # Nullable for the same reason as CollectionJobItem.device_id - see
    # devices.py's delete_device, which nulls this out rather than deleting
    # the snapshot when its device is deleted.
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_item_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("collection_job_items.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped["Device | None"] = relationship()
    job_item: Mapped["CollectionJobItem"] = relationship(back_populates="snapshot")
