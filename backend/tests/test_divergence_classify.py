"""
Tests for divergence direction classification.

The current rule is the simplest possible: look at L0 (阴/阳 — close vs open
of the trigger candle). False positives are accepted at this layer; downstream
logic refines later.
"""
import pytest
from unittest.mock import patch

from sources.divergence_classify import (
    classify_divergence,
    classify_batch,
    filter_by_direction,
    _to_binance_symbol,
    TOP,
    BOTTOM,
    UNCLEAR,
)
from klines.models import Candle
from klines.fetcher import KlineRequestError


# ---- helpers ----

def _candle(open_, close, closed=True):
    """One candle with a meaningful body. Wicks are zero (irrelevant for L0)."""
    return Candle(
        open_time=0, close_time=1,
        open=open_,
        high=max(open_, close),
        low=min(open_, close),
        close=close,
        volume=0, closed=closed,
    )


# ---- symbol cleaning ----

def test_to_binance_symbol_strips_prefix_and_suffix():
    assert _to_binance_symbol("BINANCE:BTCUSDT.P") == "BTCUSDT"
    assert _to_binance_symbol("OKX:BTCUSDT.P") == "BTCUSDT"
    assert _to_binance_symbol("ETHUSDT") == "ETHUSDT"
    assert _to_binance_symbol("BINANCE:ethusdt.P") == "ETHUSDT"
    assert _to_binance_symbol("BINANCE:ETHUSDT") == "ETHUSDT"


# ---- core L0-based rule ----

def _fake(candles):
    """fetch_klines stand-in that returns the given candles."""
    def _f(symbol, interval, limit=100, end_time=None):
        return list(candles)
    return _f


def test_bearish_trigger_classifies_as_top():
    # close < open => 阴 => 顶背离
    candles = [_candle(open_=100, close=95)]
    with patch("sources.divergence_classify.fetch_klines", _fake(candles)):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == TOP


def test_bullish_trigger_classifies_as_bottom():
    # close > open => 阳 => 底背离
    candles = [_candle(open_=100, close=105)]
    with patch("sources.divergence_classify.fetch_klines", _fake(candles)):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == BOTTOM


def test_doji_classifies_as_bottom_following_l0_tiebreaker():
    # close == open => L0 says 阳(1) => bottom (we inherit that tiebreaker)
    candles = [_candle(open_=100, close=100)]
    with patch("sources.divergence_classify.fetch_klines", _fake(candles)):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == BOTTOM


def test_live_unclosed_candle_is_the_classification_target():
    # The Pine screener fires on the live bar — we must look at THAT bar,
    # not the previous closed one. Here the closed prior bar is bearish (would
    # produce TOP under the old buggy logic) but the live bar is bullish, so
    # the correct answer is BOTTOM.
    candles = [
        _candle(open_=100, close=95, closed=True),    # previous closed: bearish
        _candle(open_=95, close=99, closed=False),    # live (in-progress): bullish
    ]
    with patch("sources.divergence_classify.fetch_klines", _fake(candles)):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == BOTTOM


def test_lone_unclosed_candle_still_classifies():
    # Even if the response contains only the in-progress candle, we use it.
    candles = [_candle(open_=100, close=95, closed=False)]
    with patch("sources.divergence_classify.fetch_klines", _fake(candles)):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == TOP


def test_unclear_when_empty_response():
    with patch("sources.divergence_classify.fetch_klines", _fake([])):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == UNCLEAR


def test_unclear_on_kline_request_error():
    def raise_kr(symbol, interval, limit=100, end_time=None):
        raise KlineRequestError("bad interval")

    with patch("sources.divergence_classify.fetch_klines", raise_kr):
        assert classify_divergence("BINANCE:BTCUSDT.P", "9z") == UNCLEAR


def test_unclear_on_unexpected_exception():
    # e.g. symbol does not exist on Binance — http_client raises HTTPError
    def raise_err(symbol, interval, limit=100, end_time=None):
        raise RuntimeError("symbol not found")

    with patch("sources.divergence_classify.fetch_klines", raise_err):
        assert classify_divergence("BINANCE:NONEXISTENT.P", "1h") == UNCLEAR


