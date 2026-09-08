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
)

# Ensure tasks module is registered with this app.
celery_app.autodiscover_tasks(["app"])
