"""Starting a background task from the API.

Normally a task goes to the Celery worker through the broker. In eager
mode (CELERY_TASK_ALWAYS_EAGER, no broker) Celery runs it inside the
caller, which would hold the HTTP request open until a whole collection
finishes; with EAGER_BACKGROUND it runs on a thread of the API process
instead, so the request returns at once and the job's progress is visible
while it runs. The tasks chain the next device themselves, so one thread
works through a whole job in order.
"""

from __future__ import annotations

import logging
import threading

from app.config import get_settings

log = logging.getLogger(__name__)


def dispatch(task, args: list | None = None, kwargs: dict | None = None) -> None:
    settings = get_settings()
    if settings.celery_task_always_eager and settings.eager_background:

        def run() -> None:
            try:
                task.apply(args=args or [], kwargs=kwargs or {})
            except Exception:  # noqa: BLE001 - the task records its own failure; this is only the log
                log.exception("Background task %s failed", getattr(task, "name", task))

        threading.Thread(target=run, name=f"job-{getattr(task, 'name', 'task')}", daemon=True).start()
        return
    task.apply_async(args=args or [], kwargs=kwargs or {})
