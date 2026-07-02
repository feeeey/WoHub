import os
from unittest.mock import patch
from database import get_db


def test_process_due_checks_marks_done_and_error():
    from tasks import tracker
    from tasks.outcome_poller import process_due_checks
    db = get_db(os.environ["DB_PATH"])
    cur = db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) VALUES (NULL, 'BTCUSDT', 'Binance', 'x', '1h')")
    sid = cur.lastrowid
    db.execute("INSERT INTO snapshots (signal_id, price) VALUES (?, 100.0)", (sid,))
    # one due, one not due
    db.execute("INSERT INTO outcome_checks (signal_id, horizon, due_at) VALUES (?, '1h', datetime('now', '-1 minute'))", (sid,))
    db.execute("INSERT INTO outcome_checks (signal_id, horizon, due_at) VALUES (?, '4h', datetime('now', '+4 hours'))", (sid,))
    db.commit()
    tickers = ([{"symbol": "BTCUSDT", "exchange": "Binance", "lastPrice": 105.0,
                 "priceChangePercent": 0, "volume24h": 0, "high24h": 0, "low24h": 0}], None)
    with patch.object(tracker, "fetch_all_tickers", return_value=tickers):
        n = process_due_checks(limit=50)
    assert n == 1
    rows = db.execute("SELECT horizon, done, error FROM outcome_checks WHERE signal_id = ? ORDER BY horizon", (sid,)).fetchall()
    db.close()
    assert [(r["horizon"], r["done"]) for r in rows] == [("1h", 1), ("4h", 0)]
    assert rows[0]["error"] is None


def test_poller_thread_start_stop():
    import tasks.outcome_poller as p
    p.start_poller(interval=0.05)
    assert p._thread is not None and p._thread.is_alive()
    p.stop_poller()
    assert not p._thread.is_alive()
