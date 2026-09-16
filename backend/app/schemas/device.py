from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

CONSOLE_PROTOCOLS = ("ssh", "telnet")


def _check_protocol(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().lower() or None
    if value is not None and value not in CONSOLE_PROTOCOLS:
        raise ValueError("console_protocol must be 'ssh' or 'telnet'")
    return value


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    port: int = 22
    # Optional: auto-detected from `name` (see app.services.hostname_detection)
    # when omitted. Left blank and the name doesn't resolve to a role with an
    # unambiguous device_type (e.g. a WLC - could be AireOS or Catalyst 9800)?
    # The request is rejected asking for one explicitly.
    device_type: str | None = None
    site: str | None = None
    credential_id: UUID | None = None
    snmp_profile_id: UUID | None = None
    custom_commands: str | None = None
    device_role: str | None = None
    # Optional out-of-band console path (see models/device.py).
    console_host: str | None = Field(default=None, max_length=255)
    console_port: int | None = Field(default=None, ge=1, le=65535)
    console_protocol: str | None = None
    console_credential_id: UUID | None = None
    console_connect_command: str | None = Field(default=None, max_length=255)

    @field_validator("console_protocol")
    @classmethod
    def _protocol(cls, value: str | None) -> str | None:
        return _check_protocol(value)


class DeviceUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    device_type: str | None = None
    site: str | None = None
    credential_id: UUID | None = None
    snmp_profile_id: UUID | None = None
    clear_snmp_profile: bool = False  # explicit - snmp_profile_id=None alone means "don't change"
    custom_commands: str | None = None
    device_role: str | None = None
    clear_console: bool = False  # explicit - removes the whole console path
    # Optional out-of-band console path (see models/device.py).
    console_host: str | None = Field(default=None, max_length=255)
    console_port: int | None = Field(default=None, ge=1, le=65535)
    console_protocol: str | None = None
    console_credential_id: UUID | None = None
    console_connect_command: str | None = Field(default=None, max_length=255)

    @field_validator("console_protocol")
    @classmethod
    def _protocol(cls, value: str | None) -> str | None:
        return _check_protocol(value)


class DeviceOut(BaseModel):
    id: UUID
    name: str
    host: str
    port: int
    device_type: str
    site: str | None
    credential_id: UUID | None
    snmp_profile_id: UUID | None
    custom_commands: str | None
    device_role: str | None
    console_host: str | None = None
    console_port: int | None = None
    console_protocol: str | None = None
    console_credential_id: UUID | None = None
    console_connect_command: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeviceDetectionOut(BaseModel):
    device_role: str | None
    device_role_label: str | None
    suggested_device_type: str | None


class DeviceReachabilityOut(BaseModel):
    device_id: UUID
    host: str
    ping_ok: bool
    dns_ok: bool
    resolved_ip: str | None


class DeviceClearResult(BaseModel):
    deleted: int
    # Devices left alone because a collection job is currently in progress
    # against them - same guard as the single-device delete endpoint.
    skipped: int
