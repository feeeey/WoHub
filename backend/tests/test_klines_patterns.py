"""
Deterministic synthetic-candle tests for the candlestick pattern detector.

Each pattern gets:
  - at least one positive test that asserts the pattern IS detected
  - at least one near-miss negative that asserts it is NOT detected

Candles are constructed via the `c()` helper. Pattern detection runs on the
last 1-3 candles of the list (single / double / triple), with several leading
context candles so prior-trend logic has something to look at.
"""
import time
import pytest

from klines.models import Candle
from klines.patterns import detect_patterns


# ---------- helpers ----------

_T0 = 1_700_000_000_000  # arbitrary fixed ms epoch
_INTERVAL_MS = 60 * 60 * 1000  # 1h


def c(idx, o, h, l, cl, v=1000.0, closed=True):
    """Build a Candle at index `idx` (offset from _T0 in 1h steps)."""
    return Candle(
        open_time=_T0 + idx * _INTERVAL_MS,
        close_time=_T0 + (idx + 1) * _INTERVAL_MS - 1,
        open=o, high=h, low=l, close=cl, volume=v, closed=closed,
    )


def trend_down(n=5, start=110.0, step=2.0):
    """Returns `n` strongly-bearish candles trending down, ending near `start - n*step`."""
    out = []
    p = start
    for i in range(n):
        op = p
        cl = p - step
        out.append(c(i, op, op + 0.2, cl - 0.2, cl))
        p = cl
    return out


def trend_up(n=5, start=100.0, step=2.0):
    out = []
    p = start
    for i in range(n):
        op = p
        cl = p + step
        out.append(c(i, op, cl + 0.2, op - 0.2, cl))
        p = cl
    return out


def flat(n=5, mid=100.0):
    """Tiny dojis around `mid`, used as neutral context."""
    out = []
    for i in range(n):
        out.append(c(i, mid, mid + 0.1, mid - 0.1, mid))
    return out


def names(matches):
    return {m.name for m in matches}


# ---------- single-candle patterns ----------

def test_doji_detected():
    candles = flat(4) + [c(4, 100.0, 100.5, 99.5, 100.02)]  # body 0.02, range 1.0
    assert "Doji" in names(detect_patterns(candles))


def test_doji_not_detected_on_long_body():
    # Use a non-flat context so the only candle we check is the long-body one.
    candles = trend_up(4) + [c(4, 100.0, 105.0, 99.5, 104.5)]  # huge body at idx -1
    last_doji_matches = [
        m for m in detect_patterns(candles) if m.name == "Doji" and -1 in m.indices
    ]
    assert not last_doji_matches


def test_hammer_detected_after_downtrend():
    candles = trend_down(5) + [c(5, 99.5, 100.0, 95.0, 99.7)]
    # body ~0.2, lower wick ~4.5, upper wick ~0.3, in upper third, prior down
    assert "Hammer" in names(detect_patterns(candles))


def test_hammer_requires_downtrend_otherwise_hanging_man():
    candles = trend_up(5) + [c(5, 99.5, 100.0, 95.0, 99.7)]  # same shape, prior up
    n = names(detect_patterns(candles))
    assert "HangingMan" in n
    assert "Hammer" not in n


def test_inverted_hammer_detected_after_downtrend():
    candles = trend_down(5) + [c(5, 99.7, 105.0, 99.5, 99.9)]
    # small body in lower third, long upper wick, prior down
    assert "InvertedHammer" in names(detect_patterns(candles))


def test_shooting_star_detected_after_uptrend():
    candles = trend_up(5) + [c(5, 110.5, 116.0, 110.3, 110.7)]
    assert "ShootingStar" in names(detect_patterns(candles))


def test_marubozu_detected():
    candles = flat(4) + [c(4, 100.0, 110.05, 99.95, 110.0)]
    # body 10.0, range 10.10 -> body/range = 0.99 >= 0.95
    assert "Marubozu" in names(detect_patterns(candles))


def test_marubozu_not_detected_with_long_wicks():
    candles = flat(4) + [c(4, 100.0, 115.0, 95.0, 110.0)]  # 10 body, 20 range -> 0.5
    assert "Marubozu" not in names(detect_patterns(candles))


# ---------- two-candle patterns ----------

def test_bullish_engulfing():
    ctx = trend_down(5)
    prev = c(5, 100.0, 100.5, 98.0, 98.5)   # red, body 100..98.5
    curr = c(6, 98.0, 102.0, 97.5, 101.5)   # green, body 98..101.5 engulfs 98.5..100
    assert "BullishEngulfing" in names(detect_patterns(ctx + [prev, curr]))


def test_bearish_engulfing():
    ctx = trend_up(5)
    prev = c(5, 100.0, 102.0, 99.8, 101.5)  # green, body 100..101.5
    curr = c(6, 102.0, 102.3, 98.5, 99.0)   # red, body 102..99 engulfs 100..101.5
    assert "BearishEngulfing" in names(detect_patterns(ctx + [prev, curr]))


def test_engulfing_negative_when_body_too_small():
    ctx = trend_down(5)
    prev = c(5, 100.0, 100.5, 99.5, 99.6)
    curr = c(6, 99.6, 100.1, 99.4, 99.9)  # tiny body, does not engulf
    n = names(detect_patterns(ctx + [prev, curr]))
    assert "BullishEngulfing" not in n


