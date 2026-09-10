from pydantic import BaseModel, field_validator


class OrganizationSettingsOut(BaseModel):
    name: str
    # None = keep collected snapshots forever.
    snapshot_retention_days: int | None


class OrganizationSettingsUpdate(BaseModel):
    snapshot_retention_days: int | None = None
    # Same ambiguity as ScheduleUpdate.clear_device_ids - snapshot_retention_days=None
    # alone can't distinguish "leave it alone" from "clear it back to keep-forever".
    clear_retention: bool = False

    @field_validator("snapshot_retention_days")
    @classmethod
    def _positive(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("snapshot_retention_days must be at least 1")
        return value
