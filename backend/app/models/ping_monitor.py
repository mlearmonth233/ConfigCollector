import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import GUID, Base_, UTCDateTime


class PingMonitorConfig(Base_):
    """An org's continuous reachability monitor - one row per org, created
    on first use. Celery beat runs a cycle every `interval_seconds` (see
    tasks.run_ping_monitors): every device is pinged, the result is appended
    to its PingSample history and its PingStatus is updated. A device is
    only declared down after `failure_threshold` consecutive misses, so one
    dropped packet doesn't page anyone.

    Down/up emails reuse the recipients and SMTP server from the org's
    SnmpMonitorConfig ("Alerts by email" on the SNMP page) - one place to
    configure where alerts go."""

    __tablename__ = "ping_monitor_configs"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    failure_threshold: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=1500, nullable=False)
    alert_on_down: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    alert_on_up: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # How long per-check history is kept for uptime figures and sparklines.
    history_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)

    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_result: Mapped[str | None] = mapped_column(Text, nullable=True)


class PingStatus(Base_):
    """Current reachability of one device: 'up', 'down' or 'unknown' (not
    enough checks yet). `consecutive_failures` drives the threshold;
    `last_change_at` is when the state last flipped, so the UI can say
    "down for 12 minutes"."""

    __tablename__ = "ping_statuses"

    org_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("organizations.id"), nullable=False, index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_change_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    device: Mapped["Device"] = relationship()


class PingSample(Base_):
    """One ping result. Kept for PingMonitorConfig.history_days, then
    purged by the monitor itself."""

    __tablename__ = "ping_samples"
    __table_args__ = (Index("ix_ping_samples_device_time", "device_id", "checked_at"),)

    device_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
