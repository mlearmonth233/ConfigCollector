import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base_, UTCDateTime


class Licence(Base_):
    """The licence key an organization has applied (at most one). The key
    itself is stored; the tier it grants is derived from it on every
    request (core/licence.py), so an expiry takes effect without a
    restart and nothing here can be edited into a better tier."""

    __tablename__ = "licences"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, unique=True)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    applied_by_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
