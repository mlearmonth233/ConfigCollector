import io
import zipfile
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.command_profiles import get_org_command_overrides
from app.api.deps import get_current_user
from app.database import get_db
from app.models.credential import Credential, MfaMode
from app.models.device import Device
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.schedule import Schedule
from app.models.user import User
from app.schemas.job import JobClearResult, JobCreate, JobDetailOut, JobItemOut, JobOut, NeighborGapCheckOut, NeighborGapOut
from app.services.device_types import parse_command_list, resolve_commands
from app.services.filenames import build_snapshot_filename, build_zip_filename, folder_for_device_type
from app.services.neighbor_discovery import extract_neighbors, has_neighbor_command, normalize_device_name
from app.tasks import collect_device_task

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[JobOut]:
    # A plain count, not a full selectinload of every item - the list view
    # only ever needs item_count, and eager-loading the items themselves
    # would pull every device's full live_output transcript along with them
    # just to throw it away and report a number.
    jobs = list(
        await db.scalars(
            select(CollectionJob)
            .where(CollectionJob.org_id == user.org_id)
            .order_by(CollectionJob.created_at.desc())
        )
    )
    counts = dict(
        (
            await db.execute(
                select(CollectionJobItem.job_id, func.count())
                .where(CollectionJobItem.job_id.in_([job.id for job in jobs]))
                .group_by(CollectionJobItem.job_id)
            )
        ).all()
    )
    return [
        JobOut(
            id=job.id,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            item_count=counts.get(job.id, 0),
        )
        for job in jobs
    ]


@router.delete("", response_model=JobClearResult)
async def clear_finished_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobClearResult:
    """Bulk 'clear all old jobs': deletes every job for this org that isn't
    still running (its items/snapshots go with it via cascade - see
    CollectionJob.items and CollectionJobItem.snapshot). A job still in
    progress is left alone rather than erroring, so this is safe to call
    even while something else is collecting."""
    result = await db.scalars(
        select(CollectionJob).where(CollectionJob.org_id == user.org_id, CollectionJob.status != JobStatus.RUNNING)
    )
    jobs = list(result)
    if jobs:
        # A schedule's last_job_id is purely informational (its "last run"
        # link) - it shouldn't keep a finished job from being cleared, so
        # null it out here rather than leaving a dangling reference.
        await db.execute(
            update(Schedule).where(Schedule.last_job_id.in_([job.id for job in jobs])).values(last_job_id=None)
        )
    for job in jobs:
        await db.delete(job)
    await db.commit()
    return JobClearResult(deleted=len(jobs))


