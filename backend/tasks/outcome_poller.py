"""Executes due outcome_checks rows. Restart-safe replacement for the
former threading.Timer scheme: on boot the poller simply picks up any
overdue rows (including those written before a restart)."""
import threading
from database import get_db
from config import settings
from app_logger import log as applog

_stop = threading.Event()
_thread = None


def process_due_checks(limit=50) -> int:
    """Run all due checks once. Returns number processed. Own connection,
    short transactions; safe to call from any thread (incl. tests)."""
    from tasks.tracker import run_outcome_check
    db = get_db(settings.db_path)
    rows = db.execute(
        """SELECT c.id, c.signal_id, c.horizon, s.symbol, s.exchange
           FROM outcome_checks c JOIN signals s ON s.id = c.signal_id
           WHERE c.done = 0 AND c.due_at <= datetime('now')
           ORDER BY c.due_at LIMIT ?""",
        (limit,),
    ).fetchall()
    db.close()
    for r in rows:
        err = run_outcome_check(r["signal_id"], r["symbol"], r["exchange"], r["horizon"])
        db = get_db(settings.db_path)
        db.execute("UPDATE outcome_checks SET done = 1, error = ? WHERE id = ?", (err, r["id"]))
        db.commit()
        db.close()
        if err:
            applog("tracker", "warn", f"outcome check #{r['id']} ({r['symbol']} {r['horizon']}): {err}")
    return len(rows)


def _loop(interval):
    while not _stop.wait(interval):
        try:
            process_due_checks()
        except Exception as e:
            applog("tracker", "error", f"outcome poller: {e}")


def start_poller(interval=60.0):
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), daemon=True, name="outcome-poller")
    _thread.start()


def stop_poller():
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
