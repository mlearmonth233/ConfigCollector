from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.snmp_monitor import SnmpAlertKind


class SnmpMonitorConfigUpdate(BaseModel):
    """Full settings form. Delivery (recipients, SMTP, webhooks) is set on
    the Alerts page, not here."""

    enabled: bool = False
    interval_minutes: int = Field(default=5, ge=1, le=1440)
    device_ids: list[UUID] | None = None  # None = all devices
    snmp_profile_id: UUID | None = None
    alert_link_down: bool = True
    alert_link_up: bool = False
    alert_ap_down: bool = True
    alert_ap_up: bool = False
    alert_device_down: bool = True
    alert_device_up: bool = True
    alert_syslog_max_level: int | None = Field(default=None, ge=0, le=7)


class SnmpMonitorConfigOut(BaseModel):
    enabled: bool
    interval_minutes: int
    device_ids: list[UUID] | None
    snmp_profile_id: UUID | None
    alert_link_down: bool
    alert_link_up: bool
    alert_ap_down: bool
    alert_ap_up: bool
    alert_device_down: bool
    alert_device_up: bool
    alert_syslog_max_level: int | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_result: str | None
    monitored_device_count: int
    # Whether any delivery channel (Alerts page) is configured.
    channels_configured: bool


class SnmpAlertOut(BaseModel):
    id: UUID
    device_id: UUID | None
    device_name: str
    kind: SnmpAlertKind
    kind_label: str
    subject: str
    detail: str | None
    emailed: bool
    email_error: str | None
    notified_via: str | None = None
    webhook_error: str | None = None
    created_at: datetime


