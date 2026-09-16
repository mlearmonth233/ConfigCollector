import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_, UTCDateTime


class SnmpMonitorConfig(Base_):
    """An org's SNMP monitoring + alerting settings - one row per org
    (created on first save). The monitor re-polls the chosen devices every
    `interval_minutes` (see tasks.run_snmp_monitors, driven by Celery beat),
    compares each device against its previous SnmpMonitorState, records an
    SnmpAlert for every enabled event it finds, and emails the batch to
    `recipients` via the SMTP settings here. The SMTP password is Fernet-
    encrypted at rest and never returned by the API."""

    __tablename__ = "snmp_monitor_configs"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    # Comma-separated device UUIDs; NULL = every device in the org (re-read
    # each run, so new devices are picked up automatically).
    device_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Profile to use for every monitored device; NULL = each device's own /
    # the org default, same precedence as an SNMP poll job.
    snmp_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("snmp_profiles.id", ondelete="SET NULL"), nullable=True
    )

    # Which events raise an alert.
    alert_link_down: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_link_up: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    alert_ap_down: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_ap_up: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    alert_device_down: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_device_up: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Alert on new syslog-history entries at this Cisco severity level or
    # worse (0 emergencies ... 7 debugging); NULL = don't watch the syslog.
    alert_syslog_max_level: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Delivery (recipients, SMTP, webhooks) lives in AlertSettings
    # (models/alerting.py), shared by every monitor.

    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Outcome of the last cycle, for the UI: e.g. "polled 12 devices, 2
    # alerts, email sent" or the SMTP error.
    last_result: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization: Mapped["Organization"] = relationship(back_populates="snmp_monitor_config")


class SnmpMonitorState(Base_):
    """What the monitor last saw on one device - interface oper statuses,
    the AP table, reachability, the newest syslog index - as JSON, so the
    next cycle can tell what changed. Replaced wholesale each cycle."""

    __tablename__ = "snmp_monitor_states"
    __table_args__ = (UniqueConstraint("org_id", "device_id", name="uq_snmp_monitor_states_device"),)

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class SnmpAlertKind(str, enum.Enum):
    LINK_DOWN = "link_down"
    LINK_UP = "link_up"
    AP_DOWN = "ap_down"
    AP_UP = "ap_up"
    DEVICE_DOWN = "device_down"
    DEVICE_UP = "device_up"
    SYSLOG = "syslog"
    # Raised by the continuous ping monitor (services/ping_monitor.py), not SNMP.
    PING_DOWN = "ping_down"
    PING_UP = "ping_up"
    # A collection found the running config differs from the previous snapshot.
    CONFIG_CHANGED = "config_changed"


class SnmpAlert(Base_):
    """One detected event. `emailed` records whether the cycle that raised
    it managed to send the notification email (and `email_error` why not)."""

    __tablename__ = "snmp_alerts"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[SnmpAlertKind] = mapped_column(Enum(SnmpAlertKind), nullable=False)
    # The thing that changed: an interface name, an AP name, a syslog line.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    emailed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which channels delivered it ("email, teams"), and any webhook failure.
    notified_via: Mapped[str | None] = mapped_column(String(255), nullable=True)
    webhook_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For config-change alerts: the collection job that found the change,
    # so a job's alerts go out as one message when it finishes.
    job_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=False)

    organization: Mapped["Organization"] = relationship(back_populates="snmp_alerts")
