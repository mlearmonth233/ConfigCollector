import asyncio
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import object_session

from app.celery_app import celery_app
from app.config import get_settings
from app.core.encryption import decrypt_secret
from app.db_sync import SyncSessionLocal
from app.models.command_profile import CommandProfile
from app.models.credential import Credential
from app.models.custom_device_type import CustomDeviceType
from app.models.device import Device
from app.models.dns_check import DnsCheckJob, DnsCheckJobItem
from app.models.firmware import FirmwareUpgradeJob, FirmwareUpgradeJobItem, TransferProtocol
from app.models.job import ACTIVE_JOB_STATUSES, CollectionJob, CollectionJobItem, JobStatus
from app.models.organization import Organization
from app.models.schedule import Schedule
from app.models.snapshot import ConfigSnapshot
from app.models.snmp import SnmpJob, SnmpJobItem, SnmpProfile
from app.models.snmp_monitor import SnmpAlert, SnmpMonitorConfig, SnmpMonitorState
from app.services.collector import (
    AuthenticationError,
    CollectionCancelled,
    CollectionError,
    EnableModeError,
    collect_device_config,
)
from app.services.device_types import DeviceTypeSpec, build_catalog, parse_command_list
from app.services.dns_check import CONCURRENCY as DNS_CHECK_CHUNK_SIZE
from app.services.dns_check import run_dns_checks
from app.services.firmware_push import push_file, render_push_commands, transfer_port
from app.services.scheduling import ScheduleTiming, compute_next_run_at
from app.services.snmp_poll import CONCURRENCY as SNMP_CHUNK_SIZE
from app.services.snmp_poll import SnmpAuth, SnmpError, format_report, poll_many
from app.services import snmp_monitor
from app.services.snmp_monitor import EventFilter, SmtpSettings, Snapshot, diff_snapshots, format_email, parse_recipients
from app.services.transfer_servers import serve_file

settings = get_settings()
log = logging.getLogger(__name__)


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
            log.exception("Unexpected error collecting job item %s", job_item_id)
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


def _resolve_spec(device: Device) -> DeviceTypeSpec:
    """The DeviceTypeSpec for a device - a built-in type or one of its
    org's custom types (see models/custom_device_type.py), looked up through
    the session the device was loaded from. Raises CollectionError for a
    type that no longer exists (a custom type deleted after the device was
    created), so the item fails cleanly instead of crashing the task."""
    db = object_session(device)
    custom = db.query(CustomDeviceType).filter(CustomDeviceType.org_id == device.org_id).all() if db else []
    catalog = build_catalog(custom)
    spec = catalog.get(device.device_type)
    if spec is None:
        raise CollectionError(
            f"Device type '{device.device_type}' no longer exists - it may have been deleted from custom "
            "device types. Edit the device and pick a current type."
        )
    return spec


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
        log.warning("Collection of %s (%s) skipped: device has no credential and the org has no default", device.name, device.host)
        item.status = JobStatus.FAILED
        item.error_message = "Device has no credential assigned"
    else:
        log.info("Collecting %s (%s, %s) for job %s with credential '%s'", device.name, device.host, device.device_type, item.job_id, credential.name)
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
                log.warning(
                    "%s (%s): primary credential '%s' failed (%s); trying fallback '%s'",
                    device.name, device.host, credential.name, primary_exc, fallback.name,
                )
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
            log.info(
                "Collected %s (%s): %d chars%s", device.name, device.host, len(content), " using fallback credential" if used_fallback else ""
            )
        except CollectionCancelled:
            # should_cancel() already noticed this mid-command-list and
            # collector.py already narrated it into live_output - nothing
            # more to record than the outcome itself.
            item.status = JobStatus.CANCELLED
            log.info("Collection of %s (%s) cancelled", device.name, device.host)
        except CollectionError as exc:
            item.status = JobStatus.FAILED
            item.error_message = str(exc)
            log.warning("Collection of %s (%s) FAILED: %s", device.name, device.host, exc)
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
    spec = _resolve_spec(device)
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
        spec=spec,
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


