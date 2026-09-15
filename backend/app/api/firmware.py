import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.credential import Credential, MfaMode
from app.models.device import Device
from app.models.firmware import FirmwareImage, FirmwareUpgradeJob, FirmwareUpgradeJobItem, TransferProtocol
from app.models.job import ACTIVE_JOB_STATUSES, JobStatus
from app.models.user import User
from app.schemas.firmware import (
    FirmwareImageOut,
    FirmwareJobCreate,
    FirmwareJobDetailOut,
    FirmwareJobItemOut,
    FirmwareJobOut,
    NetworkInterfaceOut,
    PushDefaultsOut,
)
from app.services.device_types import parse_command_list
from app.services.firmware_push import DEFAULT_PUSH_COMMANDS, render_push_commands, transfer_port
from app.services.job_reaper import force_stop_job
from app.services.network_interfaces import list_network_interfaces
from app.tasks import firmware_push_device_task

router = APIRouter(prefix="/api/firmware", tags=["firmware"])

settings = get_settings()

# Same sanitization approach as filenames.py's downloaded-config naming -
# any character unsafe (or just awkward) in a filename collapses to "_".
_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\s]+')


def _org_storage_dir(org_id: uuid.UUID) -> Path:
    return Path(settings.firmware_storage_dir) / str(org_id)


@router.get("/network-interfaces", response_model=list[NetworkInterfaceOut])
async def get_network_interfaces(user: User = Depends(get_current_user)) -> list[NetworkInterfaceOut]:
    """This machine's own IPv4 addresses (the one running this backend, not
    any managed device) - see services/network_interfaces.py for why this
    has to be a per-job pick rather than one fixed setting."""
    return [NetworkInterfaceOut(name=i.name, address=i.address) for i in list_network_interfaces()]


@router.get("/push-defaults", response_model=PushDefaultsOut)
async def get_push_defaults(user: User = Depends(get_current_user)) -> PushDefaultsOut:
    """The built-in per-device-type copy command templates a push job falls
    back to when none is supplied - exposed so the UI can prefill (and let
    the user edit) exactly what will run."""
    return PushDefaultsOut(
        commands_by_device_type=dict(DEFAULT_PUSH_COMMANDS),
        placeholders=["{url}", "{protocol}", "{host}", "{port}", "{filename}"],
    )


