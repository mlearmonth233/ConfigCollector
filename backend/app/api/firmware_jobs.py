from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.database import get_db
from app.models.credential import Credential, MfaMode
from app.models.device import Device
from app.models.firmware import FirmwareImage, FirmwareJob, FirmwareJobItem, FirmwareJobStatus
from app.models.user import User
from app.schemas.firmware import FirmwareJobCreate, FirmwareJobDetailOut, FirmwareJobItemOut, FirmwareJobOut
from app.services.device_types import FIRMWARE_FLASH_PREFIXES
from app.tasks import push_firmware_task

router = APIRouter(prefix="/api/firmware-jobs", tags=["firmware-jobs"])


@router.get("", response_model=list[FirmwareJobOut])
async def list_firmware_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FirmwareJobOut]:
    jobs = list(
        await db.scalars(
            select(FirmwareJob).where(FirmwareJob.org_id == user.org_id).order_by(FirmwareJob.created_at.desc())
        )
    )
    counts = dict(
        (
            await db.execute(
                select(FirmwareJobItem.job_id, func.count())
                .where(FirmwareJobItem.job_id.in_([job.id for job in jobs]))
                .group_by(FirmwareJobItem.job_id)
            )
        ).all()
    )
    return [_to_job_out(job, counts.get(job.id, 0)) for job in jobs]


@router.post("", response_model=FirmwareJobDetailOut, status_code=status.HTTP_201_CREATED)
async def create_firmware_job(
    payload: FirmwareJobCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareJobDetailOut:
    image = await db.get(FirmwareImage, payload.firmware_image_id)
    if image is None or image.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware image not found")

    device_query = (
        select(Device)
        .options(selectinload(Device.credential).selectinload(Credential.fallback_credential))
        .where(Device.org_id == user.org_id, Device.id.in_(payload.device_ids))
    )
    devices_by_id = {d.id: d for d in await db.scalars(device_query)}
    if len(devices_by_id) != len(set(payload.device_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more devices not found")
    # Preserve the requested order, same reasoning as jobs.py's create_and_dispatch_job.
    devices = [devices_by_id[device_id] for device_id in payload.device_ids]

    unsupported = sorted({d.name for d in devices if d.device_type not in FIRMWARE_FLASH_PREFIXES})
    if unsupported:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Firmware push isn't supported for these devices' type (no known flash-style "
                "filesystem): " + ", ".join(unsupported)
            ),
        )

    org_default_credential = await db.scalar(
        select(Credential)
        .options(selectinload(Credential.fallback_credential))
        .where(Credential.org_id == user.org_id, Credential.is_default.is_(True))
    )

    def _effective_credential(d: Device) -> Credential | None:
        return d.credential or org_default_credential

    missing_credential_devices = sorted(d.name for d in devices if _effective_credential(d) is None)
    if missing_credential_devices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "These devices have no credential assigned and no org-wide default credential is "
                "configured: " + ", ".join(missing_credential_devices)
            ),
        )

    credential_otps = payload.credential_otps or {}

    def _passcode_creds_in_use(d: Device) -> list[Credential]:
        credential = _effective_credential(d)
        candidates = [credential, credential.fallback_credential if credential else None]
        return [c for c in candidates if c is not None and c.mfa_mode == MfaMode.PASSCODE]

    missing_otp_credentials = sorted(
        {c.name for d in devices for c in _passcode_creds_in_use(d) if not credential_otps.get(str(c.id))}
    )
    if missing_otp_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A one-time passcode is required for these credentials before the push can start: "
                + ", ".join(missing_otp_credentials)
            ),
        )

    target_filename = payload.target_filename or image.filename

    job = FirmwareJob(
        org_id=user.org_id,
        created_by_id=user.id,
        firmware_image_id=image.id,
        firmware_filename=image.filename,
        firmware_md5=image.md5,
        firmware_size_bytes=image.size_bytes,
        firmware_storage_path=image.storage_path,
        target_filename=target_filename,
        verify_checksum=payload.verify_checksum,
        reload_after=payload.reload_after,
        status=FirmwareJobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()

    items = [FirmwareJobItem(job_id=job.id, device_id=d.id) for d in devices]
    db.add_all(items)
    await db.commit()
    for item in items:
        await db.refresh(item)

    dispatch_specs = []
    for item, device in zip(items, devices):
        credential = _effective_credential(device)
        otp = credential_otps.get(str(credential.id)) if credential else None
        fallback = credential.fallback_credential if credential else None
        fallback_otp = credential_otps.get(str(fallback.id)) if fallback else None
        dispatch_specs.append({"item_id": str(item.id), "otp": otp, "fallback_otp": fallback_otp})

    # Devices are pushed strictly one at a time - see FirmwareJob's
    # docstring for why this doesn't pipeline the way collection jobs do.
    first, *rest = dispatch_specs
    push_firmware_task.apply_async(
        args=[first["item_id"]],
        kwargs={"otp": first["otp"], "fallback_otp": first["fallback_otp"], "remaining": rest},
    )

    return await fetch_firmware_job_detail(db, job.id, user.org_id)


@router.post("/{job_id}/cancel", response_model=FirmwareJobDetailOut)
async def cancel_firmware_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareJobDetailOut:
    """Same semantics as jobs.py's cancel_job: a device still PENDING is
    marked CANCELLED immediately; a device already mid-transfer can't be
    interrupted right away (a live SCP transfer is opaque until it returns),
    but the queue itself stops advancing past whatever's already running."""
    job = await _get_owned_job(db, job_id, user.org_id)
    if job.status != FirmwareJobStatus.RUNNING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Job has already finished")

    job.cancel_requested = True
    now = datetime.now(timezone.utc)
    for item in job.items:
        if item.status == FirmwareJobStatus.PENDING:
            item.status = FirmwareJobStatus.CANCELLED
            item.finished_at = now
    await db.commit()

    return await fetch_firmware_job_detail(db, job_id, user.org_id)


@router.get("/{job_id}", response_model=FirmwareJobDetailOut)
async def get_firmware_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareJobDetailOut:
    return await fetch_firmware_job_detail(db, job_id, user.org_id)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_firmware_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    job = await _get_owned_job(db, job_id, user.org_id)
    if job.status == FirmwareJobStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This job is still in progress - wait for it to finish (or cancel it) before deleting",
        )
    await db.delete(job)
    await db.commit()


