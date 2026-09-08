from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.database import get_db
from app.models.device import Device
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.user import User
from app.schemas.job import JobCreate, JobDetailOut, JobItemOut, JobOut
from app.tasks import collect_device_task

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[JobOut]:
    result = await db.scalars(
        select(CollectionJob)
        .where(CollectionJob.org_id == user.org_id)
        .options(selectinload(CollectionJob.items))
        .order_by(CollectionJob.created_at.desc())
    )
    return [_to_job_out(job) for job in result]


@router.post("", response_model=JobDetailOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobDetailOut:
    if payload.device_ids:
        result = await db.scalars(
            select(Device).where(Device.org_id == user.org_id, Device.id.in_(payload.device_ids))
        )
        devices = list(result)
        if len(devices) != len(set(payload.device_ids)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more devices not found")
    else:
        devices = list(await db.scalars(select(Device).where(Device.org_id == user.org_id)))

    if not devices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No devices to collect from")

    job = CollectionJob(
        org_id=user.org_id,
        created_by_id=user.id,
        status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()

    items = [CollectionJobItem(job_id=job.id, device_id=d.id) for d in devices]
    db.add_all(items)
    await db.commit()
    for item in items:
        await db.refresh(item)

    for item in items:
        collect_device_task.delay(str(item.id))

    # Dispatching may have run synchronously (CELERY_TASK_ALWAYS_EAGER, used
    # in tests/dev without a broker) via a separate sync session, so re-fetch
    # through this async session with eager-loaded relationships rather than
    # touching potentially-stale lazy attributes on `job`/`items`.
    return await _fetch_job_detail(db, job.id, user.org_id)


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobDetailOut:
    return await _fetch_job_detail(db, job_id, user.org_id)


async def _fetch_job_detail(db: AsyncSession, job_id: UUID, org_id: UUID) -> JobDetailOut:
    # A plain select() (rather than Session.get()) always honors the eager
    # loading options below, even when `job` is already in this session's
    # identity map from earlier in the same request (e.g. just created).
    # get() would instead return that cached-but-not-eager-loaded instance,
    # so accessing job.items/snapshot afterwards would trigger a lazy load
    # that fails outside of an awaited context.
    job = await db.scalar(
        select(CollectionJob)
        .where(CollectionJob.id == job_id)
        .options(
            selectinload(CollectionJob.items).selectinload(CollectionJobItem.device),
            selectinload(CollectionJob.items).selectinload(CollectionJobItem.snapshot),
        )
    )
    if job is None or job.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _to_job_detail_out(job)


def _to_job_out(job: CollectionJob) -> JobOut:
    return JobOut(
        id=job.id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        item_count=len(job.items),
    )


def _to_job_detail_out(job: CollectionJob) -> JobDetailOut:
    items_out = []
    for item in job.items:
        items_out.append(
            JobItemOut(
                id=item.id,
                device_id=item.device_id,
                device_name=item.device.name if item.device else "",
                status=item.status,
                error_message=item.error_message,
                started_at=item.started_at,
                finished_at=item.finished_at,
                snapshot_id=item.snapshot.id if item.snapshot else None,
            )
        )
    base = _to_job_out(job)
    return JobDetailOut(**base.model_dump(), items=items_out)
