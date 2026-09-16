import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class Device(Base_):
    __tablename__ = "devices"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("credentials.id"), nullable=True)
    # SNMP profile for this device; NULL = the org's default SnmpProfile.
    snmp_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("snmp_profiles.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, default=22, nullable=False)
    # Key into DEVICE_TYPE_REGISTRY (app/services/device_types.py), e.g.
    # "cisco_ios", "cisco_wlc", "fortinet", "apc_pdu".
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

    # Out-of-band console path (optional): how to reach this device's serial
    # console when its management address is unreachable. Typically a
    # console server (Opengear, Lantronix, Avocent, Digi, a Cisco async
    # card) exposing one SSH or telnet port per serial line. The Terminal
    # page offers "Connect via console" when console_host is set.
    console_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    console_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    console_protocol: Mapped[str | None] = mapped_column(String(8), nullable=True)  # "ssh" | "telnet"
    # Login for the console server itself; NULL = the device's own /
    # org-default credential. Ignored for telnet (reverse telnet has no
    # login of its own - the device's console prompt appears directly).
    console_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    # Typed into the console server's shell once logged in, e.g. Cisco
    # "connect line 3" or Opengear "pmshell -l port03". Empty = the port
    # itself is the serial line (Opengear/Lantronix per-port SSH, telnet).
    console_connect_command: Mapped[str | None] = mapped_column(String(255), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="devices")
    credential: Mapped["Credential | None"] = relationship(back_populates="devices", foreign_keys=[credential_id])
    console_credential: Mapped["Credential | None"] = relationship(foreign_keys=[console_credential_id])
    # passive_deletes=True: don't lazy-load every job_item just to null out
    # its device_id when this device is deleted (that lazy-load-then-UPDATE
    # dance is also what used to fail outright back when device_id was
    # NOT NULL). devices.py's delete_device already nulls these out itself
    # via a plain UPDATE before deleting, so there's nothing left for the
    # ORM to manage here.
    job_items: Mapped[list["CollectionJobItem"]] = relationship(
        back_populates="device", passive_deletes=True
    )
