import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base_


class WirelessStandardRecord(Base_):
    """An organization's wireless standard (at most one): the settings the
    Wireless Audit checks its controllers against, as the JSON form of
    schemas/wireless_audit.py's WirelessStandard. Kept as one JSON document
    so a new check's setting needs no migration - a missing key simply
    takes the schema's default."""

    __tablename__ = "wireless_standards"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, unique=True)
    settings: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("users.id"), nullable=True)
