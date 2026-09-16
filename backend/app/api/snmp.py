"""SNMP: profiles (v2c community / v3 user - the SNMP counterpart of
Credential) and poll jobs that gather system details, interface errors and
the device's syslog history over SNMP (see services/snmp_poll.py)."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, require_admin
from app.core.encryption import encrypt_secret
from app.database import get_db
from app.models.device import Device
from app.models.job import ACTIVE_JOB_STATUSES, JobStatus
from app.models.snmp import SnmpJob, SnmpJobItem, SnmpProfile, SnmpSecurityLevel, SnmpVersion
from app.models.alerting import AlertSettings
from app.models.snmp_monitor import SnmpMonitorConfig
from app.models.user import User
from app.schemas.snmp import (
    SnmpJobClearResult,
    SnmpJobCreate,
    SnmpJobDetailOut,
    SnmpJobItemOut,
    SnmpJobOut,
    SnmpProfileCreate,
    SnmpProfileOut,
    SnmpProfileUpdate,
)
from app.schemas.snmp_monitor import SnmpMonitorConfigOut, SnmpMonitorConfigUpdate
from app.services import alerting
from app.services.snmp_poll import SnmpAuth, validate_auth
from app.tasks import run_snmp_job_task, run_snmp_monitor_cycle

router = APIRouter(prefix="/api/snmp", tags=["snmp"])


# --- profiles -----------------------------------------------------------------------


def _to_profile_out(p: SnmpProfile, device_count: int) -> SnmpProfileOut:
    return SnmpProfileOut(
        id=p.id,
        name=p.name,
        is_default=p.is_default,
        version=p.version,
        port=p.port,
        timeout_seconds=p.timeout_seconds,
        retries=p.retries,
        has_community=bool(p.encrypted_community),
        username=p.username,
        security_level=p.security_level,
        auth_protocol=p.auth_protocol,
        has_auth_password=bool(p.encrypted_auth_password),
        priv_protocol=p.priv_protocol,
        has_priv_password=bool(p.encrypted_priv_password),
        context_name=p.context_name,
        device_count=device_count,
        created_at=p.created_at,
    )


def _check_complete(p: SnmpProfile) -> None:
    """400 if the profile as it now stands couldn't be used to poll -
    checked with placeholder secrets since only presence matters here."""
    try:
        validate_auth(
            SnmpAuth(
                version=p.version,
                community="x" if p.encrypted_community else None,
                username=p.username,
                security_level=p.security_level,
                auth_protocol=p.auth_protocol,
                auth_password="x" if p.encrypted_auth_password else None,
                priv_protocol=p.priv_protocol,
                priv_password="x" if p.encrypted_priv_password else None,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _profile_device_counts(db: AsyncSession, org_id) -> dict:
    rows = await db.execute(
        select(Device.snmp_profile_id, func.count())
        .where(Device.org_id == org_id, Device.snmp_profile_id.is_not(None))
        .group_by(Device.snmp_profile_id)
    )
    return dict(rows.all())


async def _get_owned_profile(db: AsyncSession, profile_id: UUID, org_id) -> SnmpProfile:
    p = await db.get(SnmpProfile, profile_id)
    if p is None or p.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SNMP profile not found")
    return p


@router.get("/profiles", response_model=list[SnmpProfileOut])
async def list_profiles(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[SnmpProfileOut]:
    counts = await _profile_device_counts(db, user.org_id)
    rows = await db.scalars(select(SnmpProfile).where(SnmpProfile.org_id == user.org_id).order_by(SnmpProfile.created_at))
    return [_to_profile_out(p, counts.get(p.id, 0)) for p in rows]


@router.post("/profiles", response_model=SnmpProfileOut, status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: SnmpProfileCreate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> SnmpProfileOut:
    p = SnmpProfile(
        org_id=admin.org_id,
        name=payload.name,
        version=payload.version,
        port=payload.port,
        timeout_seconds=payload.timeout_seconds,
        retries=payload.retries,
        context_name=payload.context_name or None,
    )
    if payload.version == SnmpVersion.V2C:
        p.encrypted_community = encrypt_secret(payload.community) if payload.community else None
    else:
        p.username = payload.username
        p.security_level = payload.security_level or SnmpSecurityLevel.NO_AUTH_NO_PRIV
        p.auth_protocol = payload.auth_protocol
        p.encrypted_auth_password = encrypt_secret(payload.auth_password) if payload.auth_password else None
        p.priv_protocol = payload.priv_protocol
        p.encrypted_priv_password = encrypt_secret(payload.priv_password) if payload.priv_password else None
    _check_complete(p)

    existing_count = await db.scalar(select(func.count()).select_from(SnmpProfile).where(SnmpProfile.org_id == admin.org_id))
    if payload.is_default or existing_count == 0:
        await _clear_default(db, admin.org_id)
        p.is_default = True
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _to_profile_out(p, 0)


async def _clear_default(db: AsyncSession, org_id) -> None:
    for other in await db.scalars(select(SnmpProfile).where(SnmpProfile.org_id == org_id, SnmpProfile.is_default.is_(True))):
        other.is_default = False


@router.patch("/profiles/{profile_id}", response_model=SnmpProfileOut)
async def update_profile(
    profile_id: UUID,
    payload: SnmpProfileUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> SnmpProfileOut:
    p = await _get_owned_profile(db, profile_id, admin.org_id)
    updates = payload.model_dump(exclude_unset=True)
    for field in ("name", "version", "port", "timeout_seconds", "retries", "username", "security_level", "auth_protocol", "priv_protocol"):
        if field in updates and updates[field] is not None:
            setattr(p, field, updates[field])
    if "context_name" in updates:
        p.context_name = updates["context_name"] or None
    # Secrets: only replaced when a non-empty value is sent.
    if updates.get("community"):
        p.encrypted_community = encrypt_secret(updates["community"])
    if updates.get("auth_password"):
        p.encrypted_auth_password = encrypt_secret(updates["auth_password"])
    if updates.get("priv_password"):
        p.encrypted_priv_password = encrypt_secret(updates["priv_password"])
    if p.version == SnmpVersion.V3 and p.security_level is None:
        p.security_level = SnmpSecurityLevel.NO_AUTH_NO_PRIV
    _check_complete(p)
    await db.commit()
    await db.refresh(p)
    counts = await _profile_device_counts(db, admin.org_id)
    return _to_profile_out(p, counts.get(p.id, 0))


@router.post("/profiles/{profile_id}/set-default", response_model=SnmpProfileOut)
async def set_default_profile(
    profile_id: UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> SnmpProfileOut:
    p = await _get_owned_profile(db, profile_id, admin.org_id)
    await _clear_default(db, admin.org_id)
    p.is_default = True
    await db.commit()
    await db.refresh(p)
    counts = await _profile_device_counts(db, admin.org_id)
    return _to_profile_out(p, counts.get(p.id, 0))


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> None:
    p = await _get_owned_profile(db, profile_id, admin.org_id)
    # Devices pointing at it fall back to the org default (FK is SET NULL).
    await db.delete(p)
    await db.commit()


# --- jobs ---------------------------------------------------------------------------


@router.post("/jobs", response_model=SnmpJobDetailOut, status_code=status.HTTP_201_CREATED)
async def create_snmp_job(
    payload: SnmpJobCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> SnmpJobDetailOut:
    devices_by_id = {
        d.id: d
        for d in await db.scalars(select(Device).where(Device.org_id == user.org_id, Device.id.in_(payload.device_ids)))
    }
    if len(devices_by_id) != len(set(payload.device_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more devices not found")
    devices = [devices_by_id[d] for d in dict.fromkeys(payload.device_ids)]

    override = None
    if payload.snmp_profile_id is not None:
        override = await _get_owned_profile(db, payload.snmp_profile_id, user.org_id)
    default_profile = await db.scalar(
        select(SnmpProfile).where(SnmpProfile.org_id == user.org_id, SnmpProfile.is_default.is_(True))
    )
    if override is None:
        missing = sorted(d.name for d in devices if d.snmp_profile_id is None and default_profile is None)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="These devices have no SNMP profile and there's no org default SNMP profile - add one on "
                "the SNMP page or pick a profile for this run: " + ", ".join(missing),
            )

    job = SnmpJob(
        org_id=user.org_id,
        created_by_id=user.id,
        snmp_profile_id=override.id if override else None,
        extra_oids=", ".join(payload.extra_oids) or None,
        status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.flush()
    db.add_all([SnmpJobItem(job_id=job.id, device_id=d.id) for d in devices])
    await db.commit()

    run_snmp_job_task.apply_async(args=[str(job.id)])
    return await fetch_snmp_job_detail(db, job.id, user.org_id)


@router.get("/jobs", response_model=list[SnmpJobOut])
async def list_snmp_jobs(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[SnmpJobOut]:
    jobs = list(
        await db.scalars(select(SnmpJob).where(SnmpJob.org_id == user.org_id).order_by(SnmpJob.created_at.desc()))
    )
    counts = dict(
        (
            await db.execute(
                select(SnmpJobItem.job_id, func.count())
                .where(SnmpJobItem.job_id.in_([j.id for j in jobs]))
                .group_by(SnmpJobItem.job_id)
            )
        ).all()
    )
    return [_to_job_out(j, counts.get(j.id, 0)) for j in jobs]


@router.delete("/jobs", response_model=SnmpJobClearResult)
async def clear_finished_snmp_jobs(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> SnmpJobClearResult:
    jobs = list(
        await db.scalars(
            select(SnmpJob).where(SnmpJob.org_id == user.org_id, SnmpJob.status.not_in(ACTIVE_JOB_STATUSES))
        )
    )
    for job in jobs:
        await db.delete(job)
    await db.commit()
    return SnmpJobClearResult(deleted=len(jobs))


@router.get("/jobs/{job_id}", response_model=SnmpJobDetailOut)
async def get_snmp_job(job_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> SnmpJobDetailOut:
    return await fetch_snmp_job_detail(db, job_id, user.org_id)


@router.post("/jobs/{job_id}/cancel", response_model=SnmpJobDetailOut)
async def cancel_snmp_job(job_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> SnmpJobDetailOut:
    job = await _get_owned_job(db, job_id, user.org_id)
    if job.status not in ACTIVE_JOB_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Job has already finished")
    job.cancel_requested = True
    now = datetime.now(timezone.utc)
    for item in job.items:
        if item.status == JobStatus.PENDING:
            item.status = JobStatus.CANCELLED
            item.finished_at = now
    await db.commit()
    return await fetch_snmp_job_detail(db, job_id, user.org_id)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_snmp_job(job_id: UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> None:
    job = await _get_owned_job(db, job_id, user.org_id)
    if job.status in ACTIVE_JOB_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This job is still in progress - wait for it to finish (or cancel it) before deleting",
        )
    await db.delete(job)
    await db.commit()


async def _get_owned_job(db: AsyncSession, job_id: UUID, org_id) -> SnmpJob:
    # populate_existing: see api/jobs.py's _get_owned_job - the eager task
    # may already have written statuses via its own sync session.
    job = await db.scalar(
        select(SnmpJob)
        .where(SnmpJob.id == job_id)
        .options(selectinload(SnmpJob.items).selectinload(SnmpJobItem.device))
        .execution_options(populate_existing=True)
    )
    if job is None or job.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _to_job_out(job: SnmpJob, item_count: int) -> SnmpJobOut:
    return SnmpJobOut(
        id=job.id,
        status=job.status,
        snmp_profile_id=job.snmp_profile_id,
        extra_oids=[o for o in (job.extra_oids or "").split(", ") if o],
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        item_count=item_count,
    )


async def fetch_snmp_job_detail(db: AsyncSession, job_id: UUID, org_id) -> SnmpJobDetailOut:
    job = await _get_owned_job(db, job_id, org_id)
    items = [
        SnmpJobItemOut(
            id=i.id,
            device_id=i.device_id,
            device_name=i.device.name if i.device else "deleted device",
            device_host=i.device.host if i.device else None,
            profile_name=i.profile_name,
            status=i.status,
            error_message=i.error_message,
            live_output=i.live_output,
            report=i.report,
            started_at=i.started_at,
            finished_at=i.finished_at,
        )
        for i in job.items
    ]
    base = _to_job_out(job, len(items))
    return SnmpJobDetailOut(**base.model_dump(), items=items)


# --- monitoring / alerting -------------------------------------------------------------


async def _get_or_create_config(db: AsyncSession, org_id) -> SnmpMonitorConfig:
    config = await db.scalar(select(SnmpMonitorConfig).where(SnmpMonitorConfig.org_id == org_id))
    if config is None:
        config = SnmpMonitorConfig(org_id=org_id)
        db.add(config)
        await db.flush()
    return config


async def _monitored_count(db: AsyncSession, config: SnmpMonitorConfig) -> int:
    if config.device_ids:
        return len([x for x in config.device_ids.split(",") if x.strip()])
    return await db.scalar(select(func.count()).select_from(Device).where(Device.org_id == config.org_id)) or 0


def _config_out(c: SnmpMonitorConfig, monitored: int, channels_configured: bool) -> SnmpMonitorConfigOut:
    return SnmpMonitorConfigOut(
        enabled=c.enabled,
        interval_minutes=c.interval_minutes,
        device_ids=[UUID(x) for x in c.device_ids.split(",") if x.strip()] if c.device_ids else None,
        snmp_profile_id=c.snmp_profile_id,
        alert_link_down=c.alert_link_down,
        alert_link_up=c.alert_link_up,
        alert_ap_down=c.alert_ap_down,
        alert_ap_up=c.alert_ap_up,
        alert_device_down=c.alert_device_down,
        alert_device_up=c.alert_device_up,
        alert_syslog_max_level=c.alert_syslog_max_level,
        next_run_at=c.next_run_at,
        last_run_at=c.last_run_at,
        last_result=c.last_result,
        monitored_device_count=monitored,
        channels_configured=channels_configured,
    )


async def _channels_configured(db: AsyncSession, org_id: UUID) -> bool:
    settings = await db.scalar(select(AlertSettings).where(AlertSettings.org_id == org_id))
    return bool(alerting.channels(settings))


@router.get("/monitor", response_model=SnmpMonitorConfigOut)
async def get_monitor_config(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> SnmpMonitorConfigOut:
    config = await _get_or_create_config(db, user.org_id)
    await db.commit()
    return _config_out(config, await _monitored_count(db, config), await _channels_configured(db, user.org_id))


@router.put("/monitor", response_model=SnmpMonitorConfigOut)
async def update_monitor_config(
    payload: SnmpMonitorConfigUpdate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> SnmpMonitorConfigOut:
    config = await _get_or_create_config(db, admin.org_id)
    if payload.device_ids:
        count = await db.scalar(
            select(func.count()).select_from(Device).where(Device.org_id == admin.org_id, Device.id.in_(payload.device_ids))
        )
        if count != len(set(payload.device_ids)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="One or more devices not found")
    if payload.snmp_profile_id is not None:
        await _get_owned_profile(db, payload.snmp_profile_id, admin.org_id)

    was_enabled = config.enabled
    for field in (
        "enabled", "interval_minutes", "snmp_profile_id", "alert_link_down", "alert_link_up", "alert_ap_down", "alert_ap_up",
        "alert_device_down", "alert_device_up", "alert_syslog_max_level",
    ):
        setattr(config, field, getattr(payload, field))
    config.device_ids = ",".join(str(d) for d in payload.device_ids) if payload.device_ids else None
    if config.enabled and not was_enabled:
        config.next_run_at = datetime.now(timezone.utc)  # first cycle (the baseline) as soon as beat ticks
    await db.commit()
    await db.refresh(config)
    return _config_out(config, await _monitored_count(db, config), await _channels_configured(db, admin.org_id))


@router.post("/monitor/run-now", response_model=SnmpMonitorConfigOut)
async def run_monitor_now(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> SnmpMonitorConfigOut:
    """Runs one monitoring cycle immediately (even if the monitor is
    disabled) - the first run on a device only records its baseline."""
    await _get_or_create_config(db, admin.org_id)  # make sure the row exists before the cycle reads it
    await db.commit()
    await asyncio.to_thread(run_snmp_monitor_cycle, admin.org_id, force=True)
    refreshed = await db.scalar(
        select(SnmpMonitorConfig).where(SnmpMonitorConfig.org_id == admin.org_id).execution_options(populate_existing=True)
    )
    return _config_out(refreshed, await _monitored_count(db, refreshed), await _channels_configured(db, admin.org_id))
