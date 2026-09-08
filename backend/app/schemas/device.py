from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    host: str = Field(min_length=1, max_length=255)
    port: int = 22
    device_type: str
    site: str | None = None
    credential_id: UUID | None = None
    custom_commands: str | None = None


class DeviceUpdate(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    device_type: str | None = None
    site: str | None = None
    credential_id: UUID | None = None
    custom_commands: str | None = None


class DeviceOut(BaseModel):
    id: UUID
    name: str
    host: str
    port: int
    device_type: str
    site: str | None
    credential_id: UUID | None
    custom_commands: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeviceImportResult(BaseModel):
    created: int
    errors: list[str]
