"""Co-scheduled tasks must all run, not just the first one.

Every entry in CRON_TRIGGERS fires on the same minute marks, so in production
all due tasks are released at once into a single worker thread. With
APScheduler's default 1-second misfire grace, everything queued behind the
first (multi-minute) task was silently discarded — the platform looked healthy
while most tasks never executed.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.triggers.interval import IntervalTrigger

from tasks import scheduler as sched_mod
from tasks.scheduler import (MISFIRE_GRACE_S, CRON_TRIGGERS, get_scheduler,
                             _bridge_apscheduler_logs)


@pytest.fixture
def fresh_scheduler():
    sched_mod._scheduler = None
    sched = get_scheduler()
    yield sched
    sched.shutdown(wait=False)
    sched_mod._scheduler = None


def test_grace_window_is_configured(fresh_scheduler):
    """A 1s default is what dropped the jobs; anything short reintroduces it."""
    assert fresh_scheduler._job_defaults["misfire_grace_time"] == MISFIRE_GRACE_S
    assert MISFIRE_GRACE_S >= 600


def test_all_co_scheduled_tasks_run_behind_a_slow_one(fresh_scheduler):
    """The regression: three tasks due at the same instant, first one slow."""
    started, done = [], threading.Event()
    lock = threading.Lock()

    def job(n):
        with lock:
            started.append(n)
            if len(started) == 3:
                done.set()
        if n == 1:
            time.sleep(2)  # stands in for screener calls + screenshots

    fire_at = datetime.now(timezone.utc) + timedelta(seconds=1)
    for i in (1, 2, 3):
        fresh_scheduler.add_job(job, IntervalTrigger(seconds=300, start_date=fire_at),
                                args=[i], id=f"t{i}")

    assert done.wait(timeout=20), f"only {sorted(started)} of 3 tasks ran"
    assert sorted(started) == [1, 2, 3]


def test_cron_triggers_do_collide_on_the_same_minute():
    """Documents why the grace window matters: these are not spread out."""
    marks = {res: next(str(f) for f in trig.fields if f.name == "minute")
             for res, trig in CRON_TRIGGERS.items()}
    assert all("58" in m for m in marks.values()), \
        f"every resolution should share the :58 mark, got {marks}"


def test_dropped_jobs_become_visible_in_the_app_log():
    """APScheduler's warnings previously only went to stderr."""
    from app_logger import get_logs
    _bridge_apscheduler_logs()
    logging.getLogger("apscheduler.executors.default").warning(
        "Run time of job \"x\" was missed by 0:05:00")
    entries = [e for e in get_logs(source="scheduler") if "was missed" in e["message"]]
    assert entries and entries[0]["level"] == "warn"
