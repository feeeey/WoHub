from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

_scheduler = None

CRON_TRIGGERS = {
    "5m": CronTrigger(minute="3,8,13,18,23,28,33,38,43,48,53,58", timezone="UTC"),
    "15m": CronTrigger(minute="13,28,43,58", timezone="UTC"),
    "30m": CronTrigger(minute="28,58", timezone="UTC"),
    "1h": CronTrigger(minute=58, timezone="UTC"),
    "4h": CronTrigger(hour="3,7,11,15,19,23", minute=58, timezone="UTC"),
    "1d": CronTrigger(hour=23, minute=58, timezone="UTC"),
    "1w": CronTrigger(day_of_week="sun", hour=23, minute=58, timezone="UTC"),
}

SCHEDULE_DESC = {
    "5m": "每5分钟",
    "15m": "每15分钟",
    "30m": "每30分钟",
    "1h": "每小时 :58 UTC",
    "4h": "每4小时",
    "1d": "每天 23:58 UTC",
    "1w": "每周日 23:58 UTC",
}

RESOLUTION_PRIORITY = ["5m", "15m", "30m", "1h", "4h", "1d", "1w"]


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        # max_workers=1: tasks execute sequentially, avoiding DB lock contention
        # and respecting TradingView's single-request-at-a-time constraint
        _scheduler = BackgroundScheduler(
            timezone="UTC",
            executors={"default": {"type": "threadpool", "max_workers": 1}},
            job_defaults={"coalesce": True, "max_instances": 1},
        )
        _scheduler.start()
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def add_task_job(task_id, func, schedule_key):
    scheduler = get_scheduler()
    job_id = f"task_{task_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    trigger = CRON_TRIGGERS.get(schedule_key)
    if trigger:
        scheduler.add_job(func, trigger, id=job_id, args=[task_id], replace_existing=True)
    else:
        try:
            seconds = int(schedule_key)
            scheduler.add_job(func, IntervalTrigger(seconds=seconds), id=job_id, args=[task_id], replace_existing=True)
        except ValueError:
            raise ValueError(f"Invalid schedule: {schedule_key}")


def remove_task_job(task_id):
    scheduler = get_scheduler()
    job_id = f"task_{task_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


def is_task_running(task_id):
    scheduler = get_scheduler()
    job_id = f"task_{task_id}"
    return scheduler.get_job(job_id) is not None


def get_shortest_resolution(resolutions):
    for r in RESOLUTION_PRIORITY:
        if r in resolutions:
            return r
    return resolutions[0] if resolutions else "1h"
