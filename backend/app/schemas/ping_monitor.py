from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PingMonitorSettingsUpdate(BaseModel):
    enabled: bool = True
    interval_seconds: int = Field(default=60, ge=15, le=3600)
    failure_threshold: int = Field(default=3, ge=1, le=10)
    timeout_ms: int = Field(default=1500, ge=200, le=5000)
    alert_on_down: bool = True
    alert_on_up: bool = True
    history_days: int = Field(default=7, ge=1, le=90)


class PingMonitorSettingsOut(PingMonitorSettingsUpdate):
    last_run_at: datetime | None
    next_run_at: datetime | None
    last_result: str | None
    # True when the recipients + SMTP server on SNMP > Alerts are set, so
    # down/up alerts will actually be emailed.
    email_configured: bool


class PingSampleOut(BaseModel):
    t: datetime
    ok: bool
    rtt_ms: float | None


class PingDeviceOut(BaseModel):
    device_id: UUID
    name: str
    host: str
    site: str | None
    device_type: str
    state: str  # up | down | unknown
    consecutive_failures: int
    last_checked_at: datetime | None
    last_change_at: datetime | None
    last_rtt_ms: float | None
    uptime_24h_pct: float | None  # None until there is at least one sample
    checks_24h: int
    recent: list[PingSampleOut]  # oldest first, capped for a sparkline


class PingSummaryOut(BaseModel):
    total: int
    up: int
    down: int
    unknown: int
    avg_rtt_ms: float | None
    # Seconds since the last completed cycle; None if never run.
    seconds_since_last_run: float | None
    # True when checks are overdue by more than a few intervals - the
    # scheduler (beat) is probably not running.
    stale: bool


class PingOverviewOut(BaseModel):
    settings: PingMonitorSettingsOut
    summary: PingSummaryOut
    devices: list[PingDeviceOut]


class PingHistoryOut(BaseModel):
    device_id: UUID
    hours: int
    samples: list[PingSampleOut]
