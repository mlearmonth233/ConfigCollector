import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class NetworkZone(str, enum.Enum):
    """Which network a device lives on, per the site naming convention
    (a "P0"/"O0" marker embedded in the hostname) - see
    app.services.hostname_detection."""

    IT = "it"
    OT = "ot"


class Device(Base_):
    __tablename__ = "devices"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("credentials.id"), nullable=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    # Key into DEVICE_TYPE_REGISTRY (app/services/device_types.py), e.g.
    # "cisco_ios", "cisco_wlc", "cisco_asa", "fortinet", "apc_pdu".
    device_type: Mapped[str] = mapped_column(String(64), nullable=False)
    site: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Optional override of the default "show config" command(s) for this
    # device's type, comma-separated. Needed for PDUs/console servers whose
    # CLI varies by vendor and isn't covered by a dedicated netmiko driver.
    custom_commands: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Network function/role (access switch, core switch, WLC, PDU, ...) -
    # key into DEVICE_ROLES (app/services/hostname_detection.py). Distinct
    # from device_type: role doesn't imply a vendor/Netmiko driver (a core
    # switch could be Cisco IOS or NX-OS), it's for organizing/reporting.
    # Auto-detected from the device's name when not given explicitly.
    device_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    network_zone: Mapped[NetworkZone | None] = mapped_column(Enum(NetworkZone), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="devices")
    credential: Mapped["Credential | None"] = relationship(back_populates="devices")
    # passive_deletes=True: don't lazy-load every job_item just to null out
    # its device_id when this device is deleted (that lazy-load-then-UPDATE
    # dance is also what used to fail outright back when device_id was
    # NOT NULL). devices.py's delete_device already nulls these out itself
    # via a plain UPDATE before deleting, so there's nothing left for the
    # ORM to manage here.
    job_items: Mapped[list["CollectionJobItem"]] = relationship(
        back_populates="device", passive_deletes=True
    )