@celery_app.task(name="app.tasks.firmware_push_device_task", bind=True, max_retries=0)
def firmware_push_device_task(
    self,
    job_item_id: str,
    commands_by_device_type: dict[str, str],
    protocol: str,
    server_host: str,
    otp: str | None = None,
    remaining: list[dict] | None = None,
) -> None:
    """Pushes the job's firmware image file onto one device's storage - a
    copy only, never an install or reload (see services/firmware_push.py).

    Runs strictly one device at a time - unlike collect_device_task's
    authentication-overlap pipeline, there's no upside here to overlapping
    one device still pulling an image with the next one authenticating,
    and real downside (multiple devices pulling a possibly-large image over
    the same transfer server/network link at once, or a staggered rollout
    the user specifically wanted serialized)."""
    remaining = remaining or []

    def dispatch_next() -> None:
        if not remaining:
            return
        next_item, *rest = remaining
        firmware_push_device_task.apply_async(
            args=[next_item["item_id"], commands_by_device_type, protocol, server_host],
            kwargs={"otp": next_item["otp"], "remaining": rest},
        )

    db = SyncSessionLocal()
    try:
        item = db.get(FirmwareUpgradeJobItem, job_item_id)
        if item is None:
            dispatch_next()
            return

        if item.status == JobStatus.CANCELLED:
            dispatch_next()
            _finalize_firmware_job_if_done(db, item.job_id)
            return

        try:
            _push_to_one_device(db, item, commands_by_device_type, protocol, server_host, otp)
        except Exception as exc:  # noqa: BLE001
            # Same reasoning as collect_device_task's own catch-all: without
            # this, an unexpected error here would silently stop the rest
            # of the queue from ever being attempted.
            db.rollback()
            log.exception("Unexpected error pushing file for job item %s", job_item_id)
            item.status = JobStatus.FAILED
            item.error_message = f"Unexpected error during file push: {exc}"
            item.finished_at = datetime.now(timezone.utc)
            db.commit()

        dispatch_next()
        _finalize_firmware_job_if_done(db, item.job_id)
    finally:
        db.close()


def _run_async_in_new_thread(coro_factory):
    """Runs the coroutine `coro_factory()` produces to completion, on a
    brand-new event loop in a dedicated thread - not just asyncio.run()
    directly, since this task can execute (in Celery's eager mode - used by
    tests, and available in production via CELERY_TASK_ALWAYS_EAGER)
    synchronously inside a caller's *already-running* asyncio loop (the
    async request handler that queued it). asyncio.run() refuses to nest
    inside one of those; a freshly spawned thread never has one, so this
    works the same whether or not the caller does."""
    result: dict = {}

    def _target() -> None:
        try:
            result["value"] = asyncio.run(coro_factory())
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _attempt_push(
    device: Device,
    credential: Credential,
    otp: str | None,
    commands: list[str],
    on_authenticated,
    on_output,
    should_cancel,
) -> str:
    """firmware_push's counterpart to _attempt_collection: one login with
    one credential, then the rendered copy command(s). Same exception
    contract (AuthenticationError / EnableModeError / CommandExecutionError
    / CollectionCancelled) since it runs over the same device session."""
    spec = _resolve_spec(device)
    password = decrypt_secret(credential.encrypted_password)
    secret = decrypt_secret(credential.encrypted_enable_secret) if credential.encrypted_enable_secret else None
    return push_file(
        host=device.host,
        port=device.port,
        device_type=device.device_type,
        username=credential.username,
        password=password,
        secret=secret,
        auth_timeout=credential.auth_timeout_seconds,
        commands=commands,
        mfa_mode=credential.mfa_mode.value,
        otp=otp,
        otp_delimiter=credential.otp_delimiter,
        on_authenticated=on_authenticated,
        on_output=on_output,
        should_cancel=should_cancel,
        spec=spec,
    )


