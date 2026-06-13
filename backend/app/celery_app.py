from celery import Celery

from app.config import settings

celery_app = Celery(
    "fincopilot",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.ingestion", "app.tasks.memory_extraction"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Without this, Celery defaults to os.cpu_count() prefork workers. On
    # Railway that reads the host's core count (e.g. 48), forking enough
    # processes to OOM-kill the container seconds after startup.
    worker_concurrency=2,
)
