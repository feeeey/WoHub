import os
from unittest.mock import patch
from database import get_db


def test_record_signals_exact_rows_no_cross_product():
    from tasks import executor
    entries = [  # (raw_symbol, label, resolution)
        ("BINANCE:BTCUSDT.P", "底背离", "1h"),
        ("BINANCE:BTCUSDT.P", "超卖", "1h"),
        ("BINANCE:ETHUSDT.P", "底背离", "4h"),
    ]
    with patch.object(executor, "record_snapshot"), patch.object(executor, "schedule_outcome_tracking"):
        executor._record_signals(None, entries)
    db = get_db(os.environ["DB_PATH"])
    rows = db.execute("SELECT symbol, indicator, timeframe FROM signals ORDER BY id").fetchall()
    db.close()
    assert [(r["symbol"], r["indicator"], r["timeframe"]) for r in rows] == [
        ("BTCUSDT", "底背离", "1h"), ("BTCUSDT", "超卖", "1h"), ("ETHUSDT", "底背离", "4h")]


def test_record_signals_returns_id_map():
    from tasks import executor
    with patch.object(executor, "record_snapshot"), patch.object(executor, "schedule_outcome_tracking"):
        id_map = executor._record_signals(None, [("BINANCE:BTCUSDT.P", "底背离", "1h")])
    assert list(id_map.keys()) == [("BTCUSDT", "1h")]
    assert len(id_map[("BTCUSDT", "1h")]) == 1
