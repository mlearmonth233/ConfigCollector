from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("configcollector", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_always_eager=settings.celery_task_always_eager,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    # Requires a separate `celery -A app.celery_app beat` process running
    # alongside the worker - see README. run_due_schedules checks fairly
    # often since a schedule's next_run_at can be as granular as an hour;
    # purge_expired_snapshots only needs to notice a day boundary, so
    # hourly is already more than enough.
    beat_schedule={
        "run-due-schedules": {"task": "app.tasks.run_due_schedules", "schedule": 60.0},
        "purge-expired-snapshots": {"task": "app.tasks.purge_expired_snapshots", "schedule": 3600.0},
    },
)

# Ensure tasks module is registered with this app.
celery_app.autodiscover_tasks(["app"])
