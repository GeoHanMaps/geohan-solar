from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init
from app.config import settings

celery_app = Celery(
    "geohan",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],
)

@worker_process_init.connect
def init_gee(**kwargs):
    try:
        from app.gee_init import initialize_ee
        initialize_ee()
    except Exception:
        pass  # GEE yoksa terrain servisi kendi hatasını verir


celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,           # worker crash'te task yeniden kuyruğa girer
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # her worker bir task alır, adil dağılım
    # Günlük artefakt retention (worker'ı -B ile çalıştır; yoksa cron ile
    # `celery -A app.celery_app call geohan.cleanup_artifacts`).
    beat_schedule={
        "cleanup-expired-artifacts": {
            "task": "geohan.cleanup_artifacts",
            "schedule": crontab(hour=3, minute=17),
        },
    },
)
