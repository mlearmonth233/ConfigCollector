"""Monitor page: continuous ping reachability for every device."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.database import get_db
from app.models.device import Device
from app.models.ping_monitor import PingMonitorConfig, PingSample, PingStatus
from app.models.alerting import AlertSettings
from app.models.user import User
from app.schemas.ping_monitor import (
    PingDeviceOut,
    PingHistoryOut,
    PingMonitorSettingsOut,
    PingMonitorSettingsUpdate,
    PingOverviewOut,
    PingSampleOut,
    PingSummaryOut,
)
from app.services.ping_monitor import MIN_INTERVAL_SECONDS, run_ping_cycle
from app.services import alerting

router = APIRouter(prefix="/api/ping", tags=["ping"])

RECENT_WINDOW = timedelta(hours=1)
RECENT_CAP = 40  # points per sparkline


async def _get_or_create_config(db: AsyncSession, org_id: UUID) -> PingMonitorConfig:
    config = await db.scalar(select(PingMonitorConfig).where(PingMonitorConfig.org_id == org_id))
    if config is None:
        config = PingMonitorConfig(org_id=org_id)
        db.add(config)
        await db.flush()
    return config


async def _email_configured(db: AsyncSession, org_id: UUID) -> bool:
    """True when at least one alert delivery channel is set up (Alerts page)."""
    settings = await db.scalar(select(AlertSettings).where(AlertSettings.org_id == org_id))
    return bool(alerting.channels(settings))


def _settings_out(config: PingMonitorConfig, email_configured: bool) -> PingMonitorSettingsOut:
    return PingMonitorSettingsOut(
        enabled=config.enabled,
        interval_seconds=config.interval_seconds,
        failure_threshold=config.failure_threshold,
        timeout_ms=config.timeout_ms,
        alert_on_down=config.alert_on_down,
        alert_on_up=config.alert_on_up,
        history_days=config.history_days,
        last_run_at=config.last_run_at,
        next_run_at=config.next_run_at,
        last_result=config.last_result,
        email_configured=email_configured,
    )


def _downsample(samples: list[PingSampleOut], cap: int) -> list[PingSampleOut]:
    if len(samples) <= cap:
        return samples
    step = len(samples) / cap
    return [samples[int(i * step)] for i in range(cap)]


async def _overview(db: AsyncSession, org_id: UUID) -> PingOverviewOut:
    config = await _get_or_create_config(db, org_id)
    await db.commit()
    now = datetime.now(timezone.utc)

    devices = list(await db.scalars(select(Device).where(Device.org_id == org_id).order_by(Device.name)))
    statuses = {s.device_id: s for s in await db.scalars(select(PingStatus).where(PingStatus.org_id == org_id))}
    device_ids = [d.id for d in devices]

    # 24h uptime per device in one aggregate query.
    day_ago = now - timedelta(hours=24)
    uptime_rows = {}
    if device_ids:
        rows = await db.execute(
            select(
                PingSample.device_id,
                func.count().label("checks"),
                func.sum(case((PingSample.ok.is_(True), 1), else_=0)).label("oks"),
            )
            .where(PingSample.device_id.in_(device_ids), PingSample.checked_at >= day_ago)
            .group_by(PingSample.device_id)
        )
        uptime_rows = {r.device_id: (int(r.checks), int(r.oks or 0)) for r in rows}

    # Last hour of samples for the sparklines, grouped per device in Python.
    recent: dict[UUID, list[PingSampleOut]] = {d: [] for d in device_ids}
    if device_ids:
        rows = await db.execute(
            select(PingSample.device_id, PingSample.checked_at, PingSample.ok, PingSample.rtt_ms)
            .where(PingSample.device_id.in_(device_ids), PingSample.checked_at >= now - RECENT_WINDOW)
            .order_by(PingSample.checked_at)
        )
        for device_id, checked_at, ok, rtt in rows:
            recent[device_id].append(PingSampleOut(t=checked_at, ok=ok, rtt_ms=rtt))

    out_devices = []
    up = down = unknown = 0
    rtts = []
    for device in devices:
        status_row = statuses.get(device.id)
        state = status_row.state if status_row else "unknown"
        if state == "up":
            up += 1
            if status_row and status_row.last_rtt_ms is not None:
                rtts.append(status_row.last_rtt_ms)
        elif state == "down":
            down += 1
        else:
            unknown += 1
        checks, oks = uptime_rows.get(device.id, (0, 0))
        out_devices.append(
            PingDeviceOut(
                device_id=device.id,
                name=device.name,
                host=device.host,
                site=device.site,
                device_type=device.device_type,
                console=bool(device.console_host),
                state=state,
                consecutive_failures=status_row.consecutive_failures if status_row else 0,
                last_checked_at=status_row.last_checked_at if status_row else None,
                last_change_at=status_row.last_change_at if status_row else None,
                last_rtt_ms=status_row.last_rtt_ms if status_row else None,
                uptime_24h_pct=round(100.0 * oks / checks, 1) if checks else None,
                checks_24h=checks,
                recent=_downsample(recent.get(device.id, []), RECENT_CAP),
            )
        )

    since_last = (now - config.last_run_at).total_seconds() if config.last_run_at else None
    interval = max(MIN_INTERVAL_SECONDS, config.interval_seconds)
    stale = bool(config.enabled and devices and (since_last is None or since_last > interval * 3 + 30))
    summary = PingSummaryOut(
        total=len(devices),
        up=up,
        down=down,
        unknown=unknown,
        avg_rtt_ms=round(sum(rtts) / len(rtts), 1) if rtts else None,
        seconds_since_last_run=since_last,
        stale=stale,
    )
    return PingOverviewOut(settings=_settings_out(config, await _email_configured(db, org_id)), summary=summary, devices=out_devices)


@router.get("/overview", response_model=PingOverviewOut)
async def overview(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PingOverviewOut:
    return await _overview(db, user.org_id)


@router.put("/settings", response_model=PingMonitorSettingsOut)
async def update_settings(
    payload: PingMonitorSettingsUpdate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> PingMonitorSettingsOut:
    config = await _get_or_create_config(db, admin.org_id)
    for field in ("enabled", "interval_seconds", "failure_threshold", "timeout_ms", "alert_on_down", "alert_on_up", "history_days"):
        setattr(config, field, getattr(payload, field))
    if payload.enabled:
        # Run at the next beat tick rather than waiting out the old interval.
        config.next_run_at = datetime.now(timezone.utc)
    await db.commit()
    return _settings_out(config, await _email_configured(db, admin.org_id))


@router.post("/run-now", response_model=PingOverviewOut)
async def run_now(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> PingOverviewOut:
    """One cycle immediately, even if the monitor is paused."""
    config = await _get_or_create_config(db, user.org_id)
    await db.commit()
    await asyncio.to_thread(run_ping_cycle, user.org_id, force=True)
    # The cycle wrote through its own (sync) session; reload what this
    # session already holds so the overview reflects the new run.
    await db.refresh(config)
    return await _overview(db, user.org_id)


@router.get("/history/{device_id}", response_model=PingHistoryOut)
async def history(
    device_id: UUID,
    hours: int = Query(24, ge=1, le=24 * 90),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PingHistoryOut:
    device = await db.scalar(select(Device).where(Device.id == device_id, Device.org_id == user.org_id))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = await db.execute(
        select(PingSample.checked_at, PingSample.ok, PingSample.rtt_ms)
        .where(PingSample.device_id == device_id, PingSample.checked_at >= since)
        .order_by(PingSample.checked_at)
    )
    samples = [PingSampleOut(t=t, ok=ok, rtt_ms=rtt) for t, ok, rtt in rows]
    return PingHistoryOut(device_id=device_id, hours=hours, samples=_downsample(samples, 2000))
