import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.celery_app import celery_app
from app.core.encryption import decrypt_secret
from app.db_sync import SyncSessionLocal
from app.models.command_profile import CommandProfile
from app.models.credential import Credential
from app.models.device import Device
from app.models.job import ACTIVE_JOB_STATUSES, CollectionJob, CollectionJobItem, JobStatus
from app.models.organization import Organization
from app.models.schedule import Schedule, ScheduleFrequency
from app.models.snapshot import ConfigSnapshot
from app.services.collector import (
    AuthenticationError,
    CollectionCancelled,
    CollectionError,
    EnableModeError,
    collect_device_config,
)
from app.services.device_types import parse_command_list


@celery_app.task(name="app.tasks.collect_device_task", bind=True, max_retries=0)
def collect_device_task(
    self,
    job_item_id: str,
    commands_override: list[str] | None = None,
    otp: str | None = None,
    fallback_otp: str | None = None,
    remaining: list[dict] | None = None,
) -> None:
    # `remaining` is the rest of this job's queue: dicts of
    # {item_id, commands_override, otp, fallback_otp} for every device still
    # to come, in order. Rather than a Celery chain (which only dispatches
    # the next device once this one has *fully* finished), the next device
    # is dispatched as soon as *this* device finishes authenticating - see
    # dispatch_next() below - so a device's (usually fast) command-running
    # phase overlaps with the next device's (usually slow, TACACS+/RADIUS-
    # bound) authentication phase. At most one device is ever
    # authenticating at a time, since the next dispatch only ever happens
    # once this one stops needing to.
    remaining = remaining or []
    dispatched_next = False

    def dispatch_next() -> None:
        nonlocal dispatched_next
        if dispatched_next or not remaining:
            return
        dispatched_next = True
        next_device, *rest = remaining
        collect_device_task.apply_async(
            args=[next_device["item_id"]],
            kwargs={
                "commands_override": next_device["commands_override"],
                "otp": next_device["otp"],
                "fallback_otp": next_device["fallback_otp"],
                "remaining": rest,
            },
        )

    db = SyncSessionLocal()
    try:
        item = db.get(CollectionJobItem, job_item_id)
        if item is None:
            dispatch_next()
            return

        if item.status == JobStatus.CANCELLED:
            # Cancelled (by the user, while this device was still queued
            # behind another one - see jobs.py's cancel_job) before this
            # task even started. Don't resurrect it back to AUTHENTICATING -
            # just pass the baton on so the rest of the chain still gets a
            # chance to run (or, if they're cancelled too, to skip in turn).
            dispatch_next()
            _finalize_job_if_done(db, item.job_id)
            return

        try:
            _collect_one_device(db, item, commands_override, otp, fallback_otp, dispatch_next)
        except Exception as exc:  # noqa: BLE001
            # An exception escaping here would otherwise mean dispatch_next()
            # was never called at all (it's only invoked from within
            # _collect_one_device, on a successful authentication) - every
            # device still queued behind this one would then never even be
            # attempted. A truly unexpected error (as opposed to a normal
            # auth/command failure, which _collect_one_device already turns
            # into a FAILED item without raising) must still just fail this
            # one item and let the queue continue.
            db.rollback()
            item.status = JobStatus.FAILED
            item.error_message = f"Unexpected error during collection: {exc}"
            item.finished_at = datetime.now(timezone.utc)
            db.commit()

        # No-op if authentication already triggered it above - this only
        # does anything when this device never got past authenticating (no
        # credential assigned, auth failed with no/failed fallback, or the
        # unexpected-error path just above), so the queue still advances.
        dispatch_next()

        _finalize_job_if_done(db, item.job_id)
    finally:
        db.close()


def _resolve_credential(db, device: Device) -> Credential | None:
    """A device's own credential_id, if it has one, otherwise the org's one
    default credential (Credential.is_default) - devices no longer need a
    credential picked per-device, so this is the common case."""
    if device.credential is not None:
        return device.credential
    return db.query(Credential).filter(Credential.org_id == device.org_id, Credential.is_default.is_(True)).first()


