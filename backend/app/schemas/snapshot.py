from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SnapshotOut(BaseModel):
    id: UUID
    device_id: UUID | None
    collected_at: datetime
    content: str

    model_config = {"from_attributes": True}


class SnapshotSummaryOut(BaseModel):
    id: UUID
    device_id: UUID | None
    collected_at: datetime

    model_config = {"from_attributes": True}
