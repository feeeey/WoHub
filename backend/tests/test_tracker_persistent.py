import os
from unittest.mock import patch
from database import get_db


def _db():
    return get_db(os.environ["DB_PATH"])


def _mk_signal(db, symbol="BTCUSDT"):
    cur = db.execute(
        "INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) VALUES (NULL, ?, 'Binance', '底背离', '1h')",
        (symbol,))
    db.commit()
    return cur.lastrowid


def test_schedule_inserts_three_checks():
    from tasks.tracker import schedule_outcome_tracking
    db = _db()
    sid = _mk_signal(db)
    schedule_outcome_tracking(sid, "BTCUSDT", "Binance")
    rows = db.execute("SELECT horizon, done FROM outcome_checks WHERE signal_id = ? ORDER BY horizon", (sid,)).fetchall()
    db.close()
    assert [(r["horizon"], r["done"]) for r in rows] == [("1h", 0), ("24h", 0), ("4h", 0)]


TICKERS = ([{"symbol": "BTCUSDT", "exchange": "Binance", "lastPrice": 110.0,
             "priceChangePercent": 1.0, "volume24h": 5e6, "high24h": 0, "low24h": 0}], None)


def test_run_outcome_check_writes_outcome():
    from tasks import tracker
    db = _db()
    sid = _mk_signal(db)
    db.execute("INSERT INTO snapshots (signal_id, price, volume_24h, change_24h, funding_rate) VALUES (?, 100.0, 1e6, 0, 0)", (sid,))
    db.commit()
    with patch.object(tracker, "fetch_all_tickers", return_value=TICKERS):
        err = tracker.run_outcome_check(sid, "BTCUSDT", "Binance", "1h")
    assert err is None
    row = db.execute("SELECT change_1h FROM outcomes WHERE signal_id = ?", (sid,)).fetchone()
    db.close()
    assert abs(row["change_1h"] - 10.0) < 1e-6


def test_run_outcome_check_reports_price_miss():
    from tasks import tracker
    db = _db()
    sid = _mk_signal(db, symbol="NOPE")
    db.execute("INSERT INTO snapshots (signal_id, price) VALUES (?, 100.0)", (sid,))
    db.commit()
    db.close()
    with patch.object(tracker, "fetch_all_tickers", return_value=TICKERS):
        err = tracker.run_outcome_check(sid, "NOPE", "Binance", "1h")
    assert err is not None and "price" in err
