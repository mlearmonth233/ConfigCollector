"""When a Schedule runs next - the one place that logic lives, shared by the
API (computing next_run_at when a schedule is created/edited) and the
Celery beat task (advancing it after each run). Pure functions over plain
values, so every frequency's edge cases are unit-testable without a DB.

Times of day are interpreted in the schedule's own IANA timezone
(Schedule.timezone, e.g. "America/Chicago"; None means UTC) - a "daily at
02:00" backup should fire at 2am *where the engineer is*, and keep doing so
across a DST change, which a fixed UTC hour can't do.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.schedule import ScheduleFrequency

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def resolve_timezone(name: str | None) -> tzinfo:
    """The tzinfo for an IANA zone name; None/blank means UTC. Raises
    ValueError for a name the system's zone database doesn't know (on
    Windows that database is the `tzdata` package - see requirements.txt)."""
    if not name or name.strip().upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError, KeyError, OSError) as exc:
        raise ValueError(f"Unknown timezone '{name}'") from exc


@dataclass(frozen=True)
class ScheduleTiming:
    """Everything about a schedule that determines *when* it runs - the
    Schedule model's timing columns, detached from the ORM so both the API
    payloads and the model row can be checked/computed the same way."""

    frequency: ScheduleFrequency
    interval_hours: int | None = None
    run_at_hour: int | None = None
    run_at_minute: int | None = None
    day_of_week: int | None = None  # 0 = Monday ... 6 = Sunday (Python's weekday())
    day_of_month: int | None = None  # 1..31; clamped to the month's last day where shorter
    run_once_at: datetime | None = None
    timezone: str | None = None  # IANA name; None = UTC

    @classmethod
    def from_schedule(cls, schedule) -> "ScheduleTiming":
        return cls(
            frequency=schedule.frequency,
            interval_hours=schedule.interval_hours,
            run_at_hour=schedule.run_at_hour,
            run_at_minute=schedule.run_at_minute,
            day_of_week=schedule.day_of_week,
            day_of_month=schedule.day_of_month,
            run_once_at=schedule.run_once_at,
            timezone=schedule.timezone,
        )


def _as_utc(value: datetime) -> datetime:
    # SQLite hands DateTime(timezone=True) columns back naive - the whole
    # app treats a naive stored datetime as UTC, so do the same here.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def validate_timing(timing: ScheduleTiming, *, now: datetime | None = None) -> None:
    """Raises ValueError (with a user-facing message) if the timing fields
    don't make sense for the frequency."""
    f = timing.frequency
    if f == ScheduleFrequency.EVERY_N_HOURS:
        if timing.interval_hours is None or timing.interval_hours < 1:
            raise ValueError("interval_hours must be at least 1 for an every-N-hours schedule")
        return

    if f == ScheduleFrequency.ONCE:
        if timing.run_once_at is None:
            raise ValueError("run_once_at (the date and time to run) is required for a one-time schedule")
        if _as_utc(timing.run_once_at) <= (now or datetime.now(timezone.utc)):
            raise ValueError("run_once_at must be in the future")
        return

    # DAILY / WEEKLY / MONTHLY all need a time of day (in the schedule's timezone).
    if timing.run_at_hour is None or timing.run_at_minute is None:
        raise ValueError(f"run_at_hour and run_at_minute are required for a {f.value} schedule")
    if not (0 <= timing.run_at_hour <= 23):
        raise ValueError("run_at_hour must be between 0 and 23")
    if not (0 <= timing.run_at_minute <= 59):
        raise ValueError("run_at_minute must be between 0 and 59")
    resolve_timezone(timing.timezone)

    if f == ScheduleFrequency.WEEKLY:
        if timing.day_of_week is None or not (0 <= timing.day_of_week <= 6):
            raise ValueError("day_of_week (0 = Monday ... 6 = Sunday) is required for a weekly schedule")
    elif f == ScheduleFrequency.MONTHLY:
        if timing.day_of_month is None or not (1 <= timing.day_of_month <= 31):
            raise ValueError("day_of_month (1-31) is required for a monthly schedule")


def _clamp_day(year: int, month: int, day: int) -> date:
    """`day` of that month, or its last day if the month is shorter - so a
    "monthly on the 31st" schedule still runs in April (on the 30th) and
    February (on the 28th/29th) instead of silently skipping them."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def compute_next_run_at(timing: ScheduleTiming, *, after: datetime) -> datetime | None:
    """The first run strictly after `after` (a tz-aware UTC datetime), as
    UTC - or None for a one-time schedule whose moment has already passed
    (it's finished; callers disable it rather than re-running it)."""
    after = _as_utc(after)
    f = timing.frequency

    if f == ScheduleFrequency.EVERY_N_HOURS:
        assert timing.interval_hours is not None
        return after + timedelta(hours=timing.interval_hours)

    if f == ScheduleFrequency.ONCE:
        assert timing.run_once_at is not None
        run_at = _as_utc(timing.run_once_at)
        return run_at if run_at > after else None

    assert timing.run_at_hour is not None and timing.run_at_minute is not None
    tz = resolve_timezone(timing.timezone)
    local_now = after.astimezone(tz)
    at = time(timing.run_at_hour, timing.run_at_minute)

    def _local(d: date) -> datetime:
        return datetime.combine(d, at, tzinfo=tz)

    if f == ScheduleFrequency.DAILY:
        candidate = _local(local_now.date())
        if candidate <= local_now:
            candidate = _local(local_now.date() + timedelta(days=1))
    elif f == ScheduleFrequency.WEEKLY:
        assert timing.day_of_week is not None
        days_ahead = (timing.day_of_week - local_now.weekday()) % 7
        candidate = _local(local_now.date() + timedelta(days=days_ahead))
        if candidate <= local_now:
            candidate = _local(candidate.date() + timedelta(days=7))
    elif f == ScheduleFrequency.MONTHLY:
        assert timing.day_of_month is not None
        candidate = _local(_clamp_day(local_now.year, local_now.month, timing.day_of_month))
        if candidate <= local_now:
            year, month = _next_month(local_now.year, local_now.month)
            candidate = _local(_clamp_day(year, month, timing.day_of_month))
    else:  # pragma: no cover - every enum member is handled above
        raise ValueError(f"Unsupported schedule frequency {f!r}")

    return candidate.astimezone(timezone.utc)
