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


class SnapshotDiffOut(BaseModel):
    from_id: UUID
    to_id: UUID
    from_collected_at: datetime
    to_collected_at: datetime
    # Unified diff lines (as produced by difflib.unified_diff), one per
    # array entry - "+"/"-"-prefixed lines are additions/removals, " "
    # (space) prefixed lines are unchanged context, "@@ ... @@" lines mark
    # hunk boundaries. Empty when the two snapshots are identical.
    diff: list[str]
