"""--live 模式的确定性工具数据。

金标实跑时把 agent.tools 的数据函数全部替换为固定返回值：市场数据不再是
变量，两次评测之间唯一的差异就是「模型 + prompt」——这正是要度量的对象。
runtime 通过 `from agent import tools as T` 引用模块属性，patch 模块属性即可生效。
"""
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from agent.tools import ToolBudget  # noqa: F401  （budget 语义保持真实）

_WATCHLISTS = [{"name": "主列表", "id": 78201040}]

_SNAPSHOT = {
    "BTCUSDT": {"lastPrice": 64375.1, "priceChangePercent": 0.95,
                "volume24h": 2.63e9, "fundingRate": 0.0001},
    "ETHUSDT": {"lastPrice": 3421.5, "priceChangePercent": 1.50,
                "volume24h": 2.25e9, "fundingRate": 0.00008},
    "SOLUSDT": {"lastPrice": 172.3, "priceChangePercent": 1.65,
                "volume24h": 5.5e8, "fundingRate": -0.0002},
}


def _snapshot(symbols):
    return {s: _SNAPSHOT.get(s, {"error": "no ticker data"}) for s in symbols}


def _overview(top_n=10):
    return {"gainers": [{"symbol": "EULUSDT", "lastPrice": 12.1,
                         "priceChangePercent": 63.22, "volume24h": 6.7e8},
                        {"symbol": "1000SHIBUSDT", "lastPrice": 0.021,
                         "priceChangePercent": 32.09, "volume24h": 6.0e8}][:top_n],
            "losers": [{"symbol": "DEXEUSDT", "lastPrice": 7.7,
                        "priceChangePercent": -44.40, "volume24h": 1.27e9}][:top_n],
            "funding_extremes": {
                "lowest": [{"symbol": "SOLUSDT", "fundingRate": -0.0021,
                            "exchange": "Binance"}],
                "highest": [{"symbol": "EULUSDT", "fundingRate": 0.0035,
                             "exchange": "Binance"}]}}


def _klines(symbol, interval, limit=100):
    limit = max(10, min(int(limit), 300))
    base = 64000.0 if symbol.upper().startswith("BTC") else 3400.0
    candles = []
    for i in range(limit):
        # 确定性波形（无随机）：等差相位的三段折线
        drift = (i % 7 - 3) * base * 0.001
        o = base + drift
        c = o + ((i % 3) - 1) * base * 0.0008
        candles.append([1753500000000 + i * 3600_000, o, max(o, c) + 12.0,
                        min(o, c) - 12.0, c, 1000.0 + (i % 5) * 200])
    return {"symbol": symbol.upper(), "interval": interval,
            "candles": candles, "last_closed": True}


def _indicators(symbol, interval):
    return {"symbol": symbol.upper(), "interval": interval, "last_close": 64375.1,
            "indicators": {"ma": {"ma20": 64100.2, "ma60": 63800.9},
                           "ema": {"ema20": 64150.6},
                           "macd": {"dif": 35.2, "dea": 28.9, "hist": 6.3},
                           "rsi": {"rsi14": 57.3},
                           "boll": {"upper": 65200.0, "mid": 64100.0, "lower": 63000.0},
                           "atr": {"atr14": 420.5},
                           "volume": {"ratio": 1.31}}}


def _kline_summary(symbol, interval, budget, n=120):
    if budget.used >= budget.deep_dive_limit:
        return {"error": f"deep-dive budget exhausted ({budget.deep_dive_limit} per run)"}
    budget.used += 1
    return {"symbol": symbol, "interval": interval, "last_close": 64375.1,
            "atr": 420.5, "atr_pct": 0.653,
            "pivot_below": {"price": 63590.0, "kind": "low"},
            "pivot_above": {"price": 65480.0, "kind": "high"},
            "last_closed_classification": {"kind": "含变K线", "direction": "up"},
            "recent_patterns": [{"name_zh": "长下影", "direction": "up",
                                 "category": "reversal"}],
            "recent_stats": {"bars": n, "change_pct": 1.82, "high": 65480.0,
                             "low": 62890.0, "volume_z_last": 0.7}}