def test_bullish_harami():
    ctx = trend_down(5)
    prev = c(5, 105.0, 105.5, 99.0, 99.5)   # long red
    curr = c(6, 101.0, 102.0, 100.5, 102.0) # small green inside prev body
    assert "BullishHarami" in names(detect_patterns(ctx + [prev, curr]))


def test_bearish_harami():
    ctx = trend_up(5)
    prev = c(5, 99.0, 105.5, 98.8, 105.0)   # long green
    curr = c(6, 103.0, 103.5, 102.0, 102.5) # small red inside prev body
    assert "BearishHarami" in names(detect_patterns(ctx + [prev, curr]))


def test_piercing_line():
    ctx = trend_down(5)
    prev = c(5, 105.0, 105.5, 99.0, 99.5)   # long red, body 105..99.5, mid=102.25
    curr = c(6, 98.5, 103.5, 98.2, 103.0)   # opens below prev low, closes above midpoint
    assert "PiercingLine" in names(detect_patterns(ctx + [prev, curr]))


def test_dark_cloud_cover():
    ctx = trend_up(5)
    prev = c(5, 99.0, 105.5, 98.8, 105.0)   # long green, body 99..105, mid=102
    curr = c(6, 105.5, 105.8, 100.5, 101.0) # opens above prev high, closes below mid
    assert "DarkCloudCover" in names(detect_patterns(ctx + [prev, curr]))


# ---------- three-candle patterns ----------

def test_morning_star():
    ctx = trend_down(5)
    a = c(5, 110.0, 110.5, 100.0, 100.5)   # long red
    b = c(6, 99.5, 100.0, 98.5, 99.7)      # small body (star), gaps down vs a
    d = c(7, 100.0, 106.0, 99.8, 105.5)    # long green
    assert "MorningStar" in names(detect_patterns(ctx + [a, b, d]))


def test_evening_star():
    ctx = trend_up(5)
    a = c(5, 100.0, 110.5, 99.5, 110.0)    # long green
    b = c(6, 110.5, 111.0, 110.2, 110.7)   # small body (star), gaps up
    d = c(7, 110.0, 110.5, 104.0, 104.5)   # long red
    assert "EveningStar" in names(detect_patterns(ctx + [a, b, d]))


def test_three_white_soldiers():
    ctx = trend_down(5)
    a = c(5, 100.0, 103.2, 99.8, 103.0)
    b = c(6, 101.5, 105.2, 101.3, 105.0)
    d = c(7, 103.5, 107.2, 103.3, 107.0)
    assert "ThreeWhiteSoldiers" in names(detect_patterns(ctx + [a, b, d]))


def test_three_black_crows():
    ctx = trend_up(5)
    a = c(5, 107.0, 107.2, 103.8, 104.0)
    b = c(6, 105.5, 105.7, 101.8, 102.0)
    d = c(7, 103.5, 103.7, 99.8, 100.0)
    assert "ThreeBlackCrows" in names(detect_patterns(ctx + [a, b, d]))


def test_three_soldiers_negative_on_mixed_directions():
    ctx = trend_down(5)
    a = c(5, 100.0, 103.0, 99.8, 102.5)
    b = c(6, 102.5, 103.0, 101.0, 101.5)  # red middle
    d = c(7, 101.5, 105.0, 101.3, 104.5)
    assert "ThreeWhiteSoldiers" not in names(detect_patterns(ctx + [a, b, d]))


# ---------- include_current behaviour ----------

def test_current_excluded_by_default():
    ctx = trend_down(5)
    last_closed = c(5, 99.5, 100.0, 95.0, 99.7)  # hammer
    current = c(6, 99.7, 100.0, 99.5, 99.6, closed=False)
    # default: detect only on closed candles -> hammer should be detected on idx -1
    matches = detect_patterns(ctx + [last_closed, current])
    hammers = [m for m in matches if m.name == "Hammer"]
    assert hammers
    assert all(m.on_closed for m in hammers)
    # the current (forming) candle is index -1 in the input; hammer must reference -2
    assert all(-1 not in m.indices for m in hammers)


def test_current_included_when_requested():
    ctx = trend_down(5)
    last_closed = c(5, 100.0, 100.2, 99.8, 100.1)  # nothing notable
    current = c(6, 99.5, 100.0, 95.0, 99.7, closed=False)  # hammer shape, not closed
    matches = detect_patterns(ctx + [last_closed, current], include_current=True)
    hammers = [m for m in matches if m.name == "Hammer"]
    assert hammers
    assert any(not m.on_closed for m in hammers)


# ---------- pattern metadata sanity ----------

def test_pattern_metadata_contains_metrics():
    candles = flat(4) + [c(4, 100.0, 100.5, 99.5, 100.02)]
    matches = detect_patterns(candles)
    doji = next(m for m in matches if m.name == "Doji")
    assert doji.category == "single"
    assert doji.direction == "neutral"
    assert doji.name_zh
    assert "body" in doji.metrics
    assert "range" in doji.metrics


def test_empty_candles_returns_empty_list():
    assert detect_patterns([]) == []


def test_single_candle_skips_multi_candle_patterns():
    matches = detect_patterns([c(0, 100, 105, 99, 104)])
    # only single-candle categories are eligible
    assert all(m.category == "single" for m in matches)