def _collect_one_device(
    db,
    item: CollectionJobItem,
    commands_override: list[str] | None,
    otp: str | None,
    fallback_otp: str | None,
    dispatch_next,
) -> None:
    # Queued devices sit here until it's their turn; once dequeued, the
    # first phase is authenticating (SSH + TACACS+/RADIUS + any MFA) -
    # no config commands are sent until that succeeds.
    item.status = JobStatus.AUTHENTICATING
    item.started_at = datetime.now(timezone.utc)
    db.commit()

    device = item.device
    credential = _resolve_credential(db, device)

    if credential is None:
        item.status = JobStatus.FAILED
        item.error_message = "Device has no credential assigned"
    else:
        on_authenticated = _make_authenticated_callback(db, item, dispatch_next)
        on_output = _make_output_callback(db, item)
        should_cancel = _make_should_cancel(db, item.job_id)
        try:
            try:
                content = _attempt_collection(
                    device, credential, otp, commands_override, on_authenticated, on_output, should_cancel
                )
                used_fallback = False
            except (AuthenticationError, EnableModeError) as primary_exc:
                fallback = credential.fallback_credential
                if fallback is None:
                    raise
                # Reset to AUTHENTICATING for the fallback attempt. For a
                # plain AuthenticationError the callback can't have flipped
                # this to RUNNING yet (login itself failed); for an
                # EnableModeError it genuinely was RUNNING already (login
                # succeeded, only enable mode failed afterward) - either
                # way, retrying with a full alternate credential starts
                # over from authenticating.
                item.status = JobStatus.AUTHENTICATING
                db.commit()
                on_output(
                    f"\nPrimary credential '{credential.name}' failed: {primary_exc}\n"
                    f"Trying fallback credential '{fallback.name}'...\n"
                )
                try:
                    content = _attempt_collection(
                        device, fallback, fallback_otp, commands_override, on_authenticated, on_output, should_cancel
                    )
                    used_fallback = True
                except CollectionError as fallback_exc:
                    raise CollectionError(
                        f"Primary credential '{credential.name}' failed: {primary_exc} | "
                        f"Fallback credential '{fallback.name}' also failed: {fallback_exc}"
                    ) from fallback_exc

            db.add(ConfigSnapshot(device_id=device.id, job_item_id=item.id, content=content))
            item.status = JobStatus.COMPLETED
            item.used_fallback_credential = used_fallback
        except CollectionCancelled:
            # should_cancel() already noticed this mid-command-list and
            # collector.py already narrated it into live_output - nothing
            # more to record than the outcome itself.
            item.status = JobStatus.CANCELLED
        except CollectionError as exc:
            item.status = JobStatus.FAILED
            item.error_message = str(exc)
            on_output(f"\nERROR: {exc}\n")

    item.finished_at = datetime.now(timezone.utc)
    db.commit()


def _attempt_collection(
    device: Device,
    credential: Credential,
    otp: str | None,
    commands_override: list[str] | None,
    on_authenticated,
    on_output,
    should_cancel,
) -> str:
    """One connection attempt with one credential. Raises AuthenticationError
    if login itself fails, EnableModeError if login succeeds but entering
    enable mode doesn't, or a plain CommandExecutionError if login and
    enable both succeed but running commands doesn't - callers retry with a
    fallback credential for the first two, never the last. Raises
    CollectionCancelled instead of any of those if should_cancel() notices a
    cancel request before running a command."""
    password = decrypt_secret(credential.encrypted_password)
    secret = decrypt_secret(credential.encrypted_enable_secret) if credential.encrypted_enable_secret else None
    return collect_device_config(
        host=device.host,
        port=device.port,
        device_type=device.device_type,
        username=credential.username,
        password=password,
        secret=secret,
        custom_commands=device.custom_commands,
        commands_override=commands_override,
        auth_timeout=credential.auth_timeout_seconds,
        mfa_mode=credential.mfa_mode.value,
        otp=otp,
        otp_delimiter=credential.otp_delimiter,
        on_authenticated=on_authenticated,
        on_output=on_output,
        should_cancel=should_cancel,
    )


