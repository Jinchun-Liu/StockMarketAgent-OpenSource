"""Durable asynchronous tasks and fail-closed periodic scheduling."""

from celery import Celery

from apps.worker.schedule_policy import TIMEZONE
from stock_assistant.config import get_settings
from stock_assistant.platform.runtime import valkey_urls_from_environment

settings = get_settings()
settings.assert_safe_production()
if settings.timezone != TIMEZONE:
    raise ValueError(f"runtime timezone must be {TIMEZONE}")

valkey_urls = valkey_urls_from_environment(
    default_redis=settings.redis_url.get_secret_value(),
    default_broker=settings.celery_broker_url.get_secret_value(),
    default_result_backend=settings.celery_result_backend.get_secret_value(),
)

celery_app = Celery(
    "stock_assistant",
    broker=valkey_urls.broker,
    backend=valkey_urls.result_backend,
    include=["apps.worker.tasks"],
)
celery_app.conf.update(
    timezone=settings.timezone,
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=settings.research_worker_concurrency,
    task_track_started=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
)
