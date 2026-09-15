from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.job import JobStatus


class DnsCheckJobCreate(BaseModel):
    targets: list[str] = Field(min_length=1)


class DnsCheckJobItemOut(BaseModel):
    id: UUID
    target: str
    status: JobStatus
    ping_ok: bool | None
    forward_ok: bool | None
    forward_ips: list[str]
    reverse_ok: bool | None
    reverse_hostname: str | None
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class DnsCheckJobOut(BaseModel):
    id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    item_count: int

    model_config = {"from_attributes": True}


class DnsCheckJobDetailOut(DnsCheckJobOut):
    items: list[DnsCheckJobItemOut]


class DnsCheckJobClearResult(BaseModel):
    deleted: int
