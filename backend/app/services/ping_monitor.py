"""Continuous reachability monitoring: pings every device on a timer, keeps
a short history per device, and flips each device between 'up' and 'down'
with a consecutive-failure threshold so a single lost packet is not an
outage.

A device that has never answered starts as 'unknown' and moves to 'up' on
its first reply or to 'down' once it has missed `failure_threshold` checks
in a row - neither of those first transitions alerts (there is no known-
good state to compare with). After that, up->down raises PING_DOWN and
down->up raises PING_UP, both recorded as SnmpAlert rows (the org's single
alert stream) and delivered through the org's AlertSettings channels
(services/alerting.py).

Ping itself is the OS `ping` binary, one packet, run from a worker thread
(see services/reachability.py for why a subprocess in a thread rather than
asyncio's own subprocess support). The round-trip time is parsed from its
output so the page can show latency, not just up/down.
"""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db_sync import SyncSessionLocal
from app.services.reachability import hidden_subprocess_kwargs
from app.models.device import Device
from app.models.organization import Organization
from app.models.ping_monitor import PingMonitorConfig, PingSample, PingStatus
from app.models.snmp_monitor import SnmpAlert, SnmpAlertKind
from app.services import alerting
from app.services.snmp_monitor import Event

log = logging.getLogger(__name__)

CONCURRENCY = 32
MIN_INTERVAL_SECONDS = 5  # the beat task that drives cycles ticks this often too (celery_app.py)
STATE_UP, STATE_DOWN, STATE_UNKNOWN = "up", "down", "unknown"

_RTT_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)