# ---- batch + filter ----

def test_classify_batch_returns_all_symbols():
    def fake(symbol, interval, limit=100, end_time=None):
        s = symbol.upper()
        if "BTC" in s:
            return [_candle(open_=100, close=105)]   # bullish -> bottom
        if "ETH" in s:
            return [_candle(open_=100, close=95)]    # bearish -> top
        return []                                     # unreachable -> unclear

    syms = ["BINANCE:BTCUSDT.P", "BINANCE:ETHUSDT.P", "BINANCE:WEIRD.P"]
    with patch("sources.divergence_classify.fetch_klines", fake):
        out = classify_batch(syms, "1h")
    assert out["BINANCE:BTCUSDT.P"] == BOTTOM
    assert out["BINANCE:ETHUSDT.P"] == TOP
    assert out["BINANCE:WEIRD.P"] == UNCLEAR


def test_filter_by_direction_partitions_correctly():
    def fake(symbol, interval, limit=100, end_time=None):
        s = symbol.upper()
        if "BTC" in s:
            return [_candle(open_=100, close=105)]   # bullish -> bottom
        if "ETH" in s:
            return [_candle(open_=100, close=95)]    # bearish -> top
        return []                                     # unreachable -> unclear

    syms = ["BINANCE:BTCUSDT.P", "BINANCE:ETHUSDT.P", "BINANCE:WEIRD.P"]
    with patch("sources.divergence_classify.fetch_klines", fake):
        tops = filter_by_direction(syms, "1h", "top")
        bots = filter_by_direction(syms, "1h", "bottom")
    assert tops == ["BINANCE:ETHUSDT.P"]
    assert bots == ["BINANCE:BTCUSDT.P"]


def test_filter_by_direction_rejects_invalid_direction():
    with pytest.raises(ValueError):
        filter_by_direction(["BINANCE:BTCUSDT.P"], "1h", "sideways")


def test_filter_by_direction_empty_input_returns_empty():
    assert filter_by_direction([], "1h", "top") == []


def test_filter_by_direction_preserves_input_ordering():
    # All bullish -> all bottom; ordering must be preserved
    def fake(symbol, interval, limit=100, end_time=None):
        return [_candle(open_=100, close=105)]

    syms = ["BINANCE:CCC.P", "BINANCE:AAA.P", "BINANCE:BBB.P"]
    with patch("sources.divergence_classify.fetch_klines", fake):
        out = filter_by_direction(syms, "1h", "bottom")
    assert out == syms


# ---- pine_screener integration ----

def test_pine_screener_lists_directional_divergence_options():
    from sources.pine_screener import list_screeners
    labels = {s["label"]: s for s in list_screeners()}
    assert "顶背离" in labels
    assert "底背离" in labels
    # The catch-all 顶底背离 is hidden from the UI list (still callable by code)
    assert "顶底背离" not in labels


def test_pine_screener_run_routes_virtual_to_filter(monkeypatch):
    """divergence_top routes through filter_by_direction(direction='top')."""
    from sources import pine_screener
    captured = {}

    def fake_run(folder_type, screener_name, resolution, watchlist_id):
        if screener_name == "divergence":
            return ["BINANCE:AAA.P", "BINANCE:BBB.P", "BINANCE:CCC.P"]
        return pine_screener._original_run(folder_type, screener_name, resolution, watchlist_id)

    def fake_filter(symbols, resolution, direction, **kwargs):
        captured["direction"] = direction
        return ["BINANCE:AAA.P"] if direction == "top" else ["BINANCE:BBB.P", "BINANCE:CCC.P"]

    pine_screener._original_run = pine_screener.run_screener
    monkeypatch.setattr(pine_screener, "run_screener", fake_run)
    monkeypatch.setattr(
        "sources.divergence_classify.filter_by_direction", fake_filter,
    )

    try:
        result = pine_screener._original_run("oscillator", "divergence_top", "1h", 0)
    finally:
        del pine_screener._original_run

    assert result == ["BINANCE:AAA.P"]
    assert captured["direction"] == "top"
