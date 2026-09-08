import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class Credential(Base_):
    """A named username/password (+ optional enable secret) set, reusable
    across many devices within an org. Secrets are stored encrypted."""

    __tablename__ = "credentials"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_password: Mapped[str] = mapped_column(String(512), nullable=False)
    encrypted_enable_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="credentials")
    devices: Mapped[list["Device"]] = relationship(back_populates="credential")
