"""services/scheduling.py: next-run computation and validation for every
schedule frequency, including timezone and DST behaviour - pure, no DB."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.models.schedule import ScheduleFrequency
from app.services.scheduling import ScheduleTiming, compute_next_run_at, resolve_timezone, validate_timing

CHICAGO = ZoneInfo("America/Chicago")


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# --- every N hours / once ---------------------------------------------------


def test_every_n_hours_adds_the_interval():
    timing = ScheduleTiming(ScheduleFrequency.EVERY_N_HOURS, interval_hours=6)
    assert compute_next_run_at(timing, after=_utc(2026, 3, 1, 10, 0)) == _utc(2026, 3, 1, 16, 0)


def test_once_returns_the_moment_while_it_is_still_ahead():
    timing = ScheduleTiming(ScheduleFrequency.ONCE, run_once_at=_utc(2026, 3, 5, 9, 30))
    assert compute_next_run_at(timing, after=_utc(2026, 3, 1, 0, 0)) == _utc(2026, 3, 5, 9, 30)


def test_once_returns_none_after_it_has_fired():
    # None is the signal to run_due_schedules that the schedule is finished
    # and should disable itself rather than fire again.
    timing = ScheduleTiming(ScheduleFrequency.ONCE, run_once_at=_utc(2026, 3, 5, 9, 30))
    assert compute_next_run_at(timing, after=_utc(2026, 3, 5, 9, 30)) is None
    assert compute_next_run_at(timing, after=_utc(2026, 3, 6, 0, 0)) is None


def test_once_treats_a_naive_stored_datetime_as_utc():
    # SQLite hands DateTime(timezone=True) back naive.
    timing = ScheduleTiming(ScheduleFrequency.ONCE, run_once_at=datetime(2026, 3, 5, 9, 30))
    assert compute_next_run_at(timing, after=_utc(2026, 3, 1, 0, 0)) == _utc(2026, 3, 5, 9, 30)


# --- daily -----------------------------------------------------------------


def test_daily_today_if_the_time_has_not_passed_else_tomorrow():
    timing = ScheduleTiming(ScheduleFrequency.DAILY, run_at_hour=2, run_at_minute=30)
    assert compute_next_run_at(timing, after=_utc(2026, 3, 1, 1, 0)) == _utc(2026, 3, 1, 2, 30)
    assert compute_next_run_at(timing, after=_utc(2026, 3, 1, 2, 30)) == _utc(2026, 3, 2, 2, 30)
    assert compute_next_run_at(timing, after=_utc(2026, 3, 1, 23, 59)) == _utc(2026, 3, 2, 2, 30)


def test_daily_time_is_interpreted_in_the_schedule_timezone():
    # 02:00 Chicago in February (CST, UTC-6) is 08:00 UTC.
    timing = ScheduleTiming(ScheduleFrequency.DAILY, run_at_hour=2, run_at_minute=0, timezone="America/Chicago")
    result = compute_next_run_at(timing, after=_utc(2026, 2, 10, 12, 0))
    assert result == _utc(2026, 2, 11, 8, 0)
    assert result.astimezone(CHICAGO).hour == 2


def test_daily_keeps_the_local_hour_across_a_dst_change():
    # US DST starts 2026-03-08. The day before, 02:00 CST = 08:00 UTC; the
    # day after, 02:00 CDT = 07:00 UTC. A fixed UTC hour would drift; the
    # local hour must not.
    timing = ScheduleTiming(ScheduleFrequency.DAILY, run_at_hour=2, run_at_minute=0, timezone="America/Chicago")
    before = compute_next_run_at(timing, after=_utc(2026, 3, 6, 12, 0))
    after_change = compute_next_run_at(timing, after=_utc(2026, 3, 9, 12, 0))
    assert before == _utc(2026, 3, 7, 8, 0)
    assert after_change == _utc(2026, 3, 10, 7, 0)
    assert before.astimezone(CHICAGO).hour == after_change.astimezone(CHICAGO).hour == 2


# --- weekly ----------------------------------------------------------------


def test_weekly_picks_the_next_matching_weekday():
    # 2026-03-04 is a Wednesday. Weekly on Friday (4) at 03:00 UTC.
    timing = ScheduleTiming(ScheduleFrequency.WEEKLY, run_at_hour=3, run_at_minute=0, day_of_week=4)
    result = compute_next_run_at(timing, after=_utc(2026, 3, 4, 12, 0))
    assert result == _utc(2026, 3, 6, 3, 0)
    assert result.weekday() == 4


def test_weekly_same_day_rolls_a_full_week_once_the_time_has_passed():
    # Friday 2026-03-06 at 05:00 UTC, schedule is Fridays at 03:00 -> next Friday.
    timing = ScheduleTiming(ScheduleFrequency.WEEKLY, run_at_hour=3, run_at_minute=0, day_of_week=4)
    assert compute_next_run_at(timing, after=_utc(2026, 3, 6, 5, 0)) == _utc(2026, 3, 13, 3, 0)
    # ...but still today if it hasn't.
    assert compute_next_run_at(timing, after=_utc(2026, 3, 6, 1, 0)) == _utc(2026, 3, 6, 3, 0)


def test_weekly_weekday_is_judged_in_the_schedule_timezone():
    # 03:00 UTC Saturday is still Friday evening in Chicago - a "Friday at
    # 22:00 Chicago" schedule is due later that same Chicago Friday.
    timing = ScheduleTiming(
        ScheduleFrequency.WEEKLY, run_at_hour=22, run_at_minute=0, day_of_week=4, timezone="America/Chicago"
    )
    result = compute_next_run_at(timing, after=_utc(2026, 3, 7, 3, 0))  # Sat 03:00 UTC = Fri 21:00 CST
    assert result == _utc(2026, 3, 7, 4, 0)  # Fri 22:00 CST
    assert result.astimezone(CHICAGO).weekday() == 4


# --- monthly ---------------------------------------------------------------


def test_monthly_this_month_if_ahead_else_next_month():
    timing = ScheduleTiming(ScheduleFrequency.MONTHLY, run_at_hour=1, run_at_minute=0, day_of_month=15)
    assert compute_next_run_at(timing, after=_utc(2026, 3, 10, 0, 0)) == _utc(2026, 3, 15, 1, 0)
    assert compute_next_run_at(timing, after=_utc(2026, 3, 15, 1, 0)) == _utc(2026, 4, 15, 1, 0)


def test_monthly_day_31_runs_on_the_last_day_of_shorter_months():
    timing = ScheduleTiming(ScheduleFrequency.MONTHLY, run_at_hour=1, run_at_minute=0, day_of_month=31)
    assert compute_next_run_at(timing, after=_utc(2026, 4, 1, 0, 0)) == _utc(2026, 4, 30, 1, 0)
    assert compute_next_run_at(timing, after=_utc(2026, 2, 1, 0, 0)) == _utc(2026, 2, 28, 1, 0)
    assert compute_next_run_at(timing, after=_utc(2028, 2, 1, 0, 0)) == _utc(2028, 2, 29, 1, 0)  # leap year


def test_monthly_rolls_over_december_into_january():
    timing = ScheduleTiming(ScheduleFrequency.MONTHLY, run_at_hour=1, run_at_minute=0, day_of_month=1)
    assert compute_next_run_at(timing, after=_utc(2026, 12, 20, 0, 0)) == _utc(2027, 1, 1, 1, 0)


# --- validation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("timing", "message"),
    [
        (ScheduleTiming(ScheduleFrequency.EVERY_N_HOURS, interval_hours=0), "interval_hours"),
        (ScheduleTiming(ScheduleFrequency.ONCE), "run_once_at"),
        (ScheduleTiming(ScheduleFrequency.ONCE, run_once_at=_utc(2000, 1, 1)), "in the future"),
        (ScheduleTiming(ScheduleFrequency.DAILY), "run_at_hour and run_at_minute"),
        (ScheduleTiming(ScheduleFrequency.DAILY, run_at_hour=24, run_at_minute=0), "between 0 and 23"),
        (ScheduleTiming(ScheduleFrequency.DAILY, run_at_hour=2, run_at_minute=60), "between 0 and 59"),
        (ScheduleTiming(ScheduleFrequency.DAILY, run_at_hour=2, run_at_minute=0, timezone="Mars/Olympus"), "timezone"),
        (ScheduleTiming(ScheduleFrequency.WEEKLY, run_at_hour=2, run_at_minute=0), "day_of_week"),
        (ScheduleTiming(ScheduleFrequency.WEEKLY, run_at_hour=2, run_at_minute=0, day_of_week=7), "day_of_week"),
        (ScheduleTiming(ScheduleFrequency.MONTHLY, run_at_hour=2, run_at_minute=0), "day_of_month"),
        (ScheduleTiming(ScheduleFrequency.MONTHLY, run_at_hour=2, run_at_minute=0, day_of_month=32), "day_of_month"),
    ],
)
def test_validate_timing_rejects_bad_combinations(timing, message):
    with pytest.raises(ValueError, match=message):
        validate_timing(timing)


def test_validate_timing_accepts_each_frequency():
    validate_timing(ScheduleTiming(ScheduleFrequency.EVERY_N_HOURS, interval_hours=1))
    validate_timing(ScheduleTiming(ScheduleFrequency.ONCE, run_once_at=datetime.now(timezone.utc) + timedelta(days=1)))
    validate_timing(ScheduleTiming(ScheduleFrequency.DAILY, run_at_hour=2, run_at_minute=0, timezone="Europe/London"))
    validate_timing(ScheduleTiming(ScheduleFrequency.WEEKLY, run_at_hour=2, run_at_minute=0, day_of_week=6))
    validate_timing(ScheduleTiming(ScheduleFrequency.MONTHLY, run_at_hour=2, run_at_minute=0, day_of_month=31))


def test_resolve_timezone_defaults_to_utc():
    assert resolve_timezone(None) is timezone.utc
    assert resolve_timezone("") is timezone.utc
    assert resolve_timezone("utc") is timezone.utc
    assert str(resolve_timezone("Europe/London")) == "Europe/London"
