import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class Device(Base_):
    __tablename__ = "devices"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("credentials.id"), nullable=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    # Key into DEVICE_TYPE_REGISTRY (app/services/device_types.py), e.g.
    # "cisco_ios", "cisco_wlc", "cisco_asa", "paloalto_panos", "pdu_generic".
    device_type: Mapped[str] = mapped_column(String(64), nullable=False)
    site: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Optional override of the default "show config" command(s) for this
    # device's type, comma-separated. Needed for PDUs/console servers whose
    # CLI varies by vendor and isn't covered by a dedicated netmiko driver.
    custom_commands: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="devices")
    credential: Mapped["Credential | None"] = relationship(back_populates="devices")
    job_items: Mapped[list["CollectionJobItem"]] = relationship(back_populates="device")
