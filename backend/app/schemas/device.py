from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.device import NetworkZone


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
    custom_commands: str | None = None
    device_role: str | None = None
    network_zone: NetworkZone | None = None


class DeviceUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    device_type: str | None = None
    site: str | None = None
    credential_id: UUID | None = None
    custom_commands: str | None = None
    device_role: str | None = None
    network_zone: NetworkZone | None = None


class DeviceOut(BaseModel):
    id: UUID
    name: str
    host: str
    port: int
    device_type: str
    site: str | None
    credential_id: UUID | None
    custom_commands: str | None
    device_role: str | None
    network_zone: NetworkZone | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeviceImportResult(BaseModel):
    created: int
    errors: list[str]


class DeviceDetectionOut(BaseModel):
    device_role: str | None
    device_role_label: str | None
    network_zone: NetworkZone | None
    suggested_device_type: str | None
