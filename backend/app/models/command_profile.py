import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base_


class CommandProfile(Base_):
    """An org's saved override of the command(s) to run for one device
    type, persisting the choice made on the Commands page across
    collection runs. Absent a row here, a device type just uses its
    DEVICE_TYPE_REGISTRY default (see app.services.device_types) - this
    table only holds the device types an org has actually customized.
    """

    __tablename__ = "command_profiles"
    __table_args__ = (UniqueConstraint("org_id", "device_type", name="uq_command_profiles_org_device_type"),)

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False)
    # Key into DEVICE_TYPE_REGISTRY, e.g. "cisco_ios".
    device_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # Comma-separated, same format/helpers as Device.custom_commands.
    commands: Mapped[str] = mapped_column(Text, nullable=False)
