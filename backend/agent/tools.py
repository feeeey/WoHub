# backend/agent/tools.py
"""Read-only tools for the agent loop. Every function returns a
JSON-serializable dict; errors are returned as {'error': ...} strings the
LLM can read (never raised). NO order-placing imports allowed here."""
import time
import threading
from dataclasses import dataclass
from statistics import mean, pstdev
from database import get_db
from config import settings
from klines.fetcher import fetch_klines
from klines.patterns import detect_patterns
from klines.classification import classify
from klines.structure import find_pivot, atr
from sources.exchanges import fetch_all_tickers, fetch_all_funding_rates

_MIN_INTERVAL = 0.25          # Binance fapi 无既有限流；工具层自守
_lock = threading.Lock()
_last_call = 0.0


def _throttle():
    global _last_call
    with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


@dataclass
class ToolBudget:
    deep_dive_limit: int = 5
    used: int = 0


def market_snapshot(symbols: list[str]) -> dict:
    """15s TTL 缓存的 ticker/funding 快照，键为 clean symbol。"""
    tickers, _ = fetch_all_tickers()
    funding, _ = fetch_all_funding_rates()
    tmap = {(t["symbol"], t["exchange"]): t for t in tickers}
    fmap = {(f["symbol"], f["exchange"]): f for f in funding}
    out = {}
    for sym in symbols:
        t = tmap.get((sym, "Binance"))
        if not t:
            out[sym] = {"error": "no ticker data"}
            continue
        f = fmap.get((sym, "Binance"))
        out[sym] = {"lastPrice": t["lastPrice"], "priceChangePercent": t["priceChangePercent"],
                    "volume24h": t["volume24h"],
                    "fundingRate": f["fundingRate"] if f else None}
    return out


def kline_summary(symbol: str, interval: str, budget: ToolBudget, n: int = 120) -> dict:
    """K线压缩摘要：形态 + 分类 + 双向 pivot + ATR + 近端统计。受预算与节流约束。"""
    if budget.used >= budget.deep_dive_limit:
        return {"error": f"deep-dive budget exhausted ({budget.deep_dive_limit} per run); "
                         "decide with the evidence you already have"}
    budget.used += 1
    _throttle()
    try:
        candles = fetch_klines(symbol, interval, limit=max(n, 60))
    except Exception as e:
        return {"error": f"kline fetch failed: {e}"}
    closed = [c for c in candles if c.closed]
    if len(closed) < 20:
        return {"error": f"insufficient closed candles: {len(closed)}"}
    last = closed[-1]
    a = atr(candles, period=14)
    piv_long = find_pivot(candles, "long", last.close)
    piv_short = find_pivot(candles, "short", last.close)
    patterns = [{"name_zh": p.name_zh, "direction": p.direction, "category": p.category}
                for p in detect_patterns(candles)[-5:]]
    vols = [c.volume for c in closed[-30:]]
    vol_sigma = pstdev(vols) if len(vols) > 1 else 0.0
    recent = closed[-n:] if len(closed) >= n else closed
    return {
        "symbol": symbol, "interval": interval,
        "last_close": last.close,
        "atr": a, "atr_pct": round(a / last.close * 100, 3) if a else None,
        "pivot_below": piv_long.to_dict() if piv_long else None,
        "pivot_above": piv_short.to_dict() if piv_short else None,
        "last_closed_classification": classify(last).to_dict(),
        "recent_patterns": patterns,   # 方向标签为启发式（趋势消歧），非既定事实
        "recent_stats": {
            "bars": len(recent),
            "change_pct": round((last.close - recent[0].open) / recent[0].open * 100, 3),
            "high": max(c.high for c in recent), "low": min(c.low for c in recent),
            "volume_z_last": round((last.volume - mean(vols)) / vol_sigma, 2) if vol_sigma else 0.0,
        },
    }


def signal_history(symbol: str, indicator: str, limit: int = 30) -> dict:
    """同 symbol×indicator 历史信号的 1h/4h/24h 上涨占比（方向盲的原始收益，
    LLM 须结合信号语义解读）。indicator 为纯 label（Phase 0 统一编码）；
    LIKE 子句兼容迁移前的 'label(res)' 双编码旧数据。"""
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            """SELECT o.change_1h, o.change_4h, o.change_24h
               FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id
               WHERE s.symbol = ? AND (s.indicator = ? OR s.indicator LIKE ? || '(%')
               ORDER BY s.triggered_at DESC LIMIT ?""",
            (symbol, indicator, indicator, limit)).fetchall()
    finally:
        db.close()
    out = {"symbol": symbol, "indicator": indicator, "count": len(rows)}
    for h in ("1h", "4h", "24h"):
        vals = [r[f"change_{h}"] for r in rows if r[f"change_{h}"] is not None]
        out[f"tracked_{h}"] = len(vals)
        out[f"up_rate_{h}"] = round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None
        out[f"avg_change_{h}"] = round(mean(vals), 4) if vals else None
    return out


def position_plan_preview(symbol: str, interval: str, direction: str, credential_id: int) -> dict:
    """只读仓位规划预览（需要专用凭据拉 equity/exchangeInfo，Places NO order）。
    仅当 agent_config.credential_id 已配置时注册为 agent 工具。"""
    _throttle()
    try:
        from trading.service import build_position_plan   # 只读函数；下单函数禁止 import
        return build_position_plan(credential_id=credential_id, symbol=symbol,
                                   interval=interval, direction=direction, order_type="MARKET")
    except Exception as e:
        return {"error": f"plan failed: {e}"}
