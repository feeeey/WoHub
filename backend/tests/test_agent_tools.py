# backend/tests/test_agent_tools.py
import time
from unittest.mock import patch
from agent.tools import ToolBudget, kline_summary, market_snapshot, signal_history


def _candles(n=60, base=100.0):
    from klines.models import Candle
    out = []
    for i in range(n):
        p = base + i * 0.1
        out.append(Candle(open_time=i * 3600_000, close_time=(i + 1) * 3600_000 - 1,
                          open=p, high=p + 1, low=p - 1, close=p + 0.5,
                          volume=1000.0, closed=True))
    return out


def test_kline_summary_shape_and_budget():
    budget = ToolBudget(deep_dive_limit=1)
    with patch("agent.tools.fetch_klines", return_value=_candles()):
        s = kline_summary("BTCUSDT", "1h", budget)
        s2 = kline_summary("ETHUSDT", "1h", budget)   # 预算耗尽（留在 patch 块内防真实网络调用）
    assert s["symbol"] == "BTCUSDT" and "atr" in s and "recent_patterns" in s
    assert "candles" not in s                     # 绝不返回原始蜡烛数组
    assert "error" in s2 and "budget" in s2["error"]


def test_kline_summary_throttles():
    import agent.tools as t
    budget = ToolBudget(deep_dive_limit=10)
    with patch("agent.tools.fetch_klines", return_value=_candles()):
        t0 = time.monotonic()
        kline_summary("BTCUSDT", "1h", budget)
        kline_summary("ETHUSDT", "1h", budget)
        assert time.monotonic() - t0 >= t._MIN_INTERVAL


def test_market_snapshot_filters_symbols():
    tickers = ([{"symbol": "BTCUSDT", "exchange": "Binance", "lastPrice": 1.0,
                 "priceChangePercent": 2.0, "volume24h": 9.9, "high24h": 0, "low24h": 0}], None)
    funding = ([{"symbol": "BTCUSDT", "exchange": "Binance", "fundingRate": 0.0001}], None)
    with patch("agent.tools.fetch_all_tickers", return_value=tickers), \
         patch("agent.tools.fetch_all_funding_rates", return_value=funding):
        snap = market_snapshot(["BTCUSDT", "MISSING"])
    assert snap["BTCUSDT"]["lastPrice"] == 1.0 and snap["BTCUSDT"]["fundingRate"] == 0.0001
    assert snap["MISSING"] == {"error": "no ticker data"}


def test_signal_history_win_rates(reset_db):
    import os
    from database import get_db
    db = get_db(os.environ["DB_PATH"])
    for chg in (5.0, -2.0, 3.0):
        cur = db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
                         "VALUES (NULL, 'BTCUSDT', 'Binance', '底背离', '1h')")
        db.execute("INSERT INTO outcomes (signal_id, change_4h) VALUES (?, ?)", (cur.lastrowid, chg))
    db.commit()
    db.close()
    h = signal_history("BTCUSDT", "底背离")
    assert h["count"] == 3 and abs(h["up_rate_4h"] - 2 / 3) < 1e-4
