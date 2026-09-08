import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class ConfigSnapshot(Base_):
    """The actual collected configuration text for one device at one point
    in time."""

    __tablename__ = "config_snapshots"

    device_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("devices.id"), nullable=False)
    job_item_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("collection_job_items.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped["Device"] = relationship()
    job_item: Mapped["CollectionJobItem"] = relationship(back_populates="snapshot")
