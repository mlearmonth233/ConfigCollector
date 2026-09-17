import logging
import sys

from celery import Celery
from celery.signals import setup_logging, task_failure, task_postrun, task_prerun, worker_ready

from app.config import assert_secrets_configured, get_settings
from app.core.logging_config import configure_logging

settings = get_settings()
assert_secrets_configured(settings)
log = logging.getLogger("app.celery")


def _process_name() -> str:
    """'beat' when this process is `celery ... beat`, 'worker' for the
    worker and anything else (eager mode runs tasks inside the API process,
    which has already configured itself as 'api' - configure_logging is a
    no-op there)."""
    return "beat" if "beat" in sys.argv else "worker"


@setup_logging.connect
def _configure_celery_logging(**_kwargs) -> None:
    # Connecting this signal stops Celery from installing its own root
    # handlers, so the worker/beat share the app's file+console setup.
    configure_logging(_process_name())


@worker_ready.connect
def _reap_leftover_jobs(**_kwargs) -> None:
    # A freshly started worker has nothing in flight, so any job still
    # marked running was interrupted when the previous worker stopped.
    if not settings.reap_jobs_on_start:
        return
    from app.services.job_reaper import reap_orphaned_jobs  # noqa: PLC0415 - avoid import cycle at module load

    try:
        reap_orphaned_jobs("the worker was restarted")
    except Exception:  # noqa: BLE001
        log.exception("Start-up job cleanup failed")


@task_prerun.connect
def _log_task_start(task_id=None, task=None, args=None, kwargs=None, **_ignored) -> None:
    log.info("Task %s started (id %s) args=%s", task.name if task else "?", task_id, _brief(args))


@task_postrun.connect
def _log_task_end(task_id=None, task=None, state=None, **_ignored) -> None:
    log.info("Task %s finished (id %s) state=%s", task.name if task else "?", task_id, state)


@task_failure.connect
def _log_task_failure(task_id=None, exception=None, traceback=None, sender=None, **_ignored) -> None:
    log.error(
        "Task %s FAILED (id %s): %r",
        getattr(sender, "name", "?"),
        task_id,
        exception,
        exc_info=(type(exception), exception, traceback) if exception is not None else None,
    )


def _brief(args) -> str:
    """Only the first task argument (always an id in this app) makes it to
    the log line - later arguments can carry one-time passcodes."""
    if not args:
        return "[]"
    args = list(args)
    return "[" + str(args[0])[:60] + (", …" if len(args) > 1 else "") + "]"

def broker_url(settings=settings) -> str:
    return settings.celery_broker_url or settings.redis_url


def result_backend(settings=settings) -> str | None:
    """Explicit CELERY_RESULT_BACKEND wins ("none" disables results);
    otherwise Redis brokers double as the backend and anything else (the
    desktop bundle's SQLite queue) runs without one."""
    configured = settings.celery_result_backend.strip()
    if configured:
        return None if configured.lower() == "none" else configured
    url = broker_url(settings)
    return url if url.startswith(("redis://", "rediss://")) else None


_broker = broker_url()
_backend = result_backend()
# kombu's SQL transport has no fanout exchange, which is what the worker's
# remote-control mailbox (celery inspect/control) is built on; leaving it
# on just produces connection warnings on every start.
_supports_control = not _broker.startswith(("sqla+", "sqlalchemy+", "filesystem://"))

celery_app = Celery("configcollector", broker=_broker, backend=_backend)
celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_ignore_result=_backend is None,
    worker_enable_remote_control=_supports_control,
    beat_schedule_filename=settings.celery_beat_schedule_file,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    # Keep retrying the Redis connection while the worker starts (Memurai
    # or the redis container may come up a moment later). Celery 6 makes
    # this explicit; setting it now also silences the start-up warning.
    broker_connection_retry_on_startup=True,
    # Requires a separate `celery -A app.celery_app beat` process running
    # alongside the worker - see README. run_due_schedules checks fairly
    # often since a schedule's next_run_at can be as granular as an hour;
    # purge_expired_snapshots only needs to notice a day boundary, so
    # hourly is already more than enough.
    beat_schedule={
        "run-due-schedules": {"task": "app.tasks.run_due_schedules", "schedule": 60.0},
        "purge-expired-snapshots": {"task": "app.tasks.purge_expired_snapshots", "schedule": 3600.0},
        # SNMP monitoring/alerting cycles (per-org interval, minimum 1 min).
        "run-snmp-monitors": {"task": "app.tasks.run_snmp_monitors", "schedule": 60.0},
        # Jobs with no sign of life for STALE_JOB_MINUTES are marked interrupted.
        "reap-stale-jobs": {"task": "app.tasks.reap_stale_jobs", "schedule": 300.0},
        # Continuous ping monitor: each org runs on its own interval (min 5s), checked every 5s.
        # The task is a cheap "anything due?" query when nothing is.
        "run-ping-monitors": {"task": "app.tasks.run_ping_monitors", "schedule": 5.0},
    },
)

# Ensure tasks module is registered with this app.
celery_app.autodiscover_tasks(["app"])
