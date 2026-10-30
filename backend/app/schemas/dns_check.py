from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.job import JobStatus


class DnsCheckJobCreate(BaseModel):
    """Either a list of hostnames/IPs, or one subnet to sweep."""

    targets: list[str] = Field(default_factory=list)
    subnet: str | None = None


class SubnetPreviewOut(BaseModel):
    """What a sweep of the typed subnet would check, before starting it."""

    network: str
    netmask: str
    usable: int
    first: str | None
    last: str | None


class DnsCheckJobItemOut(BaseModel):
    id: UUID
    target: str
    status: JobStatus
    ping_ok: bool | None
    forward_ok: bool | None
    forward_ips: list[str]
    reverse_ok: bool | None
    reverse_hostname: str | None
    # IP targets only: does the PTR name resolve back to this address?
    dns_matches: bool | None = None
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
    subnet: str | None = None

    model_config = {"from_attributes": True}


class DnsCheckJobDetailOut(DnsCheckJobOut):
    items: list[DnsCheckJobItemOut]


class DnsCheckJobClearResult(BaseModel):
    deleted: int
