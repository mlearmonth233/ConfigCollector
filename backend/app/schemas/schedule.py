from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, model_validator

from app.models.schedule import ScheduleFrequency
from app.services.scheduling import ScheduleTiming, validate_timing


class ScheduleCreate(BaseModel):
    """Which timing fields are required depends on `frequency` (see
    services/scheduling.py):

    - once:          run_once_at (an ISO datetime; UTC if no offset given)
    - every_n_hours: interval_hours
    - daily:         run_at_hour, run_at_minute
    - weekly:        run_at_hour, run_at_minute, day_of_week (0=Mon..6=Sun)
    - monthly:       run_at_hour, run_at_minute, day_of_month (1-31)

    `timezone` (an IANA name like "Europe/London") says what the time-of-
    day fields mean for daily/weekly/monthly; omitted means UTC."""

    name: str
    device_ids: list[UUID] | None = None  # None/omitted = every device in the org, re-resolved at each run
    frequency: ScheduleFrequency
    interval_hours: int | None = None
    run_at_hour: int | None = None
    run_at_minute: int | None = None
    day_of_week: int | None = None
    day_of_month: int | None = None
    run_once_at: datetime | None = None
    timezone: str | None = None

    def timing(self) -> ScheduleTiming:
        return ScheduleTiming(
            frequency=self.frequency,
            interval_hours=self.interval_hours,
            run_at_hour=self.run_at_hour,
            run_at_minute=self.run_at_minute,
            day_of_week=self.day_of_week,
            day_of_month=self.day_of_month,
            run_once_at=self.run_once_at,
            timezone=self.timezone,
        )

    @model_validator(mode="after")
    def _validate_frequency_fields(self) -> "ScheduleCreate":
        validate_timing(self.timing())  # raises ValueError -> 422 with its message
        return self


class ScheduleUpdate(BaseModel):
    """Partial update - only fields actually supplied are changed.
    Supplying any timing field (frequency, interval_hours, run_at_*,
    day_of_*, run_once_at, timezone) recomputes next_run_at from now;
    supplying only name/enabled/device_ids leaves the timing untouched."""

    name: str | None = None
    enabled: bool | None = None
    device_ids: list[UUID] | None = None
    clear_device_ids: bool = False  # explicit flag - device_ids=None alone is ambiguous with "don't change"
    frequency: ScheduleFrequency | None = None
    interval_hours: int | None = None
    run_at_hour: int | None = None
    run_at_minute: int | None = None
    day_of_week: int | None = None
    day_of_month: int | None = None
    run_once_at: datetime | None = None
    timezone: str | None = None

    TIMING_FIELDS: ClassVar[tuple[str, ...]] = (
        "frequency",
        "interval_hours",
        "run_at_hour",
        "run_at_minute",
        "day_of_week",
        "day_of_month",
        "run_once_at",
        "timezone",
    )

    def changes_timing(self) -> bool:
        return any(getattr(self, field) is not None for field in self.TIMING_FIELDS)


class ScheduleOut(BaseModel):
    id: UUID
    name: str
    enabled: bool
    device_ids: list[UUID] | None
    frequency: ScheduleFrequency
    interval_hours: int | None
    run_at_hour: int | None
    run_at_minute: int | None
    day_of_week: int | None
    day_of_month: int | None
    run_once_at: datetime | None
    timezone: str | None
    next_run_at: datetime
    last_run_at: datetime | None
    last_job_id: UUID | None
    created_at: datetime
