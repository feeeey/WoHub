import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app_logger import log as applog

_scheduler = None

# Every CRON_TRIGGERS entry fires on the same minute marks (:58, :13/:28/:43…),
# so all due tasks are released simultaneously into a single worker thread and
# run one after another. APScheduler's default misfire_grace_time is 1 SECOND:
# any task still waiting for the worker 1s after its scheduled time is discarded
# outright, not queued. With screener calls rate-limited to one per 2s and
# screenshots taking ~10s each, the first task easily runs for minutes — so
# every other task due that minute was silently dropped. This grace window is
# what makes them queue instead.
MISFIRE_GRACE_S = 3600

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


class _ApplogHandler(logging.Handler):
    """Bridge APScheduler's own warnings into the in-app log ring buffer.

    APScheduler reports a dropped job via the stdlib logger only. The app has no
    logging config, so those records went to stderr and never reached
    /api/settings/logs — a task could stop running for weeks and the UI showed
    nothing at all."""

    _LEVELS = {logging.ERROR: "error", logging.WARNING: "warn"}

    def emit(self, record: logging.LogRecord) -> None:
        try:
            applog("scheduler", self._LEVELS.get(record.levelno, "info"),
                   record.getMessage()[:500])
        except Exception:
            pass


def _bridge_apscheduler_logs() -> None:
    log = logging.getLogger("apscheduler")
    if not any(isinstance(h, _ApplogHandler) for h in log.handlers):
        log.addHandler(_ApplogHandler(level=logging.WARNING))
        log.setLevel(logging.INFO)


def get_scheduler():
    global _scheduler
    if _scheduler is None:
        _bridge_apscheduler_logs()
        # max_workers=1: tasks execute sequentially, avoiding DB lock contention
        # and respecting TradingView's single-request-at-a-time constraint.
        # coalesce collapses a backlog of the SAME job into one run; the grace
        # window is what keeps OTHER jobs queued behind a long-running one.
        _scheduler = BackgroundScheduler(
            timezone="UTC",
            executors={"default": {"type": "threadpool", "max_workers": 1}},
            job_defaults={"coalesce": True, "max_instances": 1,
                          "misfire_grace_time": MISFIRE_GRACE_S},
        )
        _scheduler.start()
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def add_task_jobs(task_id, func, resolutions):
    """Register one cron job per resolution. Each fires on its own schedule."""
    scheduler = get_scheduler()
    remove_task_job(task_id)  # clean up old jobs first

    if not resolutions:
        resolutions = ["1h"]

    for res in resolutions:
        job_id = f"task_{task_id}_{res}"
        trigger = CRON_TRIGGERS.get(res)
        if not trigger:
            raise ValueError(f"Invalid resolution: {res}")
        # func signature: func(task_id, resolution)
        scheduler.add_job(func, trigger, id=job_id, args=[task_id, res], replace_existing=True)


def remove_task_job(task_id):
    """Remove all jobs for a task (across all resolutions)."""
    scheduler = get_scheduler()
    prefix = f"task_{task_id}_"
    for job in scheduler.get_jobs():
        if job.id == f"task_{task_id}" or job.id.startswith(prefix):
            try:
                scheduler.remove_job(job.id)
            except Exception:
                pass


def is_task_running(task_id):
    """A task is running if any of its resolution jobs is registered."""
    scheduler = get_scheduler()
    prefix = f"task_{task_id}_"
    for job in scheduler.get_jobs():
        if job.id == f"task_{task_id}" or job.id.startswith(prefix):
            return True
    return False


def get_shortest_resolution(resolutions):
    for r in RESOLUTION_PRIORITY:
        if r in resolutions:
            return r
    return resolutions[0] if resolutions else "1h"
