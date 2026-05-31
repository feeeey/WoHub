from klines.models import Candle
from klines.structure import find_pivot, StructurePoint, LONG, SHORT, atr


def mk(low, high, t=0, closed=True):
    mid = (low + high) / 2
    return Candle(open_time=t, close_time=t + 1, open=mid, high=high,
                  low=low, close=mid, volume=1.0, closed=closed)


def _long_series():
    # lows with clear pivot lows at index 2 (100) and index 6 (95)
    lows = [120, 115, 100, 112, 118, 116, 95, 108, 110, 113]
    return [mk(low, low + 20, t=i) for i, low in enumerate(lows)]


def _short_series():
    # highs with clear pivot highs at index 2 (100) and index 6 (105)
    highs = [80, 85, 100, 88, 82, 84, 105, 92, 90, 87]
    return [mk(high - 20, high, t=i) for i, high in enumerate(highs)]


def test_long_returns_nearest_confirmed_pivot_low_below_ref():
    sp = find_pivot(_long_series(), LONG, ref_price=109, k=2, lookback=150)
    assert isinstance(sp, StructurePoint)
    assert sp.price == 95
    assert sp.bar_index == 6
    assert sp.age_bars == 3          # last closed idx 9 - 6


def test_long_skips_pivots_at_or_above_ref():
    # ref below every pivot low -> nothing qualifies
    assert find_pivot(_long_series(), LONG, ref_price=90, k=2) is None


def test_short_returns_nearest_confirmed_pivot_high_above_ref():
    sp = find_pivot(_short_series(), SHORT, ref_price=91, k=2)
    assert sp.price == 105
    assert sp.bar_index == 6
    assert sp.age_bars == 3


def test_unclosed_tail_is_ignored_for_confirmation():
    series = _long_series()
    # mark only the last bar in-progress: it drops out of the closed set, but
    # pivot at idx6 still has idx7,8 closed to its right -> still confirmable.
    series[-1] = mk(series[-1].low, series[-1].high, t=9, closed=False)
    sp = find_pivot(series, LONG, ref_price=109, k=2)
    assert sp is not None and sp.bar_index == 6
    # age now relative to last *closed* bar (idx8) -> 8 - 6 = 2
    assert sp.age_bars == 2


def test_too_few_candles_returns_none():
    assert find_pivot([mk(1, 2), mk(1, 2)], LONG, ref_price=5, k=2) is None


def test_invalid_direction_raises():
    import pytest
    with pytest.raises(ValueError):
        find_pivot(_long_series(), "sideways", ref_price=100)


def test_atr_constant_true_range_equals_that_range():
    # every candle: high=110, low=100, close=105 -> TR = 10 for every i>=1
    candles = [mk(100, 110, t=i) for i in range(10)]
    for c in candles:
        c.close = 105
        c.open = 105
    assert atr(candles, period=3) == 10.0


def test_atr_insufficient_data_returns_none():
    candles = [mk(100, 110, t=i) for i in range(3)]
    assert atr(candles, period=14) is None


def test_atr_ignores_unclosed_tail():
    candles = [mk(100, 110, t=i) for i in range(10)]
    for c in candles:
        c.close = 105
        c.open = 105
    candles[-1] = mk(100, 200, t=9, closed=False)  # wild unclosed bar must be ignored
    candles[-1].close = 105
    assert atr(candles, period=3) == 10.0