async def _get_owned_job(db: AsyncSession, job_id: UUID, org_id: UUID) -> FirmwareJob:
    job = await db.scalar(
        select(FirmwareJob)
        .where(FirmwareJob.id == job_id)
        .options(selectinload(FirmwareJob.items).selectinload(FirmwareJobItem.device))
        .execution_options(populate_existing=True)
    )
    if job is None or job.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware job not found")
    return job


async def fetch_firmware_job_detail(db: AsyncSession, job_id: UUID, org_id: UUID) -> FirmwareJobDetailOut:
    job = await _get_owned_job(db, job_id, org_id)
    return _to_job_detail_out(job)


def _to_job_out(job: FirmwareJob, item_count: int) -> FirmwareJobOut:
    return FirmwareJobOut(
        id=job.id,
        status=job.status,
        firmware_filename=job.firmware_filename,
        target_filename=job.target_filename,
        verify_checksum=job.verify_checksum,
        reload_after=job.reload_after,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        item_count=item_count,
    )


def _to_job_detail_out(job: FirmwareJob) -> FirmwareJobDetailOut:
    items_out = [
        FirmwareJobItemOut(
            id=item.id,
            device_id=item.device_id,
            device_name=item.device.name if item.device else "",
            status=item.status,
            error_message=item.error_message,
            used_fallback_credential=item.used_fallback_credential,
            checksum_verified=item.checksum_verified,
            live_output=item.live_output,
            started_at=item.started_at,
            finished_at=item.finished_at,
        )
        for item in job.items
    ]
    base = _to_job_out(job, len(job.items))
    return FirmwareJobDetailOut(**base.model_dump(), items=items_out)
