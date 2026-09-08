from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.job import JobStatus


class JobCreate(BaseModel):
    device_ids: list[UUID] | None = None  # None/omitted = all devices in org


class JobItemOut(BaseModel):
    id: UUID
    device_id: UUID
    device_name: str
    status: JobStatus
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    snapshot_id: UUID | None

    model_config = {"from_attributes": True}


class JobOut(BaseModel):
    id: UUID
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    item_count: int

    model_config = {"from_attributes": True}


class JobDetailOut(JobOut):
    items: list[JobItemOut]