def _ping_args(host: str, timeout_ms: int) -> list[str]:
    if platform.system() == "Windows":
        return ["ping", "-n", "1", "-w", str(timeout_ms), host]
    # -W is whole seconds on Linux (and BSD/macOS accepts it too); round up.
    return ["ping", "-c", "1", "-W", str(max(1, -(-timeout_ms // 1000))), host]


def _ping_blocking(host: str, timeout_ms: int) -> float | None:
    """One ping. Returns the round-trip time in ms, or None if no reply
    (or ping could not run at all - treated as unreachable, never raised)."""
    started = time.perf_counter()
    try:
        result = subprocess.run(
            _ping_args(host, timeout_ms),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout_ms / 1000 + 2,
            **hidden_subprocess_kwargs(),  # no console window per ping on Windows
        )
    except Exception:  # noqa: BLE001 - missing binary, timeout, permissions: all "no reply"
        return None
    if result.returncode != 0:
        return None
    match = _RTT_RE.search(result.stdout)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return round((time.perf_counter() - started) * 1000, 1)


async def ping_rtt(host: str, timeout_ms: int = 1500) -> float | None:
    return await asyncio.to_thread(_ping_blocking, host, timeout_ms)


def _run_in_new_thread(coro_factory):
    """asyncio.run() in a fresh thread, so this works whether the caller is a
    plain Celery worker thread or already inside an event loop (eager mode
    under the API)."""
    result: dict = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def get_or_create_config(db, org_id) -> PingMonitorConfig:
    config = db.query(PingMonitorConfig).filter(PingMonitorConfig.org_id == org_id).first()
    if config is None:
        config = PingMonitorConfig(org_id=org_id)
        db.add(config)
        db.flush()
    return config


def apply_result(status: PingStatus, ok: bool, rtt_ms: float | None, threshold: int, now: datetime) -> str | None:
    """Updates one device's status from a check. Returns 'down' or 'up' when
    the device just crossed into that state from the opposite known state
    (an alertable event), otherwise None."""
    previous = status.state
    status.last_checked_at = now
    if ok:
        status.consecutive_failures = 0
        status.last_rtt_ms = rtt_ms
        if previous != STATE_UP:
            status.state = STATE_UP
            status.last_change_at = now
            return STATE_UP if previous == STATE_DOWN else None
        return None
    status.consecutive_failures += 1
    status.last_rtt_ms = None
    if previous != STATE_DOWN and status.consecutive_failures >= threshold:
        status.state = STATE_DOWN
        status.last_change_at = now
        return STATE_DOWN if previous == STATE_UP else None
    return None


def run_ping_cycle(org_id, *, force: bool = False) -> dict:
    """One monitoring cycle for an org: ping every device, record samples,
    update statuses, raise and email alerts, purge old history, and set
    next_run_at. `force` runs even when the monitor is disabled ("Check
    now" in the UI)."""
    db = SyncSessionLocal()
    try:
        config = get_or_create_config(db, org_id)
        if not config.enabled and not force:
            return {"skipped": True}
        now = datetime.now(timezone.utc)
        config.last_run_at = now
        config.next_run_at = now + timedelta(seconds=max(MIN_INTERVAL_SECONDS, config.interval_seconds))
        db.commit()

        devices = db.query(Device).filter(Device.org_id == org_id).order_by(Device.name).all()
        statuses = {s.device_id: s for s in db.query(PingStatus).filter(PingStatus.org_id == org_id).all()}
        for device in devices:
            if device.id not in statuses:
                status = PingStatus(org_id=org_id, device_id=device.id, state=STATE_UNKNOWN)
                db.add(status)
                statuses[device.id] = status
        # Devices deleted since the last cycle leave their status behind
        # (SQLite doesn't enforce the FK cascade by default).
        device_ids = {d.id for d in devices}
        for stale_id in [k for k in statuses if k not in device_ids]:
            db.delete(statuses.pop(stale_id))
        db.flush()

        timeout_ms = config.timeout_ms

        async def _ping_all() -> list[float | None]:
            semaphore = asyncio.Semaphore(CONCURRENCY)

            async def one(host: str) -> float | None:
                async with semaphore:
                    return await ping_rtt(host, timeout_ms)

            return await asyncio.gather(*(one(d.host) for d in devices))

        results = _run_in_new_thread(_ping_all) if devices else []

        checked_at = datetime.now(timezone.utc)
        events: list[tuple[str, Event, Device]] = []
        up = down = 0
        for device, rtt in zip(devices, results):
            ok = rtt is not None
            db.add(PingSample(device_id=device.id, checked_at=checked_at, ok=ok, rtt_ms=rtt))
            transition = apply_result(statuses[device.id], ok, rtt, config.failure_threshold, checked_at)
            if statuses[device.id].state == STATE_UP:
                up += 1
            elif statuses[device.id].state == STATE_DOWN:
                down += 1
            if transition == STATE_DOWN and config.alert_on_down:
                events.append((device.name, Event(SnmpAlertKind.PING_DOWN, device.name, f"{device.name} ({device.host}) stopped answering ping after {config.failure_threshold} consecutive checks"), device))
            elif transition == STATE_UP and config.alert_on_up:
                events.append((device.name, Event(SnmpAlertKind.PING_UP, device.name, f"{device.name} ({device.host}) is answering ping again"), device))

        alerts = [
            SnmpAlert(org_id=org_id, device_id=device.id, device_name=name, kind=ev.kind, subject=ev.subject[:255], detail=ev.detail)
            for name, ev, device in events
        ]
        db.add_all(alerts)
        for _, ev, device in events:
            log.warning("Ping alert: %s - %s", ev.kind.value, ev.detail)

        summary = f"Pinged {len(devices)} device(s): {up} up, {down} down"
        if alerts:
            summary += f", {len(alerts)} alert(s)"
            summary += alerting.dispatch(db, org_id, [(n, e) for n, e, _ in events], alerts)

        cutoff = now - timedelta(days=max(1, config.history_days))
        db.execute(delete(PingSample).where(PingSample.checked_at < cutoff))

        config.last_result = summary
        db.commit()
        log.log(logging.WARNING if alerts else logging.INFO, "Ping monitor cycle for org %s: %s", org_id, summary)
        return {"devices": len(devices), "up": up, "down": down, "alerts": len(alerts), "summary": summary}
    finally:
        db.close()


def due_org_ids() -> list:
    """Orgs whose monitor is enabled and due (or never run)."""
    now = datetime.now(timezone.utc)
    db = SyncSessionLocal()
    try:
        rows = db.execute(
            select(PingMonitorConfig.org_id).where(
                PingMonitorConfig.enabled.is_(True),
                (PingMonitorConfig.next_run_at.is_(None)) | (PingMonitorConfig.next_run_at <= now),
            )
        ).scalars().all()
        # Orgs that have never opened the Monitor page have no config row yet;
        # monitoring is on by default, so give them one.
        configured = set(db.execute(select(PingMonitorConfig.org_id)).scalars().all())
        missing = [oid for oid in db.execute(select(Organization.id)).scalars().all() if oid not in configured]
        for org_id in missing:
            db.add(PingMonitorConfig(org_id=org_id))
        if missing:
            db.commit()
        return list(rows) + list(missing)
    finally:
        db.close()
