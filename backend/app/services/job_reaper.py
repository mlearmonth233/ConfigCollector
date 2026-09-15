"""Cleans up jobs that can no longer finish on their own.

A job's status is written by the worker as it goes: PENDING -> AUTHENTICATING
-> RUNNING -> COMPLETED/FAILED per device, and the job itself is finalized
when its last device finishes. If the worker is killed mid-device (Ctrl+C
in the dev window, a reboot, a crash), nothing ever writes the terminal
status: the device sits at "running" forever, the job stays "running",
Cancel can't complete (it relies on the worker noticing), and the device
can't be deleted because of the in-progress guard. The same applies to
eager mode, where the API process is the worker.

Three ways out, all here:

- reap_orphaned_jobs(): on worker start-up (and API start-up in eager
  mode) every unfinished item is interrupted - a fresh worker has nothing
  in flight, so anything still active is a leftover. Skipped when
  REAP_JOBS_ON_START is false (several workers sharing a broker).
- reap_stale_jobs(): run by beat every few minutes; interrupts jobs with
  no sign of life (no row update on the job or any of its items) for
  STALE_JOB_MINUTES. Covers a worker that died and was not restarted.
- force_stop_job(): the "Force stop" button - the user's own escape hatch
  for a job they can see is dead.

An interrupted device is FAILED with an explanatory error_message (so it
shows up alongside real failures and can be retried per device or by
re-running the job); devices that were still queued behind it are
CANCELLED, because the per-job dispatch chain that would have started them
died with the worker.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.db_sync import SyncSessionLocal
from app.models.dns_check import DnsCheckJob, DnsCheckJobItem
from app.models.firmware import FirmwareUpgradeJob, FirmwareUpgradeJobItem
from app.models.job import ACTIVE_JOB_STATUSES, CollectionJob, CollectionJobItem, JobStatus
from app.models.snmp import SnmpJob, SnmpJobItem

log = logging.getLogger(__name__)

IN_FLIGHT = (JobStatus.AUTHENTICATING, JobStatus.RUNNING)

# (job model, item model, human name) for every job family the app has.
JOB_FAMILIES = (
    (CollectionJob, CollectionJobItem, "collection"),
    (FirmwareUpgradeJob, FirmwareUpgradeJobItem, "file push"),
    (DnsCheckJob, DnsCheckJobItem, "DNS check"),
    (SnmpJob, SnmpJobItem, "SNMP poll"),
)


def interrupt_job(job, items, *, reason: str, now: datetime | None = None) -> int:
    """Marks every unfinished item of `job` as interrupted and finalizes the
    job. Pure ORM mutation on already-loaded objects (no commit), so both
    the sync reaper and the async API endpoint can use it. Returns how many
    items were changed."""
    now = now or datetime.now(timezone.utc)
    changed = 0
    for item in items:
        if item.status not in ACTIVE_JOB_STATUSES:
            continue
        if item.status in IN_FLIGHT:
            item.status = JobStatus.FAILED
            message = f"Interrupted: {reason}"
        else:
            item.status = JobStatus.CANCELLED
            message = f"Skipped: {reason}"
        if hasattr(item, "error_message"):
            item.error_message = message
        if hasattr(item, "live_output") and item.live_output is not None:
            item.live_output = (item.live_output or "") + f"\n[{message}]\n"
        item.finished_at = item.finished_at or now
        if item.started_at is None:
            item.started_at = now
        changed += 1

    statuses = [i.status for i in items]
    if any(s in ACTIVE_JOB_STATUSES for s in statuses):
        return changed  # something else is legitimately still going (shouldn't happen here)
    if any(s == JobStatus.CANCELLED for s in statuses):
        job.status = JobStatus.CANCELLED
    elif any(s == JobStatus.FAILED for s in statuses) or not statuses:
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.COMPLETED
    job.finished_at = job.finished_at or now
    if job.started_at is None:
        job.started_at = now
    return changed


def force_stop_job(job, items, *, by: str) -> int:
    """The user's "Force stop": unfinished devices become CANCELLED (that is
    what they asked for), with a note saying who did it."""
    now = datetime.now(timezone.utc)
    changed = 0
    for item in items:
        if item.status in ACTIVE_JOB_STATUSES:
            item.status = JobStatus.CANCELLED
            note = f"Force-stopped by {by} while {item.status.value if isinstance(item.status, JobStatus) else item.status}"
            if hasattr(item, "error_message"):
                item.error_message = f"Force-stopped by {by}"
            if hasattr(item, "live_output") and item.live_output is not None:
                item.live_output = (item.live_output or "") + f"\n[{note}]\n"
            item.finished_at = now
            item.started_at = item.started_at or now
            changed += 1
    job.status = JobStatus.CANCELLED
    job.cancel_requested = True
    job.finished_at = job.finished_at or now
    job.started_at = job.started_at or now
    return changed


def _last_activity(job, items) -> datetime | None:
    stamps = [job.updated_at, job.started_at, job.created_at]
    for item in items:
        stamps.extend((item.updated_at, item.started_at, item.finished_at))
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def _unfinished_jobs(db, job_model, item_model):
    """Jobs that are still active themselves or have any active item."""
    active_item_jobs = select(item_model.job_id).where(item_model.status.in_(ACTIVE_JOB_STATUSES))
    return db.execute(
        select(job_model).where(or_(job_model.status.in_(ACTIVE_JOB_STATUSES), job_model.id.in_(active_item_jobs)))
    ).scalars().all()


def reap_orphaned_jobs(reason: str = "the worker was restarted") -> dict[str, int]:
    """Every unfinished job is a leftover from a previous process: mark them
    all interrupted. Returns {family: jobs interrupted}."""
    summary: dict[str, int] = {}
    db = SyncSessionLocal()
    try:
        for job_model, item_model, name in JOB_FAMILIES:
            count = 0
            for job in _unfinished_jobs(db, job_model, item_model):
                items = db.execute(select(item_model).where(item_model.job_id == job.id)).scalars().all()
                interrupt_job(job, items, reason=reason)
                count += 1
                log.warning("Marked %s job %s as interrupted (%s)", name, job.id, reason)
            if count:
                summary[name] = count
        db.commit()
    finally:
        db.close()
    if summary:
        log.warning("Start-up cleanup: interrupted %s", ", ".join(f"{n} {k} job(s)" for k, n in summary.items()))
    else:
        log.info("Start-up cleanup: no unfinished jobs left behind")
    return summary


def reap_stale_jobs(max_idle: timedelta) -> dict[str, int]:
    """Interrupts unfinished jobs with no row activity for `max_idle`.
    Returns {family: jobs interrupted}."""
    cutoff = datetime.now(timezone.utc) - max_idle
    minutes = int(max_idle.total_seconds() // 60)
    summary: dict[str, int] = {}
    db = SyncSessionLocal()
    try:
        for job_model, item_model, name in JOB_FAMILIES:
            count = 0
            for job in _unfinished_jobs(db, job_model, item_model):
                items = db.execute(select(item_model).where(item_model.job_id == job.id)).scalars().all()
                last = _last_activity(job, items)
                if last is not None and last > cutoff:
                    continue
                interrupt_job(job, items, reason=f"no progress for {minutes} minutes - the worker probably stopped")
                count += 1
                log.warning("Marked stale %s job %s as interrupted (last activity %s)", name, job.id, last)
            if count:
                summary[name] = count
        db.commit()
    finally:
        db.close()
    return summary