@router.post("/images", response_model=FirmwareImageOut, status_code=status.HTTP_201_CREATED)
async def upload_firmware_image(
    file: UploadFile,
    label: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareImage:
    original_filename = file.filename or "firmware.bin"
    sanitized = _UNSAFE_FILENAME_CHARS.sub("_", original_filename).strip("_") or "firmware.bin"
    stored_filename = f"{uuid.uuid4().hex}_{sanitized}"

    storage_dir = _org_storage_dir(user.org_id)
    storage_dir.mkdir(parents=True, exist_ok=True)
    dest_path = storage_dir / stored_filename

    size_bytes = 0
    with open(dest_path, "wb") as dest:
        while chunk := await file.read(1024 * 1024):
            dest.write(chunk)
            size_bytes += len(chunk)

    image = FirmwareImage(
        org_id=user.org_id,
        uploaded_by_id=user.id,
        stored_filename=stored_filename,
        original_filename=original_filename,
        label=label,
        size_bytes=size_bytes,
    )
    db.add(image)
    await db.commit()
    await db.refresh(image)
    return image


@router.get("/images", response_model=list[FirmwareImageOut])
async def list_firmware_images(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FirmwareImage]:
    result = await db.scalars(
        select(FirmwareImage).where(FirmwareImage.org_id == user.org_id).order_by(FirmwareImage.created_at.desc())
    )
    return list(result)


@router.delete("/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_firmware_image(
    image_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    image = await db.scalar(
        select(FirmwareImage).where(FirmwareImage.id == image_id, FirmwareImage.org_id == user.org_id)
    )
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware image not found")

    in_progress = await db.scalar(
        select(FirmwareUpgradeJob.id)
        .where(FirmwareUpgradeJob.firmware_image_id == image_id, FirmwareUpgradeJob.status.in_(ACTIVE_JOB_STATUSES))
        .limit(1)
    )
    if in_progress is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This image is in use by a push job in progress - wait for it to finish before deleting",
        )

    file_path = _org_storage_dir(user.org_id) / image.stored_filename
    file_path.unlink(missing_ok=True)
    await db.delete(image)
    await db.commit()


@router.post("/jobs", response_model=FirmwareJobDetailOut, status_code=status.HTTP_201_CREATED)
async def create_firmware_job(
    payload: FirmwareJobCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareJobDetailOut:
    image = await db.scalar(
        select(FirmwareImage).where(FirmwareImage.id == payload.firmware_image_id, FirmwareImage.org_id == user.org_id)
    )
    if image is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Firmware image not found")

    device_query = (
        select(Device)
        .options(selectinload(Device.credential))
        .where(Device.org_id == user.org_id, Device.id.in_(payload.device_ids))
    )
    devices_by_id = {d.id: d for d in await db.scalars(device_query)}
    if len(devices_by_id) != len(set(payload.device_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more devices not found")
    devices = [devices_by_id[d] for d in payload.device_ids]

    org_default_credential = await db.scalar(
        select(Credential).where(Credential.org_id == user.org_id, Credential.is_default.is_(True))
    )

    def _effective_credential(d: Device) -> Credential | None:
        return d.credential or org_default_credential

    missing_credential = sorted(d.name for d in devices if _effective_credential(d) is None)
    if missing_credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="These devices have no credential assigned and no org-wide default credential is configured: "
            + ", ".join(missing_credential),
        )

    credential_otps = payload.credential_otps or {}
    missing_otp = sorted(
        {
            c.name
            for d in devices
            for c in [_effective_credential(d)]
            if c is not None and c.mfa_mode == MfaMode.PASSCODE and not credential_otps.get(str(c.id))
        }
    )
    if missing_otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A one-time passcode is required for these credentials before the push can start: "
            + ", ".join(missing_otp),
        )

    # A type the user supplied nothing for (or only whitespace) takes the
    # built-in default; the fully resolved map is what's dispatched, so the
    # worker never has to know about defaults.
    supplied = {t: raw for t, raw in (payload.commands_by_device_type or {}).items() if parse_command_list(raw)}
    commands_by_device_type = {
        t: supplied.get(t) or DEFAULT_PUSH_COMMANDS.get(t) for t in sorted({d.device_type for d in devices})
    }
    missing_commands = sorted(t for t, raw in commands_by_device_type.items() if not raw)
    if missing_commands:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No copy command supplied for these device types, and there's no built-in default for them: "
            + ", ".join(missing_commands),
        )
    # Dry-run the substitution now so a typo'd placeholder is a 400 here
    # rather than a command sent verbatim to a device.
    for device_type, raw in commands_by_device_type.items():
        try:
            render_push_commands(
                raw,
                protocol=payload.protocol,
                host=payload.server_host,
                port=transfer_port(payload.protocol),
                filename=image.original_filename,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Copy command for '{device_type}': {exc}",
            ) from exc

    job = FirmwareUpgradeJob(
        org_id=user.org_id,
        created_by_id=user.id,
        firmware_image_id=image.id,
        protocol=payload.protocol,
        status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()

    items = [FirmwareUpgradeJobItem(job_id=job.id, device_id=d.id) for d in devices]
    db.add_all(items)
    await db.commit()
    for item in items:
        await db.refresh(item)

    dispatch_specs = []
    for item, device in zip(items, devices):
        credential = _effective_credential(device)
        otp = credential_otps.get(str(credential.id)) if credential else None
        dispatch_specs.append({"item_id": str(item.id), "otp": otp})

    # Strictly sequential (see tasks.py's firmware_push_device_task) -
    # only the first device is dispatched here; each one dispatches the
    # next only once it's fully finished.
    first, *rest = dispatch_specs
    firmware_push_device_task.apply_async(
        args=[first["item_id"], commands_by_device_type, payload.protocol.value, payload.server_host],
        kwargs={"otp": first["otp"], "remaining": rest},
    )

    return await _fetch_job_detail(db, job.id, user.org_id)


@router.get("/jobs", response_model=list[FirmwareJobOut])
async def list_firmware_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FirmwareJobOut]:
    jobs = list(
        await db.scalars(
            select(FirmwareUpgradeJob)
            .where(FirmwareUpgradeJob.org_id == user.org_id)
            .order_by(FirmwareUpgradeJob.created_at.desc())
        )
    )
    counts = dict(
        (
            await db.execute(
                select(FirmwareUpgradeJobItem.job_id, func.count())
                .where(FirmwareUpgradeJobItem.job_id.in_([j.id for j in jobs]))
                .group_by(FirmwareUpgradeJobItem.job_id)
            )
        ).all()
    )
    return [
        FirmwareJobOut(
            id=j.id,
            firmware_image_id=j.firmware_image_id,
            protocol=j.protocol,
            status=j.status,
            cancel_requested=j.cancel_requested,
            created_at=j.created_at,
            started_at=j.started_at,
            finished_at=j.finished_at,
            item_count=counts.get(j.id, 0),
        )
        for j in jobs
    ]


@router.get("/jobs/{job_id}", response_model=FirmwareJobDetailOut)
async def get_firmware_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareJobDetailOut:
    return await _fetch_job_detail(db, job_id, user.org_id)


@router.post("/jobs/{job_id}/cancel", response_model=FirmwareJobDetailOut)
async def cancel_firmware_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareJobDetailOut:
    job = await db.scalar(select(FirmwareUpgradeJob).where(FirmwareUpgradeJob.id == job_id, FirmwareUpgradeJob.org_id == user.org_id))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status not in ACTIVE_JOB_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This job has already finished")

    job.cancel_requested = True
    await db.execute(
        update(FirmwareUpgradeJobItem)
        .where(FirmwareUpgradeJobItem.job_id == job.id, FirmwareUpgradeJobItem.status == JobStatus.PENDING)
        .values(status=JobStatus.CANCELLED, finished_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return await _fetch_job_detail(db, job_id, user.org_id)


@router.post("/jobs/{job_id}/force-stop", response_model=FirmwareJobDetailOut)
async def force_stop_firmware_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FirmwareJobDetailOut:
    """Same as /api/jobs/{id}/force-stop, for a push job whose worker died
    mid-transfer and will never report back."""
    job = await db.scalar(
        select(FirmwareUpgradeJob).options(selectinload(FirmwareUpgradeJob.items)).where(FirmwareUpgradeJob.id == job_id, FirmwareUpgradeJob.org_id == user.org_id)
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status not in ACTIVE_JOB_STATUSES and not any(i.status in ACTIVE_JOB_STATUSES for i in job.items):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This job has already finished")
    force_stop_job(job, job.items, by=user.email)
    await db.commit()
    return await _fetch_job_detail(db, job_id, user.org_id)


async def _fetch_job_detail(db: AsyncSession, job_id: uuid.UUID, org_id: uuid.UUID) -> FirmwareJobDetailOut:
    # populate_existing=True: without it, the identity map wins even for a
    # fresh select() - an item already loaded earlier in this same session
    # (create_firmware_job constructs/refreshes them before dispatch) keeps
    # its already-loaded column values rather than picking up whatever the
    # dispatched task (running via a separate sync session/connection, and
    # with CELERY_TASK_ALWAYS_EAGER, inline before we ever get here) wrote -
    # see api/jobs.py's _get_owned_job for the same fix on collection jobs.
    job = await db.scalar(
        select(FirmwareUpgradeJob)
        .where(FirmwareUpgradeJob.id == job_id, FirmwareUpgradeJob.org_id == org_id)
        .options(
            selectinload(FirmwareUpgradeJob.items).selectinload(FirmwareUpgradeJobItem.device),
        )
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    items_out = [
        FirmwareJobItemOut(
            id=item.id,
            device_id=item.device_id,
            device_name=item.device.name if item.device else "",
            status=item.status,
            error_message=item.error_message,
            live_output=item.live_output,
            started_at=item.started_at,
            finished_at=item.finished_at,
        )
        for item in job.items
    ]
    return FirmwareJobDetailOut(
        id=job.id,
        firmware_image_id=job.firmware_image_id,
        protocol=job.protocol,
        status=job.status,
        cancel_requested=job.cancel_requested,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        item_count=len(items_out),
        items=items_out,
    )
