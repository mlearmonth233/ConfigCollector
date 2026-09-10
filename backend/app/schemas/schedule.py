from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, model_validator

from app.models.schedule import ScheduleFrequency


class ScheduleCreate(BaseModel):
    name: str
    device_ids: list[UUID] | None = None  # None/omitted = every device in the org, re-resolved at each run
    frequency: ScheduleFrequency
    interval_hours: int | None = None  # required (and only used) when frequency == every_n_hours
    run_at_hour: int | None = None  # required (and only used) when frequency == daily - UTC
    run_at_minute: int | None = None  # required (and only used) when frequency == daily - UTC

    @model_validator(mode="after")
    def _validate_frequency_fields(self) -> "ScheduleCreate":
        if self.frequency == ScheduleFrequency.EVERY_N_HOURS:
            if self.interval_hours is None or self.interval_hours < 1:
                raise ValueError("interval_hours must be at least 1 for an every-N-hours schedule")
        elif self.frequency == ScheduleFrequency.DAILY:
            if self.run_at_hour is None or self.run_at_minute is None:
                raise ValueError("run_at_hour and run_at_minute are required for a daily schedule")
            if not (0 <= self.run_at_hour <= 23):
                raise ValueError("run_at_hour must be between 0 and 23")
            if not (0 <= self.run_at_minute <= 59):
                raise ValueError("run_at_minute must be between 0 and 59")
        return self


class ScheduleUpdate(BaseModel):
    """Partial update - only fields actually supplied are changed.
    Supplying any of frequency/interval_hours/run_at_hour/run_at_minute
    recomputes next_run_at from now; supplying only name/enabled/device_ids
    leaves the existing schedule timing untouched."""

    name: str | None = None
    enabled: bool | None = None
    device_ids: list[UUID] | None = None
    clear_device_ids: bool = False  # explicit flag - device_ids=None alone is ambiguous with "don't change"
    frequency: ScheduleFrequency | None = None
    interval_hours: int | None = None
    run_at_hour: int | None = None
    run_at_minute: int | None = None


class ScheduleOut(BaseModel):
    id: UUID
    name: str
    enabled: bool
    device_ids: list[UUID] | None
    frequency: ScheduleFrequency
    interval_hours: int | None
    run_at_hour: int | None
    run_at_minute: int | None
    next_run_at: datetime
    last_run_at: datetime | None
    last_job_id: UUID | None
    created_at: datetime