@router.post("", response_model=JobDetailOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobDetailOut:
    job = await create_and_dispatch_job(
        db,
        org_id=user.org_id,
        created_by_id=user.id,
        device_ids=payload.device_ids,
        commands_by_device_type=payload.commands_by_device_type,
        credential_otps=payload.credential_otps,
    )
    return await fetch_job_detail(db, job.id, user.org_id)


async def create_and_dispatch_job(
    db: AsyncSession,
    *,
    org_id,
    created_by_id,
    device_ids: list | None,
    commands_by_device_type: dict[str, str] | None = None,
    credential_otps: dict[str, str] | None = None,
) -> CollectionJob:
    """Shared by the POST /api/jobs endpoint and a schedule's "run now"
    action (see api/schedules.py) - everything create_job used to do
    itself, minus turning the result into a response body, so both callers
    get the exact same device/credential/OTP/command validation and
    pipelined dispatch."""
    device_query = (
        select(Device)
        .options(selectinload(Device.credential).selectinload(Credential.fallback_credential))
        .where(Device.org_id == org_id)
    )
    if device_ids:
        device_query = device_query.where(Device.id.in_(device_ids))
        devices_by_id = {d.id: d for d in await db.scalars(device_query)}
        if len(devices_by_id) != len(set(device_ids)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more devices not found")
        # `IN (...)` doesn't preserve the given order (it can come back in
        # primary-key/index order) - devices are collected one at a time in
        # this order (see dispatch below), so it needs to actually match
        # what was requested rather than something incidental.
        devices = [devices_by_id[device_id] for device_id in device_ids]
    else:
        devices = list(await db.scalars(device_query.order_by(Device.created_at)))

    if not devices:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No devices to collect from")

    credential_otps = credential_otps or {}

    # Devices don't need a credential picked per-device anymore - a device
    # with no credential_id of its own uses the org's one default
    # credential instead (see Credential.is_default).
    org_default_credential = await db.scalar(
        select(Credential)
        .options(selectinload(Credential.fallback_credential))
        .where(Credential.org_id == org_id, Credential.is_default.is_(True))
    )

    def _effective_credential(d: Device) -> Credential | None:
        return d.credential or org_default_credential

    missing_credential_devices = sorted(d.name for d in devices if _effective_credential(d) is None)
    if missing_credential_devices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "These devices have no credential assigned and no org-wide default credential is "
                "configured - add a credential on the Credentials page (the first one becomes the "
                "default automatically) or set one as default: " + ", ".join(missing_credential_devices)
            ),
        )

    def _passcode_creds_in_use(d: Device) -> list[Credential]:
        # Which credential ends up authenticating a device (primary or its
        # fallback) isn't known until it's actually contacted, and an OTP
        # can't be requested mid-run - so both must be supplied up front
        # whenever either uses passcode-based MFA.
        credential = _effective_credential(d)
        candidates = [credential, credential.fallback_credential if credential else None]
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

    # An org's saved Commands-page default (per device type) sits between a
    # device's own custom_commands and the hardcoded registry default: it
    # applies whenever a device has no custom_commands of its own and this
    # run doesn't supply a commands_by_device_type override for its type.
    org_overrides = await get_org_command_overrides(db, org_id)
    org_default_commands = {
        device_type: parse_command_list(profile.commands) for device_type, profile in org_overrides.items()
    }

    commands_by_type = commands_by_device_type or {}
    missing_command_devices = sorted(
        d.name
        for d in devices
        if not commands_by_type.get(d.device_type)
        and not _has_resolvable_commands(d.device_type, d.custom_commands, org_default_commands.get(d.device_type))
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
        org_id=org_id,
        created_by_id=created_by_id,
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

    dispatch_specs = []
    for item, device in zip(items, devices):
        raw_override = commands_by_type.get(device.device_type)
        if raw_override:
            commands_override = parse_command_list(raw_override)
        elif not device.custom_commands and device.device_type in org_default_commands:
            commands_override = org_default_commands[device.device_type]
        else:
            commands_override = None
        credential = _effective_credential(device)
        otp = credential_otps.get(str(credential.id)) if credential else None
        fallback = credential.fallback_credential if credential else None
        fallback_otp = credential_otps.get(str(fallback.id)) if fallback else None
        dispatch_specs.append(
            {
                "item_id": str(item.id),
                "commands_override": commands_override,
                "otp": otp,
                "fallback_otp": fallback_otp,
            }
        )

    # Only the first device is dispatched here - each device's task
    # dispatches the next one itself, as soon as it finishes authenticating
    # (see tasks.py's collect_device_task/dispatch_next), so a device's
    # command-running phase overlaps with the next device's authentication
    # phase instead of waiting for it. At most one device is ever
    # authenticating at a time, since the next dispatch only happens once
    # this one stops needing to - a large batch still can't hammer
    # TACACS+/RADIUS or the network with concurrent logins.
    first, *rest = dispatch_specs
    collect_device_task.apply_async(
        args=[first["item_id"]],
        kwargs={
            "commands_override": first["commands_override"],
            "otp": first["otp"],
            "fallback_otp": first["fallback_otp"],
            "remaining": rest,
        },
    )

    # Dispatching may have run synchronously (CELERY_TASK_ALWAYS_EAGER, used
    # in tests/dev without a broker) via a separate sync session - the
    # caller is responsible for re-fetching through its own session with
    # eager-loaded relationships rather than touching potentially-stale
    # lazy attributes on this `job`/`items`.
    return job


@router.post("/{job_id}/cancel", response_model=JobDetailOut)
async def cancel_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobDetailOut:
    """Cancels this job. A device still PENDING (queued behind another one,
    or its task just hasn't been picked up by the worker yet) is marked
    CANCELLED immediately, skipped outright when its turn comes up (see
    tasks.py's terminal-status guard). A device already authenticating or
    mid-command-list can't be interrupted right this instant - a live SSH
    call is opaque until it returns - but cancel_requested is checked
    between each command in that device's list (see collector.py's
    should_cancel), so it stops there rather than only ever finishing the
    entire device untouched."""
    job = await _get_owned_job(db, job_id, user.org_id)
    if job.status != JobStatus.RUNNING:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Job has already finished")

    job.cancel_requested = True
    now = datetime.now(timezone.utc)
    for item in job.items:
        if item.status == JobStatus.PENDING:
            item.status = JobStatus.CANCELLED
            item.finished_at = now
    await db.commit()

    return await fetch_job_detail(db, job_id, user.org_id)


@router.get("/{job_id}", response_model=JobDetailOut)
async def get_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobDetailOut:
    return await fetch_job_detail(db, job_id, user.org_id)


@router.get("/{job_id}/neighbor-gaps", response_model=NeighborGapCheckOut)
async def check_neighbor_gaps(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NeighborGapCheckOut:
    """Best-effort "did we miss any devices?" check: parses each collected
    device's CDP/LLDP neighbor table (only the "detail"-style commands -
    see services/neighbor_discovery.py) and flags any neighbor whose name
    doesn't match an existing device in this org, wherever it was
    discovered from. This is a discovery aid, not an authoritative
    inventory - a name mismatch (abbreviated vs. FQDN, a typo) can produce
    a false positive, and a device type with no "detail"-style neighbor
    command in its command list won't surface anything here at all."""
    job = await _get_owned_job(db, job_id, user.org_id)

    known_names = {
        normalize_device_name(name)
        for name in await db.scalars(select(Device.name).where(Device.org_id == user.org_id))
    }

    checked_item_count = 0
    # Keyed by normalized name so the same neighbor reported by more than
    # one source device (or by both CDP and LLDP) collapses into one entry.
    missing: dict[str, NeighborGapOut] = {}
    for item in job.items:
        if item.snapshot is None:
            continue
        content = item.snapshot.content
        if has_neighbor_command(content):
            checked_item_count += 1
        source_name = item.device.name if item.device else "deleted-device"
        for neighbor in extract_neighbors(content):
            normalized = normalize_device_name(neighbor.name)
            if normalized in known_names:
                continue
            existing = missing.get(normalized)
            if existing is None:
                missing[normalized] = NeighborGapOut(
                    name=neighbor.name,
                    ip=neighbor.ip,
                    protocols=[neighbor.protocol],
                    seen_from=[source_name],
                )
            else:
                if neighbor.protocol not in existing.protocols:
                    existing.protocols.append(neighbor.protocol)
                if source_name not in existing.seen_from:
                    existing.seen_from.append(source_name)
                if existing.ip is None and neighbor.ip is not None:
                    existing.ip = neighbor.ip

    return NeighborGapCheckOut(
        checked_item_count=checked_item_count,
        missing=sorted(missing.values(), key=lambda n: n.name.lower()),
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    job = await _get_owned_job(db, job_id, user.org_id)
    if job.status == JobStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This job is still in progress - wait for it to finish (or cancel it) before deleting",
        )
    await db.execute(update(Schedule).where(Schedule.last_job_id == job.id).values(last_job_id=None))
    await db.delete(job)
    await db.commit()


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
    used_paths: dict[str, int] = {}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in completed_items:
            device_name = item.device.name if item.device else "deleted-device"
            device_type = item.device.device_type if item.device else None
            filename = build_snapshot_filename(
                device_name,
                collected_at=item.snapshot.collected_at,
                ext=ext,
                include_timestamp=include_timestamp,
            )
            path = f"{folder_for_device_type(device_type)}/{filename}"
            if path in used_paths:
                # Two devices sharing a sanitized name (or a re-run with no
                # timestamp) would otherwise silently clobber one entry.
                used_paths[path] += 1
                stem, _, suffix = path.rpartition(".")
                path = f"{stem}_{used_paths[path]}.{suffix}"
            else:
                used_paths[path] = 0
            zf.writestr(path, item.snapshot.content)

    zip_filename = build_zip_filename(dated_at=job.finished_at or job.created_at)
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


async def fetch_job_detail(db: AsyncSession, job_id: UUID, org_id: UUID) -> JobDetailOut:
    job = await _get_owned_job(db, job_id, org_id)
    return _to_job_detail_out(job)


def _has_resolvable_commands(
    device_type: str, custom_commands: str | None, org_default: list[str] | None
) -> bool:
    if custom_commands or org_default:
        return True
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