def _push_to_one_device(
    db,
    item: FirmwareUpgradeJobItem,
    commands_by_device_type: dict[str, str],
    protocol: str,
    server_host: str,
    otp: str | None,
) -> None:
    item.status = JobStatus.AUTHENTICATING
    item.started_at = datetime.now(timezone.utc)
    db.commit()

    device = item.device
    credential = _resolve_credential(db, device)

    if credential is None:
        item.status = JobStatus.FAILED
        item.error_message = "Device has no credential assigned"
        item.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    raw_commands = commands_by_device_type.get(device.device_type)
    if not raw_commands:
        item.status = JobStatus.FAILED
        item.error_message = f"No copy command supplied for device type '{device.device_type}'"
        item.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    job = db.get(FirmwareUpgradeJob, item.job_id)
    image = job.firmware_image if job else None
    if image is None:
        item.status = JobStatus.FAILED
        item.error_message = "Firmware image no longer exists"
        item.finished_at = datetime.now(timezone.utc)
        db.commit()
        return

    transfer_protocol = TransferProtocol(protocol)
    try:
        commands = render_push_commands(
            raw_commands,
            protocol=transfer_protocol,
            host=server_host,
            port=transfer_port(transfer_protocol),
            filename=image.original_filename,
        )
    except ValueError as exc:
        # Already validated at job creation - this only fires for a job
        # queued by an older client that skipped that check.
        item.status = JobStatus.FAILED
        item.error_message = str(exc)
        item.finished_at = datetime.now(timezone.utc)
        db.commit()
        return
    image_path = Path(settings.firmware_storage_dir) / str(job.org_id) / image.stored_filename

    on_authenticated = _make_firmware_authenticated_callback(db, item)
    on_output = _make_firmware_output_callback(db, item)
    should_cancel = _make_firmware_should_cancel(db, item.job_id)

    try:
        async def _run() -> str:
            async with serve_file(transfer_protocol, image_path, server_host):
                return await asyncio.to_thread(
                    _attempt_push, device, credential, otp, commands, on_authenticated, on_output, should_cancel
                )

        log.info(
            "Pushing %s to %s (%s) over %s from %s", image.original_filename, device.name, device.host, transfer_protocol.value, server_host
        )
        _run_async_in_new_thread(_run)
        item.status = JobStatus.COMPLETED
        log.info("Pushed %s to %s (%s)", image.original_filename, device.name, device.host)
    except CollectionCancelled:
        item.status = JobStatus.CANCELLED
        log.info("File push to %s (%s) cancelled", device.name, device.host)
    except CollectionError as exc:
        item.status = JobStatus.FAILED
        item.error_message = str(exc)
        log.warning("File push to %s (%s) FAILED: %s", device.name, device.host, exc)
        on_output(f"\nERROR: {exc}\n")

    item.finished_at = datetime.now(timezone.utc)
    db.commit()


def _make_firmware_authenticated_callback(db, item: FirmwareUpgradeJobItem):
    def _on_authenticated() -> None:
        try:
            item.status = JobStatus.RUNNING
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    return _on_authenticated


def _make_firmware_output_callback(db, item: FirmwareUpgradeJobItem):
    def _on_output(text: str) -> None:
        try:
            item.live_output = (item.live_output or "") + text
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    return _on_output


def _make_firmware_should_cancel(db, job_id):
    def _should_cancel() -> bool:
        try:
            return bool(
                db.execute(select(FirmwareUpgradeJob.cancel_requested).where(FirmwareUpgradeJob.id == job_id)).scalar()
            )
        except Exception:  # noqa: BLE001
            return False

    return _should_cancel


def _finalize_firmware_job_if_done(db, job_id) -> None:
    job = db.get(FirmwareUpgradeJob, job_id)
    if job is None:
        return

    statuses = [
        s
        for (s,) in db.query(FirmwareUpgradeJobItem.status).filter(FirmwareUpgradeJobItem.job_id == job.id).all()
    ]
    if any(s in ACTIVE_JOB_STATUSES for s in statuses):
        return

    if any(s == JobStatus.CANCELLED for s in statuses):
        job.status = JobStatus.CANCELLED
    elif any(s == JobStatus.FAILED for s in statuses):
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.COMPLETED
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


