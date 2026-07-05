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
    # 非线程安全：设计为 run_sync 单线程使用
    deep_dive_limit: int = 5
    used: int = 0


def market_snapshot(symbols: list[str]) -> dict:
    """15s TTL 缓存的 ticker/funding 快照，键为 clean symbol。（仅查 Binance 行情）"""
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
    vols = [c.volume for c in closed[-31:-1]]   # 30 根历史棒，剔除当前棒（避免自包含压缩 z 值）
    vol_sigma = pstdev(vols) if len(vols) > 1 else 0.0
    recent = closed[-n:] if len(closed) >= n else closed
    return {
        "symbol": symbol, "interval": interval,
        "last_close": last.close,
        "atr": a, "atr_pct": round(a / last.close * 100, 3) if a is not None else None,
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
    out = {"symbol": symbol, "indicator": indicator, "signals_total": len(rows)}
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


# ---- chat agent tools（Phase 2 追加；全只读）----

def get_klines(symbol: str, interval: str, limit: int = 100) -> dict:
    """紧凑 K 线数组 [[open_time, open, high, low, close, volume], ...]，
    时间升序；limit 钳制到 300（token 保护）。"""
    try:
        limit = max(10, min(int(limit), 300))
    except (TypeError, ValueError):
        return {"error": f"limit 必须是数字，收到 {limit!r}"}
    _throttle()
    try:
        candles = fetch_klines(symbol, interval, limit=limit)
    except Exception as e:
        return {"error": f"K线获取失败: {e}"}
    if not candles:
        return {"error": "K线为空"}
    return {
        "symbol": symbol.upper(), "interval": interval,
        "candles": [[c.open_time, c.open, c.high, c.low, c.close, c.volume]
                    for c in candles],
        "last_closed": candles[-1].closed,
    }


def get_indicators(symbol: str, interval: str) -> dict:
    """核心六件套指标当前值（MA/EMA/MACD/RSI/BOLL/ATR/量比），基于已收盘K线。"""
    from klines.indicators import compute_indicators
    _throttle()
    try:
        candles = fetch_klines(symbol, interval, limit=120)
    except Exception as e:
        return {"error": f"K线获取失败: {e}"}
    closed = [c for c in candles if c.closed]
    if len(closed) < 30:
        return {"error": f"已收盘K线不足（{len(closed)} 根），无法计算指标"}
    return {"symbol": symbol.upper(), "interval": interval,
            "last_close": closed[-1].close,
            "indicators": compute_indicators(candles)}


def list_watchlists() -> dict:
    """TradingView 关注列表（name -> id），run_screener_scan 的前置。"""
    from sources.pine_screener import fetch_watchlists
    try:
        mapping = fetch_watchlists()
        return {"watchlists": [{"name": k, "id": v} for k, v in mapping.items()]}
    except Exception as e:
        return {"error": f"watchlist 获取失败（可能 cookie 过期）: {e}"}


def market_overview(top_n: int = 10) -> dict:
    """涨跌榜 + 资金费率极值（Binance USDT-M）。"""
    try:
        top_n = max(3, min(int(top_n), 20))
        tickers, _ = fetch_all_tickers()
        funding, _ = fetch_all_funding_rates()
        bn = [t for t in tickers if t["exchange"] == "Binance"]
        by_chg = sorted(bn, key=lambda t: t["priceChangePercent"], reverse=True)
        fr = sorted((f for f in funding if f["exchange"] == "Binance"),
                    key=lambda f: f["fundingRate"])
        slim = lambda t: {"symbol": t["symbol"], "lastPrice": t["lastPrice"],
                          "priceChangePercent": t["priceChangePercent"],
                          "volume24h": t["volume24h"]}
        return {"gainers": [slim(t) for t in by_chg[:top_n]],
                "losers": [slim(t) for t in reversed(by_chg[-top_n:])],
                "funding_extremes": {"lowest": fr[:5], "highest": fr[-5:]} if fr else {}}
    except Exception as e:
        return {"error": f"行情总览获取失败: {e}"}


MAX_SCAN_COMBOS = 12    # TradingView 1req/2s：12 combo ≈ 24s，对话可接受上限


def run_screener_scan(screener_keys: list[str], timeframes: list[str],
                      watchlist_id: int, progress_cb=None) -> dict:
    """跑筛选器×周期组合。每个 combo 完成回调 progress_cb(done, total, note)。
    单 combo 失败不中断整个扫描（记入 errors）。受 TradingView 全局 2s 限流。"""
    from sources import pine_screener as ps
    valid = {f"{s['folder_type']}/{s['screener_name']}": s["label"] for s in ps.list_screeners()}
    # 隐藏的合并背离筛选器也允许（向后兼容 key）
    valid.setdefault("oscillator/divergence", ps.SCREENER_NAMES["oscillator/divergence"])
    bad = [k for k in screener_keys if k not in valid]
    if bad:
        return {"error": f"未知筛选器 key: {bad}；可用: {sorted(valid)}"}
    bad_tf = [t for t in timeframes if t not in ps.VALID_RESOLUTIONS]
    if bad_tf:
        return {"error": f"非法周期: {bad_tf}；可用: {sorted(ps.VALID_RESOLUTIONS)}"}
    combos = [(k, tf) for k in screener_keys for tf in timeframes]
    if not combos:
        return {"error": "screener_keys 与 timeframes 都不能为空"}
    if len(combos) > MAX_SCAN_COMBOS:
        return {"error": f"组合数 {len(combos)} 超上限 {MAX_SCAN_COMBOS}"
                         "（限流 1 次/2 秒，请缩小范围分批扫）"}
    results, errors = [], []
    for i, (key, tf) in enumerate(combos, 1):
        folder, name = key.split("/", 1)
        label = valid[key]
        try:
            symbols = ps.run_screener(folder, name, tf, watchlist_id)
            results.append({"key": key, "label": label, "resolution": tf,
                            "symbols": symbols[:100], "count": len(symbols)})
            note = f"{label}@{tf} 命中 {len(symbols)}"
        except Exception as e:
            errors.append({"key": key, "resolution": tf, "error": str(e)[:300]})
            note = f"{label}@{tf} 失败"
        if progress_cb:
            progress_cb(i, len(combos), note)
    return {"results": results, "cross": ps.build_cross_analysis(results),
            "errors": errors,
            "hint": "空 symbols 可能是无信号，也可能是数据源失败——参考 errors 判断"}


def account_overview(credential_id: int) -> dict:
    """余额/持仓/挂单只读总览。本函数绝不 import 任何下单函数。"""
    _throttle()
    try:
        from trading.service import get_account, list_open_orders   # 均为只读
        acct = get_account(credential_id)
        acct["open_orders"] = list_open_orders(credential_id)
        return acct
    except Exception as e:
        return {"error": f"账户查询失败: {e}"}
