"""Classic indicator math on closed candles. Pure local computation, no I/O.
所有函数对不足样本返回 None/[]（调用方负责向 LLM 解释数据不足）。"""
from statistics import mean, pstdev
from klines.models import Candle
from klines.structure import atr as _atr


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return mean(values[-period:])


def ema_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [mean(values[:period])]
    for v in values[period:]:
        out.append(out[-1] + (v - out[-1]) * k)
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for prev, cur in zip(closes[:-1], closes[1:]):
        chg = cur - prev
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])
    for g, l in zip(gains[period:], losses[period:]):     # Wilder smoothing
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    if len(closes) < slow + signal:
        return None
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    dif = [f - s for f, s in zip(ema_fast[-len(ema_slow):], ema_slow)]
    dea = ema_series(dif, signal)
    if not dea:
        return None
    hist = [d - e for d, e in zip(dif[-len(dea):], dea)]
    cross = "none"
    if len(hist) >= 2:
        if hist[-2] <= 0 < hist[-1]:
            cross = "golden"
        elif hist[-2] >= 0 > hist[-1]:
            cross = "dead"
    return {"dif": round(dif[-1], 6), "dea": round(dea[-1], 6),
            "hist": round(hist[-1], 6), "cross": cross}


def bollinger(closes: list[float], period: int = 20, k: float = 2.0) -> dict | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = mean(window)
    sd = pstdev(window)
    upper, lower = mid + k * sd, mid - k * sd
    pos = 0.5 if upper == lower else (closes[-1] - lower) / (upper - lower)
    return {"upper": round(upper, 8), "mid": round(mid, 8), "lower": round(lower, 8),
            "position": round(min(max(pos, 0.0), 1.0), 4)}


def volume_ratio(volumes: list[float], period: int = 20) -> float | None:
    if len(volumes) < period + 1:
        return None
    base = mean(volumes[-period - 1:-1])
    if base == 0:
        return None
    return round(volumes[-1] / base, 4)


def compute_indicators(candles: list[Candle]) -> dict:
    """聚合六件套。只用已收盘 K 线（未收盘棒会让均线/量比失真）。"""
    closed = [c for c in candles if c.closed]
    closes = [c.close for c in closed]
    vols = [c.volume for c in closed]
    r = rsi(closes)
    a = _atr(candles, period=14)
    last = closes[-1] if closes else None
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    ema55 = ema_series(closes, 55)
    return {
        "ma": {f"ma{p}": (round(v, 8) if (v := sma(closes, p)) is not None else None)
               for p in (5, 10, 20, 50)},
        "ema": {"ema12": round(ema12[-1], 8) if ema12 else None,
                "ema26": round(ema26[-1], 8) if ema26 else None,
                "ema55": round(ema55[-1], 8) if ema55 else None},
        "macd": macd(closes),
        "rsi": {"rsi14": r,
                "state": ("overbought" if r is not None and r >= 70 else
                          "oversold" if r is not None and r <= 30 else "neutral")},
        "boll": bollinger(closes),
        "atr": {"atr14": a,
                "atr_pct": round(a / last * 100, 3) if a is not None and last else None},
        "volume": {"vol_ratio20": volume_ratio(vols)},
    }