@celery_app.task(name="app.tasks.run_dns_check_job_task", bind=True, max_retries=0)
def run_dns_check_job_task(self, job_id: str) -> None:
    """Runs every target in a DnsCheckJob, in chunks of DNS_CHECK_CHUNK_SIZE.

    Unlike collect_device_task's per-device dispatch chain, there's no auth
    phase to overlap here (a ping/DNS lookup has no credential/MFA wait) -
    so rather than one Celery task per target, this single task processes
    the whole batch itself. Each chunk's checks run concurrently (via
    run_dns_checks' own internal semaphore - DNS_CHECK_CHUNK_SIZE matches
    it exactly, so one chunk is exactly one round of full concurrency) in a
    dedicated thread (_run_async_in_new_thread - same "asyncio.run() can't
    nest inside CELERY_TASK_ALWAYS_EAGER's caller" reasoning as the
    firmware task), with results written back and committed between
    chunks. That's what makes a job with thousands of targets show live,
    incremental progress instead of jumping from "running" to "done" only
    once the entire batch finishes - and lets a mid-batch cancel request
    stop the remaining chunks instead of running to completion regardless.
    """
    db = SyncSessionLocal()
    try:
        job = db.get(DnsCheckJob, job_id)
        if job is None:
            return
        items = (
            db.query(DnsCheckJobItem)
            .filter(DnsCheckJobItem.job_id == job.id)
            .order_by(DnsCheckJobItem.created_at)
            .all()
        )

        for chunk_start in range(0, len(items), DNS_CHECK_CHUNK_SIZE):
            db.refresh(job)
            if job.cancel_requested:
                break

            chunk = items[chunk_start : chunk_start + DNS_CHECK_CHUNK_SIZE]
            now = datetime.now(timezone.utc)
            for item in chunk:
                item.status = JobStatus.RUNNING
                item.started_at = now
            db.commit()

            targets = [item.target for item in chunk]
            results = _run_async_in_new_thread(lambda: run_dns_checks(targets))

            now = datetime.now(timezone.utc)
            for item, result in zip(chunk, results):
                item.ping_ok = result.ping_ok
                item.forward_ok = result.forward_ok
                item.forward_ips = ", ".join(result.forward_ips)
                item.reverse_ok = result.reverse_ok
                item.reverse_hostname = result.reverse_hostname
                item.status = JobStatus.COMPLETED
                item.finished_at = now
            db.commit()

        job.status = JobStatus.CANCELLED if job.cancel_requested else JobStatus.COMPLETED
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        log.info("DNS check job %s %s: %d target(s)", job.id, job.status.value, len(items))
    finally:
        db.close()


def _parse_schedule_device_ids(raw: str | None) -> list[uuid.UUID] | None:
    if not raw:
        return None
    return [uuid.UUID(x) for x in raw.split(",") if x.strip()]


def _advance_schedule(schedule: Schedule, now: datetime) -> None:
    """Moves a schedule on to its next occurrence after `now` - or, for a
    one-time schedule that has just had its one run, disables it (its
    next_run_at is left as the moment it fired, so the UI can show when)."""
    next_run_at = compute_next_run_at(ScheduleTiming.from_schedule(schedule), after=now)
    if next_run_at is None:
        schedule.enabled = False
    else:
        schedule.next_run_at = next_run_at


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
        if due:
            log.info("%d schedule(s) due", len(due))
        for schedule in due:
            try:
                _run_one_schedule(db, schedule, now)
                log.info("Schedule '%s' (%s) started a collection job; next run %s", schedule.name, schedule.id, schedule.next_run_at)
            except Exception:  # noqa: BLE001
                log.exception("Schedule '%s' (%s) failed to start; advancing to its next occurrence", schedule.name, schedule.id)
                # One schedule's failure (a DB hiccup, an unreachable
                # broker) shouldn't stop the others due on this same tick,
                # or get stuck retrying the same failure forever - it's
                # still advanced to its next occurrence.
                db.rollback()
                _advance_schedule(schedule, now)
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

    _advance_schedule(schedule, now)
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
            deleted = db.query(ConfigSnapshot).filter(
                ConfigSnapshot.job_item_id.in_(stale_item_ids),
                ConfigSnapshot.collected_at < cutoff,
            ).delete(synchronize_session=False)
            if deleted:
                log.info("Retention: deleted %d snapshot(s) older than %d days for org %s", deleted, org.snapshot_retention_days, org.id)
        db.commit()
    finally:
        db.close()


