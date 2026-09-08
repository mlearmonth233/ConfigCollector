from datetime import datetime, timezone

from app.celery_app import celery_app
from app.core.encryption import decrypt_secret
from app.db_sync import SyncSessionLocal
from app.models.credential import Credential
from app.models.device import Device
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.snapshot import ConfigSnapshot
from app.services.collector import AuthenticationError, CollectionError, collect_device_config


@celery_app.task(name="app.tasks.collect_device_task", bind=True, max_retries=0)
def collect_device_task(
    self,
    job_item_id: str,
    commands_override: list[str] | None = None,
    otp: str | None = None,
    fallback_otp: str | None = None,
) -> None:
    db = SyncSessionLocal()
    try:
        item = db.get(CollectionJobItem, job_item_id)
        if item is None:
            return

        # Queued devices sit here until it's their turn; once dequeued, the
        # first phase is authenticating (SSH + TACACS+/RADIUS + any MFA) -
        # no config commands are sent until that succeeds.
        item.status = JobStatus.AUTHENTICATING
        item.started_at = datetime.now(timezone.utc)
        db.commit()

        device = item.device
        credential = device.credential

        if credential is None:
            item.status = JobStatus.FAILED
            item.error_message = "Device has no credential assigned"
        else:
            on_authenticated = _make_authenticated_callback(db, item)
            try:
                try:
                    content = _attempt_collection(
                        device, credential, otp, commands_override, on_authenticated
                    )
                    used_fallback = False
                except AuthenticationError as primary_exc:
                    fallback = credential.fallback_credential
                    if fallback is None:
                        raise
                    # Reset to AUTHENTICATING (the callback may have already
                    # flipped it to RUNNING had the primary attempt somehow
                    # gotten further - it can't have, since AuthenticationError
                    # only ever comes from a failed/timed-out login, but this
                    # keeps the displayed phase honest regardless).
                    item.status = JobStatus.AUTHENTICATING
                    db.commit()
                    try:
                        content = _attempt_collection(
                            device, fallback, fallback_otp, commands_override, on_authenticated
                        )
                        used_fallback = True
                    except CollectionError as fallback_exc:
                        raise CollectionError(
                            f"Primary credential '{credential.name}' failed to authenticate: "
                            f"{primary_exc} | Fallback credential '{fallback.name}' also failed: "
                            f"{fallback_exc}"
                        ) from fallback_exc

                db.add(ConfigSnapshot(device_id=device.id, job_item_id=item.id, content=content))
                item.status = JobStatus.COMPLETED
                item.used_fallback_credential = used_fallback
            except CollectionError as exc:
                item.status = JobStatus.FAILED
                item.error_message = str(exc)

        item.finished_at = datetime.now(timezone.utc)
        db.commit()

        _finalize_job_if_done(db, item.job_id)
    finally:
        db.close()


def _attempt_collection(
    device: Device,
    credential: Credential,
    otp: str | None,
    commands_override: list[str] | None,
    on_authenticated,
) -> str:
    """One connection attempt with one credential. Raises AuthenticationError
    if login itself fails, CommandExecutionError if login succeeds but
    running commands doesn't - callers use that distinction to decide
    whether falling back to another credential makes sense."""
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
    )


def _make_authenticated_callback(db, item: CollectionJobItem):
    """Flips an item from AUTHENTICATING to RUNNING the instant its device
    login succeeds. Swallows its own errors - a hiccup persisting this
    transitional status shouldn't be mistaken for (or block) the actual
    device collection outcome, which is recorded independently below."""

    def _on_authenticated() -> None:
        try:
            item.status = JobStatus.RUNNING
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()

    return _on_authenticated


def _finalize_job_if_done(db, job_id) -> None:
    job = db.get(CollectionJob, job_id)
    if job is None:
        return

    items = db.query(CollectionJobItem).filter(CollectionJobItem.job_id == job.id).all()
    if any(i.status in (JobStatus.PENDING, JobStatus.AUTHENTICATING, JobStatus.RUNNING) for i in items):
        return

    job.status = JobStatus.FAILED if any(i.status == JobStatus.FAILED for i in items) else JobStatus.COMPLETED
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
