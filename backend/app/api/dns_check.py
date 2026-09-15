from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.database import get_db
from app.models.dns_check import DnsCheckJob, DnsCheckJobItem
from app.models.job import JobStatus
from app.models.user import User
from app.schemas.dns_check import (
    DnsCheckJobClearResult,
    DnsCheckJobCreate,
    DnsCheckJobDetailOut,
    DnsCheckJobItemOut,
    DnsCheckJobOut,
)
from app.services.dns_check import MAX_TARGETS
from app.tasks import run_dns_check_job_task

router = APIRouter(prefix="/api/dns-check", tags=["dns-check"])


@router.post("/jobs", response_model=DnsCheckJobDetailOut, status_code=status.HTTP_201_CREATED)
async def create_dns_check_job(
    payload: DnsCheckJobCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DnsCheckJobDetailOut:
    # De-duplicate while preserving order - a pasted list commonly repeats
    # entries, and there's no reason to check (or show) the same target twice.
    seen: dict[str, None] = {}
    for raw in payload.targets:
        target = raw.strip()
        if target:
            seen.setdefault(target, None)
    targets = list(seen.keys())

    if not targets:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provide at least one hostname or IP")
    if len(targets) > MAX_TARGETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many targets in one paste (max {MAX_TARGETS})",
        )

    job = DnsCheckJob(
        org_id=user.org_id,
        created_by_id=user.id,
        status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()

    items = [DnsCheckJobItem(job_id=job.id, target=target) for target in targets]
    db.add_all(items)
    await db.commit()

    run_dns_check_job_task.apply_async(args=[str(job.id)])

    # Dispatching may have run synchronously (CELERY_TASK_ALWAYS_EAGER, used
    # in tests/dev without a broker) via a separate sync session - fetch
    # fresh through this one rather than touching potentially-stale lazy
    # attributes on this `job`.
    return await fetch_dns_check_job_detail(db, job.id, user.org_id)


@router.get("/jobs", response_model=list[DnsCheckJobOut])
async def list_dns_check_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[DnsCheckJobOut]:
    jobs = list(
        await db.scalars(
            select(DnsCheckJob).where(DnsCheckJob.org_id == user.org_id).order_by(DnsCheckJob.created_at.desc())
        )
    )
    counts = dict(
        (
            await db.execute(
                select(DnsCheckJobItem.job_id, func.count())
                .where(DnsCheckJobItem.job_id.in_([job.id for job in jobs]))
                .group_by(DnsCheckJobItem.job_id)
            )
        ).all()
    )
    return [
        DnsCheckJobOut(
            id=job.id,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            item_count=counts.get(job.id, 0),
        )
        for job in jobs
    ]


@router.delete("/jobs", response_model=DnsCheckJobClearResult)
async def clear_finished_dns_check_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DnsCheckJobClearResult:
    jobs = list(
        await db.scalars(
            select(DnsCheckJob).where(DnsCheckJob.org_id == user.org_id, DnsCheckJob.status != JobStatus.RUNNING)
        )
    )
    for job in jobs:
        await db.delete(job)
    await db.commit()
    return DnsCheckJobClearResult(deleted=len(jobs))


@router.get("/jobs/{job_id}", response_model=DnsCheckJobDetailOut)
async def get_dns_check_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DnsCheckJobDetailOut:
    return await fetch_dns_check_job_detail(db, job_id, user.org_id)


@router.post("/jobs/{job_id}/cancel", response_model=DnsCheckJobDetailOut)
async def cancel_dns_check_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DnsCheckJobDetailOut:
    job = await _get_owned_dns_check_job(db, job_id, user.org_id)
    if job.status != JobStatus.RUNNING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Job has already finished")

    job.cancel_requested = True
    now = datetime.now(timezone.utc)
    for item in job.items:
        if item.status == JobStatus.PENDING:
            item.status = JobStatus.CANCELLED
            item.finished_at = now
    await db.commit()

    return await fetch_dns_check_job_detail(db, job_id, user.org_id)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dns_check_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    job = await _get_owned_dns_check_job(db, job_id, user.org_id)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This job is still in progress - wait for it to finish (or cancel it) before deleting",
        )
    await db.delete(job)
    await db.commit()


async def _get_owned_dns_check_job(db: AsyncSession, job_id: UUID, org_id: UUID) -> DnsCheckJob:
    # See jobs.py's _get_owned_job for why this is a plain select() with
    # populate_existing=True rather than db.get(): the async session's
    # identity map would otherwise mask a status a CELERY_TASK_ALWAYS_EAGER
    # (or a genuinely concurrent worker) run already wrote via its own sync
    # session, since items were loaded/refreshed in this same session
    # before dispatch.
    job = await db.scalar(
        select(DnsCheckJob)
        .where(DnsCheckJob.id == job_id)
        .options(selectinload(DnsCheckJob.items))
        .execution_options(populate_existing=True)
    )
    if job is None or job.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _to_item_out(item: DnsCheckJobItem) -> DnsCheckJobItemOut:
    return DnsCheckJobItemOut(
        id=item.id,
        target=item.target,
        status=item.status,
        ping_ok=item.ping_ok,
        forward_ok=item.forward_ok,
        forward_ips=[ip for ip in (item.forward_ips or "").split(", ") if ip],
        reverse_ok=item.reverse_ok,
        reverse_hostname=item.reverse_hostname,
        started_at=item.started_at,
        finished_at=item.finished_at,
    )


async def fetch_dns_check_job_detail(db: AsyncSession, job_id: UUID, org_id: UUID) -> DnsCheckJobDetailOut:
    job = await _get_owned_dns_check_job(db, job_id, org_id)
    return DnsCheckJobDetailOut(
        id=job.id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        item_count=len(job.items),
        items=[_to_item_out(item) for item in job.items],
    )
