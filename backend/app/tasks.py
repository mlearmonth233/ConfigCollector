from datetime import datetime, timezone

from app.celery_app import celery_app
from app.core.encryption import decrypt_secret
from app.db_sync import SyncSessionLocal
from app.models.job import CollectionJob, CollectionJobItem, JobStatus
from app.models.snapshot import ConfigSnapshot
from app.services.collector import CollectionError, collect_device_config


@celery_app.task(name="app.tasks.collect_device_task", bind=True, max_retries=0)
def collect_device_task(self, job_item_id: str) -> None:
    db = SyncSessionLocal()
    try:
        item = db.get(CollectionJobItem, job_item_id)
        if item is None:
            return

        item.status = JobStatus.RUNNING
        item.started_at = datetime.now(timezone.utc)
        db.commit()

        device = item.device
        credential = device.credential

        if credential is None:
            item.status = JobStatus.FAILED
            item.error_message = "Device has no credential assigned"
        else:
            try:
                password = decrypt_secret(credential.encrypted_password)
                secret = (
                    decrypt_secret(credential.encrypted_enable_secret)
                    if credential.encrypted_enable_secret
                    else None
                )
                content = collect_device_config(
                    host=device.host,
                    port=device.port,
                    device_type=device.device_type,
                    username=credential.username,
                    password=password,
                    secret=secret,
                    custom_commands=device.custom_commands,
                )
                db.add(ConfigSnapshot(device_id=device.id, job_item_id=item.id, content=content))
                item.status = JobStatus.COMPLETED
            except CollectionError as exc:
                item.status = JobStatus.FAILED
                item.error_message = str(exc)

        item.finished_at = datetime.now(timezone.utc)
        db.commit()

        _finalize_job_if_done(db, item.job_id)
    finally:
        db.close()


def _finalize_job_if_done(db, job_id) -> None:
    job = db.get(CollectionJob, job_id)
    if job is None:
        return

    items = db.query(CollectionJobItem).filter(CollectionJobItem.job_id == job.id).all()
    if any(i.status in (JobStatus.PENDING, JobStatus.RUNNING) for i in items):
        return

    job.status = JobStatus.FAILED if any(i.status == JobStatus.FAILED for i in items) else JobStatus.COMPLETED
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
