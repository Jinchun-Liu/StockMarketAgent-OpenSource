# Scheduler

This runtime owns only timing. It launches one Celery Beat instance using `apps.worker.celery_app` and never runs business logic inline. Every task must independently enforce the China exchange calendar, session window, idempotency key and distributed lock; weekday cron expressions alone are insufficient because of public holidays and make-up workdays.
