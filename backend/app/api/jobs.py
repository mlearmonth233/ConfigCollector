import io
import zipfile
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.database import get_db
from app.models.credential import Credential, MfaMode
from app.models.device import Device
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.user import User
from app.schemas.job import JobCreate, JobDetailOut, JobItemOut, JobOut
from app.services.device_types import parse_command_list, resolve_commands
from app.services.filenames import build_snapshot_filename
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
    device_query = (
        select(Device)
        .options(selectinload(Device.credential).selectinload(Credential.fallback_credential))
        .where(Device.org_id == user.org_id)
    )
    if payload.device_ids:
        device_query = device_query.where(Device.id.in_(payload.device_ids))
        devices = list(await db.scalars(device_query))
        if len(devices) != len(set(payload.device_ids)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more devices not found")
    else:
        devices = list(await db.scalars(device_query))

    if not devices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No devices to collect from")

    credential_otps = payload.credential_otps or {}

    def _passcode_creds_in_use(d: Device) -> list[Credential]:
        # Which credential ends up authenticating a device (primary or its
        # fallback) isn't known until it's actually contacted, and an OTP
        # can't be requested mid-run - so both must be supplied up front
        # whenever either uses passcode-based MFA.
        candidates = [d.credential, d.credential.fallback_credential if d.credential else None]
        return [c for c in candidates if c is not None and c.mfa_mode == MfaMode.PASSCODE]

    missing_otp_credentials = sorted(
        {
            c.name
            for d in devices
            for c in _passcode_creds_in_use(d)
            if not credential_otps.get(str(c.id))
        }
    )
    if missing_otp_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A one-time passcode is required for these credentials before collection can start: "
                + ", ".join(missing_otp_credentials)
            ),
        )

    commands_by_type = payload.commands_by_device_type or {}
    missing_command_devices = sorted(
        d.name
        for d in devices
        if not commands_by_type.get(d.device_type)
        and not _has_resolvable_commands(d.device_type, d.custom_commands)
    )
    if missing_command_devices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "These devices have no default command for their type and no custom command "
                "configured - specify commands_by_device_type before starting: "
                + ", ".join(missing_command_devices)
            ),
        )

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

    for item, device in zip(items, devices):
        raw_override = commands_by_type.get(device.device_type)
        commands_override = parse_command_list(raw_override) if raw_override else None
        otp = credential_otps.get(str(device.credential_id)) if device.credential_id else None
        fallback = device.credential.fallback_credential if device.credential else None
        fallback_otp = credential_otps.get(str(fallback.id)) if fallback else None
        collect_device_task.delay(
            str(item.id), commands_override=commands_override, otp=otp, fallback_otp=fallback_otp
        )

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


@router.get("/{job_id}/download")
async def download_job_configs(
    job_id: UUID,
    ext: Literal["txt", "log"] = Query(default="txt"),
    include_timestamp: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """All of a job's successfully-collected configs, bundled as one ZIP -
    each device named the same way a single-snapshot download would be."""
    job = await _get_owned_job(db, job_id, user.org_id)
    completed_items = [item for item in job.items if item.snapshot is not None]
    if not completed_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No collected configs to download yet for this job",
        )

    buffer = io.BytesIO()
    used_filenames: dict[str, int] = {}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in completed_items:
            device_name = item.device.name if item.device else str(item.device_id)
            filename = build_snapshot_filename(
                device_name,
                collected_at=item.snapshot.collected_at,
                ext=ext,
                include_timestamp=include_timestamp,
            )
            if filename in used_filenames:
                # Two devices sharing a sanitized name (or a re-run with no
                # timestamp) would otherwise silently clobber one entry.
                used_filenames[filename] += 1
                stem, _, suffix = filename.rpartition(".")
                filename = f"{stem}_{used_filenames[filename]}.{suffix}"
            else:
                used_filenames[filename] = 0
            zf.writestr(filename, item.snapshot.content)

    zip_filename = f"job-{str(job.id)[:8]}-configs.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


async def _get_owned_job(db: AsyncSession, job_id: UUID, org_id: UUID) -> CollectionJob:
    # A plain select() (rather than Session.get()) always honors the eager
    # loading options below, even when `job` is already in this session's
    # identity map from earlier in the same request (e.g. just created).
    # get() would instead return that cached-but-not-eager-loaded instance,
    # so accessing job.items/snapshot afterwards would trigger a lazy load
    # that fails outside of an awaited context.
    #
    # populate_existing=True is equally required: by default SQLAlchemy's
    # identity map wins even for a fresh select() - already-loaded *column*
    # attributes on an identity-mapped row are left as-is, not overwritten,
    # unless told otherwise. create_job's items were loaded/refreshed in
    # this same session before dispatch; when CELERY_TASK_ALWAYS_EAGER runs
    # a device's task inline (via a separate sync session/connection) and
    # updates its status before we get here, this query would otherwise
    # still hand back the item's pre-dispatch (pending) status.
    job = await db.scalar(
        select(CollectionJob)
        .where(CollectionJob.id == job_id)
        .options(
            selectinload(CollectionJob.items).selectinload(CollectionJobItem.device),
            selectinload(CollectionJob.items).selectinload(CollectionJobItem.snapshot),
        )
        .execution_options(populate_existing=True)
    )
    if job is None or job.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


async def _fetch_job_detail(db: AsyncSession, job_id: UUID, org_id: UUID) -> JobDetailOut:
    job = await _get_owned_job(db, job_id, org_id)
    return _to_job_detail_out(job)


def _has_resolvable_commands(device_type: str, custom_commands: str | None) -> bool:
    try:
        resolve_commands(device_type, custom_commands)
        return True
    except ValueError:
        return False


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
                used_fallback_credential=item.used_fallback_credential,
                live_output=item.live_output,
                started_at=item.started_at,
                finished_at=item.finished_at,
                snapshot_id=item.snapshot.id if item.snapshot else None,
            )
        )
    base = _to_job_out(job)
    return JobDetailOut(**base.model_dump(), items=items_out)