def _snmp_auth_from_profile(profile: SnmpProfile) -> SnmpAuth:
    """Decrypts a profile's secrets into the plain SnmpAuth the poller
    uses - in memory only, for the length of the poll."""
    return SnmpAuth(
        version=profile.version,
        community=decrypt_secret(profile.encrypted_community) if profile.encrypted_community else None,
        username=profile.username,
        security_level=profile.security_level,
        auth_protocol=profile.auth_protocol,
        auth_password=decrypt_secret(profile.encrypted_auth_password) if profile.encrypted_auth_password else None,
        priv_protocol=profile.priv_protocol,
        priv_password=decrypt_secret(profile.encrypted_priv_password) if profile.encrypted_priv_password else None,
        context_name=profile.context_name,
        port=profile.port,
        timeout_seconds=profile.timeout_seconds,
        retries=profile.retries,
    )


@celery_app.task(name="app.tasks.run_snmp_job_task", bind=True, max_retries=0)
def run_snmp_job_task(self, job_id: str) -> None:
    """Polls every device in an SnmpJob (see services/snmp_poll.py), in
    chunks of SNMP_CHUNK_SIZE polled concurrently - same shape as
    run_dns_check_job_task: one task for the whole job, results committed
    per chunk so progress is visible and a cancel takes effect between
    chunks. Which profile a device uses: the job's override if set, else
    the device's own snmp_profile_id, else the org default."""
    db = SyncSessionLocal()
    try:
        job = db.get(SnmpJob, job_id)
        if job is None:
            return
        items = db.query(SnmpJobItem).filter(SnmpJobItem.job_id == job.id).order_by(SnmpJobItem.created_at).all()
        override = db.get(SnmpProfile, job.snmp_profile_id) if job.snmp_profile_id else None
        default_profile = (
            db.query(SnmpProfile).filter(SnmpProfile.org_id == job.org_id, SnmpProfile.is_default.is_(True)).first()
        )
        extra_oids = [o for o in (job.extra_oids or "").split(", ") if o]

        for chunk_start in range(0, len(items), SNMP_CHUNK_SIZE):
            db.refresh(job)
            if job.cancel_requested:
                break
            chunk = [i for i in items[chunk_start : chunk_start + SNMP_CHUNK_SIZE] if i.status == JobStatus.PENDING]
            if not chunk:
                continue

            now = datetime.now(timezone.utc)
            specs = []
            runnable = []
            for item in chunk:
                item.started_at = now
                device = item.device
                profile = override or (db.get(SnmpProfile, device.snmp_profile_id) if device and device.snmp_profile_id else None) or default_profile
                if device is None:
                    item.status = JobStatus.FAILED
                    item.error_message = "Device no longer exists"
                    item.finished_at = now
                elif profile is None:
                    item.status = JobStatus.FAILED
                    item.error_message = "No SNMP profile for this device and no org default SNMP profile"
                    item.finished_at = now
                else:
                    item.status = JobStatus.RUNNING
                    item.profile_name = profile.name
                    specs.append((device.host, _snmp_auth_from_profile(profile), extra_oids))
                    runnable.append(item)
            db.commit()

            if runnable:
                # Narration is written once per chunk rather than streamed
                # per line: a poll is short, and a DB commit per emitted
                # line from inside the poller's thread would race this
                # session.
                results = _run_async_in_new_thread(lambda: poll_many(specs))
                now = datetime.now(timezone.utc)
                for item, spec, result in zip(runnable, specs, results):
                    host, auth, _ = spec
                    if isinstance(result, SnmpError):
                        item.status = JobStatus.FAILED
                        item.error_message = str(result)
                        item.live_output = f"Polling {host}:{auth.port} over {auth.describe()}...\nERROR: {result}\n"
                        log.warning("SNMP poll of %s over %s FAILED: %s", host, auth.describe(), result)
                    elif isinstance(result, Exception):
                        item.status = JobStatus.FAILED
                        item.error_message = f"Unexpected error during SNMP poll: {result}"
                        item.live_output = f"Polling {host}:{auth.port} over {auth.describe()}...\nERROR: {result}\n"
                        log.error("SNMP poll of %s hit an unexpected error: %r", host, result, exc_info=(type(result), result, result.__traceback__))
                    else:
                        log.info("SNMP poll of %s over %s succeeded", host, auth.describe())
                        item.report = format_report(
                            item.device.name, host, auth, result, collected_at=now.strftime("%Y-%m-%d %H:%M:%S UTC")
                        )
                        summary = [
                            f"Polling {host}:{auth.port} over {auth.describe()}...",
                            f"System: {result.system.get('sysName') or '?'} - {(result.system.get('sysDescr') or '')[:80]}",
                            f"Interfaces: {len(result.interfaces)} rows",
                            f"Syslog history: {len(result.syslog)} entries"
                            + ("" if result.syslog_supported else " (not supported by this device)"),
                        ] + [f"Warning: {w}" for w in result.warnings]
                        item.live_output = "\n".join(summary) + "\n"
                        item.status = JobStatus.COMPLETED
                    item.finished_at = now
                db.commit()

        statuses = [i.status for i in db.query(SnmpJobItem).filter(SnmpJobItem.job_id == job.id).all()]
        if job.cancel_requested or any(s == JobStatus.CANCELLED for s in statuses):
            job.status = JobStatus.CANCELLED
        elif any(s == JobStatus.FAILED for s in statuses):
            job.status = JobStatus.FAILED
        else:
            job.status = JobStatus.COMPLETED
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


