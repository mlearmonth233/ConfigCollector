from datetime import datetime, timedelta, timezone
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

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


def _parse_device_ids(raw: str | None) -> list[UUID] | None:
    if not raw:
        return None
    return [UUID(x) for x in raw.split(",") if x.strip()]


def _serialize_device_ids(ids: list[UUID] | None) -> str | None:
    if not ids:
        return None
    return ",".join(str(i) for i in ids)


def compute_next_run_at(
    frequency: ScheduleFrequency,
    interval_hours: int | None,
    run_at_hour: int | None,
    run_at_minute: int | None,
    *,
    after: datetime,
) -> datetime:
    if frequency == ScheduleFrequency.EVERY_N_HOURS:
        assert interval_hours is not None
        return after + timedelta(hours=interval_hours)
    # DAILY - next occurrence of run_at_hour:run_at_minute UTC, today if
    # that hasn't passed yet, otherwise tomorrow.
    assert run_at_hour is not None and run_at_minute is not None
    candidate = after.replace(hour=run_at_hour, minute=run_at_minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    return candidate


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
    schedule = Schedule(
        org_id=admin.org_id,
        created_by_id=admin.id,
        name=payload.name,
        device_ids=_serialize_device_ids(payload.device_ids),
        frequency=payload.frequency,
        interval_hours=payload.interval_hours,
        run_at_hour=payload.run_at_hour,
        run_at_minute=payload.run_at_minute,
        next_run_at=compute_next_run_at(
            payload.frequency, payload.interval_hours, payload.run_at_hour, payload.run_at_minute, after=now
        ),
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

    frequency_changed = payload.frequency is not None
    if frequency_changed:
        schedule.frequency = payload.frequency
    if payload.interval_hours is not None:
        schedule.interval_hours = payload.interval_hours
    if payload.run_at_hour is not None:
        schedule.run_at_hour = payload.run_at_hour
    if payload.run_at_minute is not None:
        schedule.run_at_minute = payload.run_at_minute

    if frequency_changed or payload.interval_hours is not None or payload.run_at_hour is not None or payload.run_at_minute is not None:
        if schedule.frequency == ScheduleFrequency.EVERY_N_HOURS and not schedule.interval_hours:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="interval_hours must be at least 1 for an every-N-hours schedule",
            )
        if schedule.frequency == ScheduleFrequency.DAILY and (
            schedule.run_at_hour is None or schedule.run_at_minute is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="run_at_hour and run_at_minute are required for a daily schedule",
            )
        schedule.next_run_at = compute_next_run_at(
            schedule.frequency,
            schedule.interval_hours,
            schedule.run_at_hour,
            schedule.run_at_minute,
            after=datetime.now(timezone.utc),
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
