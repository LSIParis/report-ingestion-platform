from celery import Celery

from app.config import settings

celery = Celery(
    "report_platform",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=30,
    result_expires=86400,
    broker_transport_options={"visibility_timeout": 3600},
)