# --- SNMP monitoring / alerting -----------------------------------------------------


@celery_app.task(name="app.tasks.run_snmp_monitors")
def run_snmp_monitors() -> None:
    """Invoked every minute by Celery beat: runs a monitoring cycle for
    every org whose monitor is enabled and due (see run_snmp_monitor_cycle).
    One org's failure never stops the others."""
    db = SyncSessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due = (
            db.query(SnmpMonitorConfig)
            .filter(
                SnmpMonitorConfig.enabled.is_(True),
                (SnmpMonitorConfig.next_run_at.is_(None)) | (SnmpMonitorConfig.next_run_at <= now),
            )
            .all()
        )
        org_ids = [c.org_id for c in due]
    finally:
        db.close()
    for org_id in org_ids:
        try:
            run_snmp_monitor_cycle(org_id)
        except Exception:  # noqa: BLE001
            log.exception("SNMP monitoring cycle for org %s crashed; will retry on its next interval", org_id)


def _monitor_devices(db, config: SnmpMonitorConfig) -> list[Device]:
    query = db.query(Device).filter(Device.org_id == config.org_id)
    if config.device_ids:
        ids = [uuid.UUID(x) for x in config.device_ids.split(",") if x.strip()]
        query = query.filter(Device.id.in_(ids))
    return query.order_by(Device.created_at).all()


