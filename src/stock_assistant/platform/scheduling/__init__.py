"""Scheduling and task-dispatch adapters."""

from stock_assistant.platform.scheduling.celery_dispatcher import CeleryJobDispatcher

__all__ = ["CeleryJobDispatcher"]
