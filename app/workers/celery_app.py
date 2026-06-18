import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# Use Redis as both broker and backend
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "reva",
    broker=redis_url,
    backend=redis_url,
    include=["app.workers.tasks"]
)

from celery.schedules import crontab

# Optimization settings
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Africa/Lagos",
    enable_utc=False,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Celery Beat Schedule
celery_app.conf.beat_schedule = {
    "nurture-job-daily": {
        "task": "app.workers.tasks.run_nurture_job_task",
        "schedule": crontab(hour=10, minute=0),
    },
    "resurrection-job-weekly": {
        "task": "app.workers.tasks.run_resurrection_job_task",
        "schedule": crontab(day_of_week="mon", hour=9, minute=0),
    },
    "portal-sync-interval": {
        "task": "app.workers.tasks.sync_portal_leads_task",
        "schedule": 900.0,  # 15 minutes
    },
    "roi-report-weekly": {
        "task": "app.workers.tasks.send_weekly_roi_report_task",
        "schedule": crontab(day_of_week="sun", hour=20, minute=0),
    },
    "usage-rollup-daily": {
        "task": "app.workers.tasks.rollup_usage_task",
        "schedule": crontab(hour=2, minute=0),  # 02:00 Lagos
    },
    "expire-trials-hourly": {
        "task": "app.workers.tasks.expire_trials_task",
        "schedule": crontab(minute=15),  # every hour at :15
    },
    "inbox-sla-every-5min": {
        "task": "app.workers.tasks.check_inbox_sla_task",
        "schedule": crontab(minute="*/5"),
    },
}

if __name__ == "__main__":
    celery_app.start()
