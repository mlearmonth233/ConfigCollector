from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.snmp_monitor import SnmpAlertKind


class SnmpMonitorConfigUpdate(BaseModel):
    """Full settings form. `smtp_password` is write-only and only replaced
    when supplied non-empty."""

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
    recipients: list[str] = []
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    smtp_from: str | None = None

    @field_validator("recipients")
    @classmethod
    def _emails(cls, value: list[str]) -> list[str]:
        cleaned = []
        for raw in value:
            addr = raw.strip()
            if not addr:
                continue
            if "@" not in addr or addr.startswith("@") or addr.endswith("@") or " " in addr:
                raise ValueError(f"'{addr}' doesn't look like an email address")
            cleaned.append(addr)
        return cleaned

    @field_validator("smtp_host", "smtp_username", "smtp_from")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


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
    recipients: list[str]
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    has_smtp_password: bool
    smtp_starttls: bool
    smtp_ssl: bool
    smtp_from: str | None
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_result: str | None
    monitored_device_count: int


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
    created_at: datetime


class SnmpTestEmailResult(BaseModel):
    ok: bool
    message: str
