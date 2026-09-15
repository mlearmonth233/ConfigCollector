import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_


class CustomDeviceType(Base_):
    """An org-defined device type - anything the built-in registry
    (app.services.device_types.DEVICE_TYPE_REGISTRY) doesn't cover: a
    Juniper, an Arista, a Palo Alto, a Linux syslog box, a vendor's odd
    console server. It names one of Netmiko's SSH drivers and the commands
    to run (for config, logs, whatever the user wants collected), and from
    then on behaves exactly like a built-in type: it appears in the Add
    device dropdown, Device.device_type stores its `key`, the Commands
    page can override its commands, and jobs/schedules/retries collect it.

    `key` is the stable identifier stored on devices (e.g. "juniper_srx"),
    unique per org and never allowed to equal a built-in key."""

    __tablename__ = "custom_device_types"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_custom_device_types_org_key"),)

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    # Free grouping label shown in dropdowns/the Commands page (switch,
    # router, firewall, wlc, pdu, server, other...).
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    # One of Netmiko's platform names (see services.device_types.list_netmiko_drivers).
    netmiko_driver: Mapped[str] = mapped_column(String(64), nullable=False)
    # Comma-separated, same format/helpers as Device.custom_commands.
    default_commands: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Whether this platform has an "enable" privileged mode a credential's
    # enable secret should be sent to.
    secret_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Read command output by waiting for the channel to go quiet
    # (timing-based) rather than for the prompt pattern - the safe choice
    # for a driver/platform whose prompt handling hasn't been proven here.
    timing_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization: Mapped["Organization"] = relationship(back_populates="custom_device_types")
