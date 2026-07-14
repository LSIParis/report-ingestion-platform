from celery import Celery
from celery.schedules import crontab

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

# Un domaine SILENCIEUX ne produit aucun événement — c'est sa définition. Il faut donc
# aller le chercher : un balayage quotidien, tôt le matin.
celery.conf.beat_schedule = {
    "balayage-alertes": {
        "task": "app.workers.tasks.sweep_alerts",
        # `hour=6` s'entend en UTC -- le fuseau PAR DÉFAUT de Celery Beat (`timezone`
        # n'est pas surchargé dans `celery.conf.update(...)` ci-dessus). Ce n'est PAS
        # l'heure locale de l'exploitant : 6h UTC = 7h ou 8h à Paris selon la saison
        # (CET/CEST). À corriger explicitement (ou documenter côté exploitation) si
        # l'heure locale du balayage compte un jour.
        "schedule": crontab(hour=6, minute=0),
    },
}