def _make_authenticated_callback(db, item: CollectionJobItem, dispatch_next):
    """Flips an item from AUTHENTICATING to RUNNING the instant its device
    login succeeds, and dispatches the next queued device at that same
    moment (see collect_device_task) so it can start authenticating while
    this device's commands are still running. Swallows its own errors for
    the status update - a hiccup persisting this transitional status
    shouldn't be mistaken for (or block) the actual device collection
    outcome, which is recorded independently below."""

    def _on_authenticated() -> None:
        try:
            item.status = JobStatus.RUNNING
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        dispatch_next()

    return _on_authenticated


def _make_should_cancel(db, job_id):
    """A fresh read of CollectionJob.cancel_requested every time it's
    called (never cached) - the whole point is noticing a cancel request
    made by a concurrent request (jobs.py's cancel_job) while this device
    is mid-command-list. Defensively swallows its own errors (a hiccup
    checking this shouldn't be mistaken for a real cancellation, or crash a
    collection that would otherwise have succeeded)."""

    def _should_cancel() -> bool:
        try:
            return bool(db.execute(select(CollectionJob.cancel_requested).where(CollectionJob.id == job_id)).scalar())
        except Exception:  # noqa: BLE001
            return False

    return _should_cancel


def _make_output_callback(db, item: CollectionJobItem):
    """Appends to an item's live transcript as collection progresses.
    Swallows its own errors for the same reason _make_authenticated_callback
    does - this is a progress side-effect, not the recorded outcome."""

    def _on_output(text: str) -> None:
        try:
            item.live_output = (item.live_output or "") + text
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    return _on_output


def _finalize_job_if_done(db, job_id) -> None:
    job = db.get(CollectionJob, job_id)
    if job is None:
        return

    # Just the status column - this runs once per device finishing, so for
    # an N-device job it would otherwise pull N full item rows (including
    # each device's live_output transcript, which can run to tens of KB) N
    # times, an O(n^2) cost in bytes transferred that only gets worse as
    # job size grows.
    statuses = [s for (s,) in db.query(CollectionJobItem.status).filter(CollectionJobItem.job_id == job.id).all()]
    if any(s in ACTIVE_JOB_STATUSES for s in statuses):
        return

    # CANCELLED takes priority over FAILED: it's the most relevant top-level
    # fact once the user has stepped in, even if another device happened to
    # fail on its own before/after the cancel request.
    if any(s == JobStatus.CANCELLED for s in statuses):
        job.status = JobStatus.CANCELLED
    elif any(s == JobStatus.FAILED for s in statuses):
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.COMPLETED
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


def _parse_schedule_device_ids(raw: str | None) -> list[uuid.UUID] | None:
    if not raw:
        return None
    return [uuid.UUID(x) for x in raw.split(",") if x.strip()]


