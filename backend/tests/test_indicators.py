import pytest
from klines.indicators import (sma, ema_series, rsi, macd, bollinger,
                               volume_ratio, compute_indicators)
from klines.models import Candle


def test_sma_basic_and_insufficient():
    assert sma([1, 2, 3, 4, 5], 5) == 3.0
    assert sma([1, 2], 5) is None


def test_ema_seeded_with_sma():
    # period=3，seed=SMA(1,2,3)=2.0，k=0.5
    # next: 2+ (4-2)*0.5 = 3.0; then 3 + (5-3)*0.5 = 4.0
    assert ema_series([1, 2, 3, 4, 5], 3) == [2.0, 3.0, 4.0]
    assert ema_series([1, 2], 3) == []


def test_rsi_all_gains_is_100_flat_is_50():
    closes = list(range(1, 20))            # 全涨
    assert rsi(closes, 14) == 100.0
    assert rsi([5.0] * 20, 14) == 50.0     # 无涨无跌


def test_rsi_known_mixed_sequence():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    v = rsi(closes, 14)
    assert v == pytest.approx(70.46, abs=0.1)   # Wilder 教科书序列


def test_macd_constant_series_is_zero():
    out = macd([10.0] * 60)
    assert out["dif"] == pytest.approx(0.0, abs=1e-9)
    assert out["hist"] == pytest.approx(0.0, abs=1e-9)
    assert out["cross"] == "none"
    assert macd([1.0] * 10) is None        # 长度不足 slow+signal


def test_bollinger_constant_band_collapses():
    out = bollinger([10.0] * 25)
    assert out["upper"] == out["mid"] == out["lower"] == 10.0
    assert out["position"] == 0.5          # 带宽为 0 时定义居中


def test_volume_ratio():
    assert volume_ratio([1.0] * 20 + [2.0], 20) == pytest.approx(2.0)
    assert volume_ratio([1.0] * 5, 20) is None


def _mk(i, close, vol=100.0):
    return Candle(open_time=i, close_time=i + 1, open=close, high=close + 1,
                  low=close - 1, close=close, volume=vol, closed=True)


def test_compute_indicators_shape():
    candles = [_mk(i, 100 + i * 0.1) for i in range(80)]
    out = compute_indicators(candles)
    assert set(out) == {"ma", "ema", "macd", "rsi", "boll", "atr", "volume"}
    assert out["ma"]["ma20"] is not None
    assert out["rsi"]["state"] in ("overbought", "oversold", "neutral")


def test_rsi_wilder_recursion_step():
    """Verify RSI with 16 closes forces exactly one Wilder smoothing iteration."""
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
    # seed: avg_gain=3.34/14, avg_loss=1.40/14 -> RSI 70.46
    # one Wilder step with loss 0.28: avg_gain'=(0.238571*13)/14, avg_loss'=(0.1*13+0.28)/14
    # -> RS=1.9629 -> RSI=66.25 (Wilder 教科书续值)
    assert rsi(closes, 14) == pytest.approx(66.25, abs=0.05)


def test_compute_indicators_ignores_unclosed_candle():
    """Verify compute_indicators ignores unclosed candles."""
    closed = [_mk(i, 100 + i * 0.1) for i in range(80)]
    poisoned = closed + [Candle(open_time=80, close_time=81, open=999999.0,
                                high=999999.0, low=999999.0, close=999999.0,
                                volume=999999.0, closed=False)]
    assert compute_indicators(poisoned) == compute_indicators(closed)


def test_rsi_state_exact():
    """Verify RSI state classification for extreme moves."""
    up = [_mk(i, 100 + i) for i in range(80)]          # 单边上涨 -> rsi 100 -> overbought
    down = [_mk(i, 200 - i) for i in range(80)]        # 单边下跌 -> rsi 0 -> oversold
    assert compute_indicators(up)["rsi"]["state"] == "overbought"
    assert compute_indicators(down)["rsi"]["state"] == "oversold"