def _signal_history(symbol, indicator, limit=30):
    return {"symbol": symbol, "indicator": indicator, "signals_total": 24,
            "tracked_1h": 22, "up_rate_1h": 0.545, "avg_change_1h": 0.12,
            "tracked_4h": 21, "up_rate_4h": 0.571, "avg_change_4h": 0.34,
            "tracked_24h": 20, "up_rate_24h": 0.60, "avg_change_24h": 0.85}


def _screener_stats(days=90):
    return {"window_days": days,
            "stats": {"底背离": {"n": 52,
                                "1h": {"tracked": 50, "up_rate": 0.52, "avg_change": 0.08},
                                "4h": {"tracked": 49, "up_rate": 0.551, "avg_change": 0.31},
                                "24h": {"tracked": 47, "up_rate": 0.574, "avg_change": 0.92}},
                      "超卖": {"n": 364,
                               "1h": {"tracked": 350, "up_rate": 0.503, "avg_change": 0.02},
                               "4h": {"tracked": 348, "up_rate": 0.517, "avg_change": 0.11},
                               "24h": {"tracked": 340, "up_rate": 0.535, "avg_change": 0.4}}},
            "note": "up_rate 是方向盲的原始上涨占比"}


def _list_watchlists():
    return {"watchlists": _WATCHLISTS}


def _run_screener_scan(screener_keys, timeframes, watchlist_id, progress_cb=None):
    if watchlist_id not in {w["id"] for w in _WATCHLISTS}:
        return {"error": f"watchlist {watchlist_id} 不存在"}
    combos = [(k, tf) for k in screener_keys for tf in timeframes]
    results = []
    for i, (key, tf) in enumerate(combos, 1):
        results.append({"key": key, "label": key.split("/")[-1], "resolution": tf,
                        "symbols": ["BTCUSDT", "ARBUSDT"], "count": 2})
        if progress_cb:
            progress_cb(i, len(combos), f"{key}@{tf} 命中 2")
    return {"results": results, "cross": {}, "errors": [],
            "hint": "空 symbols 可能是无信号，也可能是数据源失败"}


def _position_plan(symbol, interval, direction, credential_id):
    return {"symbol": symbol, "direction": direction, "structure_found": True,
            "structure": {"price": 63590.0}, "stop_price": 63400.0,
            "take_profit_price": 66325.0, "entry_price": 64375.1,
            "quantity": 0.12, "risk_amount": 117.0, "rr_ratio": 2.0,
            "leverage": 5, "notional": 7725.0}


def _account_overview(credential_id):
    return {"balance": 11700.0, "available": 9400.0, "unrealized_pnl": 85.2,
            "positions": [{"symbol": "ETHUSDT", "position_amt": 1.5,
                           "entry_price": 3350.0, "unrealized_pnl": 85.2}],
            "open_orders": []}


def _capture_chart(symbol, interval):
    return {"files": [f"{symbol}_{interval}_eval.png"]}


def _save_memory(content, category="preference"):
    # 评测不污染真实记忆库：假装写入成功
    return {"id": 999}


def _forget_memory(memory_id):
    return {"ok": True, "deleted": memory_id}


_PATCHES = {
    "save_memory": _save_memory,
    "forget_memory": _forget_memory,
    "market_snapshot": _snapshot,
    "market_overview": _overview,
    "get_klines": _klines,
    "get_indicators": _indicators,
    "kline_summary": _kline_summary,
    "signal_history": _signal_history,
    "screener_outcome_stats": _screener_stats,
    "list_watchlists": _list_watchlists,
    "run_screener_scan": _run_screener_scan,
    "position_plan_preview": _position_plan,
    "account_overview": _account_overview,
    "capture_chart": _capture_chart,
}


@contextmanager
def apply():
    """把 agent.tools 的全部数据函数替换为确定性 fixture。"""
    with ExitStack() as stack:
        for name, fn in _PATCHES.items():
            stack.enter_context(patch(f"agent.tools.{name}", fn))
        yield