def run_snmp_monitor_cycle(org_id, *, force: bool = False) -> dict:
    """One monitoring cycle for an org: snapshot every monitored device,
    diff against its stored state, record SnmpAlerts for enabled events,
    email the batch, store the new states, and schedule the next cycle.
    Returns a small summary (also written to config.last_result). `force`
    runs even when the monitor is disabled (the UI's "Run now")."""
    db = SyncSessionLocal()
    try:
        config = db.query(SnmpMonitorConfig).filter(SnmpMonitorConfig.org_id == org_id).first()
        if config is None or (not config.enabled and not force):
            return {"skipped": True}
        now = datetime.now(timezone.utc)
        config.last_run_at = now
        config.next_run_at = now + timedelta(minutes=config.interval_minutes)
        db.commit()

        devices = _monitor_devices(db, config)
        override = db.get(SnmpProfile, config.snmp_profile_id) if config.snmp_profile_id else None
        default_profile = (
            db.query(SnmpProfile).filter(SnmpProfile.org_id == org_id, SnmpProfile.is_default.is_(True)).first()
        )
        states = {
            s.device_id: s for s in db.query(SnmpMonitorState).filter(SnmpMonitorState.org_id == org_id).all()
        }
        filters = EventFilter(
            link_down=config.alert_link_down,
            link_up=config.alert_link_up,
            ap_down=config.alert_ap_down,
            ap_up=config.alert_ap_up,
            device_down=config.alert_device_down,
            device_up=config.alert_device_up,
            syslog_max_level=config.alert_syslog_max_level,
        )

        specs = []
        skipped_no_profile = 0
        for device in devices:
            profile = override or (db.get(SnmpProfile, device.snmp_profile_id) if device.snmp_profile_id else None) or default_profile
            if profile is None:
                skipped_no_profile += 1
                continue
            previous = states.get(device.id)
            prev_snapshot = Snapshot.from_json(previous.snapshot_json) if previous else None
            specs.append((device, _snmp_auth_from_profile(profile), prev_snapshot))

        async def _snapshot_all():
            semaphore = asyncio.Semaphore(SNMP_CHUNK_SIZE)

            async def one(device, auth, prev):
                async with semaphore:
                    try:
                        return await snmp_monitor.take_snapshot(
                            device.host,
                            auth,
                            previous_syslog_index=prev.syslog_max_index if prev else 0,
                            watch_syslog=config.alert_syslog_max_level is not None,
                        )
                    except Exception as exc:  # noqa: BLE001
                        return Snapshot(reachable=False, error=f"Unexpected error: {exc}")

            return await asyncio.gather(*(one(*spec) for spec in specs))

        snapshots = _run_async_in_new_thread(_snapshot_all) if specs else []

        batch: list[tuple[str, snmp_monitor.Event]] = []
        new_alerts: list[SnmpAlert] = []
        for (device, _auth, prev_snapshot), snapshot in zip(specs, snapshots):
            if prev_snapshot is None and not snapshot.reachable:
                # Never seen it answer: don't alert "down" for a device that may
                # simply not speak SNMP yet - but remember it, so the first
                # successful answer isn't reported as a recovery either.
                pass
            events = diff_snapshots(device.name, prev_snapshot, snapshot, filters)
            for ev in events:
                batch.append((device.name, ev))
                new_alerts.append(
                    SnmpAlert(org_id=org_id, device_id=device.id, device_name=device.name, kind=ev.kind, subject=ev.subject, detail=ev.detail)
                )
            state = states.get(device.id)
            if state is None:
                state = SnmpMonitorState(org_id=org_id, device_id=device.id, snapshot_json=snapshot.to_json())
                db.add(state)
            else:
                state.snapshot_json = snapshot.to_json()
            state.updated_at = now
        db.add_all(new_alerts)
        db.commit()

        summary = f"Polled {len(specs)} device(s)"
        if skipped_no_profile:
            summary += f", {skipped_no_profile} skipped (no SNMP profile)"
        summary += f", {len(new_alerts)} alert(s)"
        recipients = parse_recipients(config.recipients)
        if new_alerts:
            if recipients and config.smtp_host:
                org = db.get(Organization, org_id)
                subject, body = format_email(org.name if org else "your organization", batch)
                try:
                    snmp_monitor.send_email(
                        SmtpSettings(
                            host=config.smtp_host,
                            port=config.smtp_port,
                            username=config.smtp_username,
                            password=decrypt_secret(config.encrypted_smtp_password) if config.encrypted_smtp_password else None,
                            starttls=config.smtp_starttls,
                            ssl=config.smtp_ssl,
                            sender=config.smtp_from,
                        ),
                        recipients,
                        subject,
                        body,
                    )
                    for alert in new_alerts:
                        alert.emailed = True
                    summary += f", emailed {len(recipients)} recipient(s)"
                except Exception as exc:  # noqa: BLE001
                    log.error("SNMP alert email via %s:%s FAILED for org %s: %s", config.smtp_host, config.smtp_port, org_id, exc)
                    for alert in new_alerts:
                        alert.email_error = str(exc)[:1000]
                    summary += f", EMAIL FAILED: {exc}"
            else:
                for alert in new_alerts:
                    alert.email_error = "No recipients or SMTP server configured"
                summary += ", not emailed (no recipients/SMTP configured)"
        config.last_result = summary
        db.commit()
        log.log(logging.WARNING if new_alerts else logging.INFO, "SNMP monitor cycle for org %s: %s", org_id, summary)
        for alert in new_alerts:
            log.warning("SNMP alert: %s - %s: %s", alert.device_name, alert.kind.value, alert.detail or alert.subject)
        return {"polled": len(specs), "alerts": len(new_alerts), "summary": summary}
    finally:
        db.close()
