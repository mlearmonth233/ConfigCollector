import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_
from app.models.device import NetworkZone


class HostnameRule(Base_):
    """One line of an org's device-naming convention: "a name matching
    `pattern` (per `match_mode`) is this role / this zone / this device
    type". See services/hostname_detection.py for how rules are applied.
    An org with no rows uses the built-in rules; saving any rows replaces
    that entirely (the API's reset endpoint copies the built-ins in as a
    starting point)."""

    __tablename__ = "hostname_rules"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pattern: Mapped[str] = mapped_column(String(255), nullable=False)
    match_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="contains")
    # Role key (lowercase slug). Built-in keys get their standard label;
    # any other key is labelled by role_label.
    device_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    network_zone: Mapped[NetworkZone | None] = mapped_column(Enum(NetworkZone), nullable=True)
    # Suggested device type (built-in or one of the org's custom types).
    device_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="hostname_rules")
