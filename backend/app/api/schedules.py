from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.api.jobs import create_and_dispatch_job, fetch_job_detail
from app.database import get_db
from app.models.device import Device
from app.models.schedule import Schedule, ScheduleFrequency
from app.models.user import User
from app.schemas.job import JobDetailOut
from app.schemas.schedule import ScheduleCreate, ScheduleOut, ScheduleUpdate
from app.services.scheduling import ScheduleTiming, compute_next_run_at, validate_timing

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _parse_device_ids(raw: str | None) -> list[UUID] | None:
    if not raw:
        return None
    return [UUID(x) for x in raw.split(",") if x.strip()]


def _serialize_device_ids(ids: list[UUID] | None) -> str | None:
    if not ids:
        return None
    return ",".join(str(i) for i in ids)


def _as_utc(value: datetime) -> datetime:
    # SQLite returns DateTime(timezone=True) columns naive; the app treats
    # every stored datetime as UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _to_out(schedule: Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=schedule.id,
        name=schedule.name,
        enabled=schedule.enabled,
        device_ids=_parse_device_ids(schedule.device_ids),
        frequency=schedule.frequency,
        interval_hours=schedule.interval_hours,
        run_at_hour=schedule.run_at_hour,
        run_at_minute=schedule.run_at_minute,
        day_of_week=schedule.day_of_week,
        day_of_month=schedule.day_of_month,
        run_once_at=schedule.run_once_at,
        timezone=schedule.timezone,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        last_job_id=schedule.last_job_id,
        created_at=schedule.created_at,
    )


async def _validate_device_ids(db: AsyncSession, org_id, device_ids: list[UUID] | None) -> None:
    if not device_ids:
        return
    count = await db.scalar(
        select(func.count()).select_from(Device).where(Device.org_id == org_id, Device.id.in_(device_ids))
    )
    if count != len(set(device_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more devices not found")


async def _get_owned_schedule(db: AsyncSession, schedule_id: UUID, org_id) -> Schedule:
    schedule = await db.get(Schedule, schedule_id)
    if schedule is None or schedule.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    return schedule


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ScheduleOut]:
    result = await db.scalars(
        select(Schedule).where(Schedule.org_id == user.org_id).order_by(Schedule.created_at)
    )
    return [_to_out(s) for s in result]


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    payload: ScheduleCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScheduleOut:
    await _validate_device_ids(db, admin.org_id, payload.device_ids)

    now = datetime.now(timezone.utc)
    next_run_at = compute_next_run_at(payload.timing(), after=now)
    if next_run_at is None:  # a one-time schedule already in the past - validate_timing normally catches this
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="run_once_at must be in the future")
    schedule = Schedule(
        org_id=admin.org_id,
        created_by_id=admin.id,
        name=payload.name,
        device_ids=_serialize_device_ids(payload.device_ids),
        frequency=payload.frequency,
        interval_hours=payload.interval_hours,
        run_at_hour=payload.run_at_hour,
        run_at_minute=payload.run_at_minute,
        day_of_week=payload.day_of_week,
        day_of_month=payload.day_of_month,
        run_once_at=payload.run_once_at,
        timezone=payload.timezone,
        next_run_at=next_run_at,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return _to_out(schedule)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: UUID,
    payload: ScheduleUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ScheduleOut:
    schedule = await _get_owned_schedule(db, schedule_id, admin.org_id)

    if payload.name is not None:
        schedule.name = payload.name
    if payload.enabled is not None:
        schedule.enabled = payload.enabled
    if payload.device_ids is not None:
        await _validate_device_ids(db, admin.org_id, payload.device_ids)
        schedule.device_ids = _serialize_device_ids(payload.device_ids)
    elif payload.clear_device_ids:
        schedule.device_ids = None

    now = datetime.now(timezone.utc)
    if payload.changes_timing():
        for field in ScheduleUpdate.TIMING_FIELDS:
            value = getattr(payload, field)
            if value is not None:
                setattr(schedule, field, value)
        timing = ScheduleTiming.from_schedule(schedule)
        try:
            validate_timing(timing, now=now)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        next_run_at = compute_next_run_at(timing, after=now)
        if next_run_at is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="run_once_at must be in the future")
        schedule.next_run_at = next_run_at
        # A one-time schedule that had already fired (and so disabled
        # itself) is being given a new moment - it's live again.
        if schedule.frequency == ScheduleFrequency.ONCE and payload.enabled is None:
            schedule.enabled = True
    elif (
        payload.enabled
        and schedule.frequency == ScheduleFrequency.ONCE
        and _as_utc(schedule.next_run_at) <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This one-time schedule has already run - give it a new date and time to run it again",
        )

    await db.commit()
    await db.refresh(schedule)
    return _to_out(schedule)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    schedule = await _get_owned_schedule(db, schedule_id, admin.org_id)
    await db.delete(schedule)
    await db.commit()


@router.post("/{schedule_id}/run-now", response_model=JobDetailOut, status_code=status.HTTP_201_CREATED)
async def run_schedule_now(
    schedule_id: UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> JobDetailOut:
    """Triggers this schedule's job immediately, without waiting for its
    next scheduled time (which is left untouched) - reuses the exact same
    device set and validation as a manual collection, so (unlike an
    unattended scheduled run) this still 400s if a targeted credential
    needs a one-time passcode this endpoint has no way to collect."""
    schedule = await _get_owned_schedule(db, schedule_id, admin.org_id)
    job = await create_and_dispatch_job(
        db,
        org_id=admin.org_id,
        created_by_id=admin.id,
        device_ids=_parse_device_ids(schedule.device_ids),
    )
    schedule.last_run_at = datetime.now(timezone.utc)
    schedule.last_job_id = job.id
    await db.commit()
    return await fetch_job_detail(db, job.id, admin.org_id)
