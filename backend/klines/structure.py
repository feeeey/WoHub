"""Market-structure helpers: fractal swing pivots and ATR.

Pure functions over a chronological list of Candle (oldest first). No network,
no credentials — fully unit-testable offline and reusable by screeners/strategies.
"""
from dataclasses import dataclass, asdict
from typing import Any

from klines.models import Candle

LONG = "long"
SHORT = "short"


@dataclass
class StructurePoint:
    price: float        # the pivot's low (long) or high (short)
    bar_index: int      # index into the closed-candle list
    bar_time: int       # open_time of the pivot bar
    age_bars: int       # bars back from the last closed candle

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _closed(candles: list[Candle]) -> list[Candle]:
    return [c for c in candles if c.closed]


def _mk_point(closed: list[Candle], i: int, price: float, last_idx: int) -> StructurePoint:
    return StructurePoint(
        price=price,
        bar_index=i,
        bar_time=closed[i].open_time,
        age_bars=last_idx - i,
    )


def find_pivot(
    candles: list[Candle],
    direction: str,
    ref_price: float,
    k: int = 2,
    lookback: int = 150,
) -> StructurePoint | None:
    """Most recent confirmed fractal pivot a stop could sit beyond.

    direction == 'long'  -> nearest confirmed pivot LOW with low  < ref_price
    direction == 'short' -> nearest confirmed pivot HIGH with high > ref_price

    A pivot low at i requires low[i] strictly less than the low of the k bars on
    each side; the k right-hand bars must exist and be closed, so the newest
    detectable pivot is k bars old. Returns None if none found within lookback.
    """
    if direction not in (LONG, SHORT):
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")
    if k < 1:
        raise ValueError("k must be >= 1")

    closed = _closed(candles)
    n = len(closed)
    if n < 2 * k + 1:
        return None

    last_idx = n - 1
    start = last_idx - k                       # newest center with k bars to its right
    lo_bound = max(k, last_idx - lookback)     # oldest center to consider
    for i in range(start, lo_bound - 1, -1):
        c = closed[i]
        if direction == LONG:
            piv = c.low
            if piv >= ref_price:
                continue
            left_ok = all(piv < closed[j].low for j in range(i - k, i))
            right_ok = all(piv < closed[j].low for j in range(i + 1, i + k + 1))
            if left_ok and right_ok:
                return _mk_point(closed, i, piv, last_idx)
        else:
            piv = c.high
            if piv <= ref_price:
                continue
            left_ok = all(piv > closed[j].high for j in range(i - k, i))
            right_ok = all(piv > closed[j].high for j in range(i + 1, i + k + 1))
            if left_ok and right_ok:
                return _mk_point(closed, i, piv, last_idx)
    return None


def atr(candles: list[Candle], period: int = 14) -> float | None:
    """Wilder's ATR over closed candles. None if insufficient data."""
    closed = _closed(candles)
    if len(closed) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(closed)):
        h, l = closed[i].high, closed[i].low
        prev_c = closed[i - 1].close
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    atr_val = sum(trs[:period]) / period          # seed = SMA of first `period` TRs
    for tr in trs[period:]:                        # then Wilder RMA
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val