def _compute_next_run_at(
    frequency: ScheduleFrequency,
    interval_hours: int | None,
    run_at_hour: int | None,
    run_at_minute: int | None,
    *,
    after: datetime,
) -> datetime:
    # Kept in sync with (but not imported from) api/schedules.py's identical
    # helper - that module imports from this one (collect_device_task), so
    # importing back here would be circular. Both are tiny, pure, and
    # covered by their own tests.
    if frequency == ScheduleFrequency.EVERY_N_HOURS:
        assert interval_hours is not None
        return after + timedelta(hours=interval_hours)
    assert run_at_hour is not None and run_at_minute is not None
    candidate = after.replace(hour=run_at_hour, minute=run_at_minute, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    return candidate


@celery_app.task(name="app.tasks.run_due_schedules")
def run_due_schedules() -> None:
    """Invoked periodically by Celery beat (see celery_app.py's
    beat_schedule) - every enabled Schedule whose next_run_at has passed
    gets its own ordinary CollectionJob, dispatched the exact same
    pipelined way as a manual one (see collect_device_task), so it shows
    up in the Jobs page like any other. Unattended, so a device whose
    effective credential needs a one-time passcode just fails that one
    item (no human is present to supply one) rather than blocking the
    rest of the schedule's devices."""
    db = SyncSessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due = db.query(Schedule).filter(Schedule.enabled.is_(True), Schedule.next_run_at <= now).all()
        for schedule in due:
            try:
                _run_one_schedule(db, schedule, now)
            except Exception:  # noqa: BLE001
                # One schedule's failure (a DB hiccup, an unreachable
                # broker) shouldn't stop the others due on this same tick,
                # or get stuck retrying the same failure forever - it's
                # still advanced to its next occurrence.
                db.rollback()
                schedule.next_run_at = _compute_next_run_at(
                    schedule.frequency, schedule.interval_hours, schedule.run_at_hour, schedule.run_at_minute,
                    after=now,
                )
                db.commit()
    finally:
        db.close()


def _run_one_schedule(db, schedule: Schedule, now: datetime) -> None:
    device_ids = _parse_schedule_device_ids(schedule.device_ids)
    device_query = db.query(Device).filter(Device.org_id == schedule.org_id)
    if device_ids:
        device_query = device_query.filter(Device.id.in_(device_ids))
    devices = device_query.order_by(Device.created_at).all()

    if devices:
        job = CollectionJob(
            org_id=schedule.org_id,
            created_by_id=schedule.created_by_id,
            status=JobStatus.RUNNING,
            started_at=now,
        )
        db.add(job)
        db.flush()

        items = [CollectionJobItem(job_id=job.id, device_id=d.id) for d in devices]
        db.add_all(items)
        db.commit()
        for item in items:
            db.refresh(item)

        # Same org-saved-default-per-device-type behavior as a manual run
        # (see api/jobs.py's create_and_dispatch_job) - a schedule has no
        # per-run command override of its own to offer.
        org_overrides = {
            row.device_type: parse_command_list(row.commands)
            for row in db.query(CommandProfile).filter(CommandProfile.org_id == schedule.org_id).all()
        }

        dispatch_specs = []
        for item, device in zip(items, devices):
            commands_override = None
            if not device.custom_commands and device.device_type in org_overrides:
                commands_override = org_overrides[device.device_type]
            dispatch_specs.append(
                {
                    "item_id": str(item.id),
                    "commands_override": commands_override,
                    # No human is present to supply a one-time passcode for
                    # an unattended run - a device whose credential needs
                    # one just fails with that clear message instead.
                    "otp": None,
                    "fallback_otp": None,
                }
            )

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

        schedule.last_run_at = now
        schedule.last_job_id = job.id

    schedule.next_run_at = _compute_next_run_at(
        schedule.frequency, schedule.interval_hours, schedule.run_at_hour, schedule.run_at_minute, after=now
    )
    db.commit()


@celery_app.task(name="app.tasks.purge_expired_snapshots")
def purge_expired_snapshots() -> None:
    """Invoked periodically by Celery beat - deletes a device's collected
    config snapshots older than its org's snapshot_retention_days (see
    Organization.snapshot_retention_days), for every org that has opted
    into a retention limit. An org with no limit set (the default) is
    skipped entirely - nothing is ever silently purged unless an admin
    explicitly turns this on."""
    db = SyncSessionLocal()
    try:
        now = datetime.now(timezone.utc)
        orgs = db.query(Organization).filter(Organization.snapshot_retention_days.isnot(None)).all()
        for org in orgs:
            cutoff = now - timedelta(days=org.snapshot_retention_days)
            stale_item_ids = (
                db.query(CollectionJobItem.id)
                .join(CollectionJob, CollectionJob.id == CollectionJobItem.job_id)
                .filter(CollectionJob.org_id == org.id)
            )
            db.query(ConfigSnapshot).filter(
                ConfigSnapshot.job_item_id.in_(stale_item_ids),
                ConfigSnapshot.collected_at < cutoff,
            ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
