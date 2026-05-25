"""
Tests for divergence direction classification.

The classification is a thin layer over fetch_klines: pull N+2 candles, look at
the close-to-close slope. So we test the logic in isolation by monkey-patching
fetch_klines, plus exercise the public helpers end-to-end.
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
    N_DEFAULT,
)
from klines.models import Candle
from klines.fetcher import KlineRequestError


# ---- helpers ----

def _candle(close, closed=True):
    return Candle(open_time=0, close_time=1, open=close, high=close, low=close,
                  close=close, volume=0, closed=closed)


def _series(*closes_and_flags):
    """Build candles. Items can be `(close, closed)` or just `close`."""
    out = []
    for item in closes_and_flags:
        if isinstance(item, tuple):
            out.append(_candle(item[0], closed=item[1]))
        else:
            out.append(_candle(item))
    return out


# ---- symbol cleaning ----

def test_to_binance_symbol_strips_prefix_and_suffix():
    assert _to_binance_symbol("BINANCE:BTCUSDT.P") == "BTCUSDT"
    assert _to_binance_symbol("OKX:BTCUSDT.P") == "BTCUSDT"
    assert _to_binance_symbol("ETHUSDT") == "ETHUSDT"
    assert _to_binance_symbol("BINANCE:ethusdt.P") == "ETHUSDT"
    assert _to_binance_symbol("BINANCE:ETHUSDT") == "ETHUSDT"


# ---- core classification ----

def _fake_fetch(closes):
    """Return a fetch_klines stand-in that yields the given closes (all closed)."""
    def _f(symbol, interval, limit=100, end_time=None):
        return _series(*closes)
    return _f


def test_classify_returns_top_when_close_above_ref():
    fake = _fake_fetch([100, 101, 102, 103, 104, 105, 110])  # 7 candles
    with patch("sources.divergence_classify.fetch_klines", fake):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == TOP


def test_classify_returns_bottom_when_close_below_ref():
    fake = _fake_fetch([110, 108, 106, 104, 102, 100, 95])
    with patch("sources.divergence_classify.fetch_klines", fake):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == BOTTOM


def test_classify_returns_unclear_on_exact_tie():
    # 7 closed candles; trigger=closes[-1]=99 must equal ref=closes[-6]=99
    fake = _fake_fetch([100, 99, 101, 100, 99, 101, 99])
    with patch("sources.divergence_classify.fetch_klines", fake):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == UNCLEAR


def test_classify_returns_unclear_when_history_insufficient():
    fake = _fake_fetch([100, 101, 102])  # only 3 closed candles, need 6
    with patch("sources.divergence_classify.fetch_klines", fake):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == UNCLEAR


def test_classify_ignores_unclosed_trailing_candle():
    # The 7th candle is unclosed; we should compare closes[-1] (the 6th, closed=100)
    # against closes[-6] (the 1st, closed=110) -> bottom.
    candles = _series(
        (110, True), (108, True), (106, True), (104, True),
        (102, True), (100, True), (95, False),  # trailing unclosed
    )

    def fake(symbol, interval, limit=100, end_time=None):
        return candles

    with patch("sources.divergence_classify.fetch_klines", fake):
        assert classify_divergence("BINANCE:BTCUSDT.P", "1h") == BOTTOM


def test_classify_returns_unclear_on_kline_request_error():
    def fake(symbol, interval, limit=100, end_time=None):
        raise KlineRequestError("bad interval")

    with patch("sources.divergence_classify.fetch_klines", fake):
        assert classify_divergence("BINANCE:BTCUSDT.P", "9z") == UNCLEAR


def test_classify_returns_unclear_on_unexpected_exception():
    # e.g. symbol does not exist on Binance — http_client raises HTTPError
    def fake(symbol, interval, limit=100, end_time=None):
        raise RuntimeError("symbol not found")

    with patch("sources.divergence_classify.fetch_klines", fake):
        assert classify_divergence("BINANCE:NONEXISTENT.P", "1h") == UNCLEAR


def test_classify_uses_n_default_of_5():
    assert N_DEFAULT == 5


# ---- batch + filter ----

def test_classify_batch_preserves_all_inputs():
    def fake(symbol, interval, limit=100, end_time=None):
        # Make BTC top, ETH bottom, SOL unclear (tie)
        sym = symbol.upper()
        if "BTC" in sym:
            return _series(100, 101, 102, 103, 104, 105, 110)
        if "ETH" in sym:
            return _series(110, 108, 106, 104, 102, 100, 95)
        # Force unclear: closes[-1] == closes[-6]
        return _series(100, 99, 101, 100, 99, 101, 99)

    with patch("sources.divergence_classify.fetch_klines", fake):
        out = classify_batch(
            ["BINANCE:BTCUSDT.P", "BINANCE:ETHUSDT.P", "BINANCE:SOLUSDT.P"],
            "1h",
        )
    assert out["BINANCE:BTCUSDT.P"] == TOP
    assert out["BINANCE:ETHUSDT.P"] == BOTTOM
    assert out["BINANCE:SOLUSDT.P"] == UNCLEAR


def test_filter_by_direction_keeps_only_matches():
    def fake(symbol, interval, limit=100, end_time=None):
        sym = symbol.upper()
        if "BTC" in sym:
            return _series(100, 101, 102, 103, 104, 105, 110)  # top
        if "ETH" in sym:
            return _series(110, 108, 106, 104, 102, 100, 95)   # bottom
        # Force unclear: closes[-1] == closes[-6]
        return _series(100, 99, 101, 100, 99, 101, 99)         # unclear

    syms = ["BINANCE:BTCUSDT.P", "BINANCE:ETHUSDT.P", "BINANCE:SOLUSDT.P"]
    with patch("sources.divergence_classify.fetch_klines", fake):
        tops = filter_by_direction(syms, "1h", "top")
        bots = filter_by_direction(syms, "1h", "bottom")
    assert tops == ["BINANCE:BTCUSDT.P"]
    assert bots == ["BINANCE:ETHUSDT.P"]


def test_filter_by_direction_rejects_invalid_direction():
    with pytest.raises(ValueError):
        filter_by_direction(["BINANCE:BTCUSDT.P"], "1h", "sideways")


def test_filter_by_direction_empty_input_returns_empty():
    assert filter_by_direction([], "1h", "top") == []


def test_filter_by_direction_preserves_input_ordering():
    def fake(symbol, interval, limit=100, end_time=None):
        # All three are tops
        return _series(100, 101, 102, 103, 104, 105, 110)

    syms = ["BINANCE:CCC.P", "BINANCE:AAA.P", "BINANCE:BBB.P"]
    with patch("sources.divergence_classify.fetch_klines", fake):
        out = filter_by_direction(syms, "1h", "top")
    assert out == syms  # input ordering preserved despite ThreadPool


# ---- pine_screener integration ----

def test_pine_screener_lists_directional_divergence_options():
    from sources.pine_screener import list_screeners
    labels = {s["label"]: s for s in list_screeners()}
    assert "顶背离" in labels
    assert "底背离" in labels
    # The catch-all 顶底背离 is hidden from the UI list (still callable by code)
    assert "顶底背离" not in labels


def test_pine_screener_run_routes_virtual_to_filter(monkeypatch):
    """Verify divergence_top calls into filter_by_direction(direction='top')."""
    from sources import pine_screener
    captured = {}

    def fake_run(folder_type, screener_name, resolution, watchlist_id):
        # Base divergence call -> return some symbols
        if screener_name == "divergence":
            return ["BINANCE:AAA.P", "BINANCE:BBB.P", "BINANCE:CCC.P"]
        # Recurse into the real run_screener for the virtual key
        return pine_screener._original_run(folder_type, screener_name, resolution, watchlist_id)

    def fake_filter(symbols, resolution, direction, **kwargs):
        captured["direction"] = direction
        # Pretend AAA is the top, others are bottoms
        return ["BINANCE:AAA.P"] if direction == "top" else ["BINANCE:BBB.P", "BINANCE:CCC.P"]

    # Save the real entry, then patch
    pine_screener._original_run = pine_screener.run_screener
    monkeypatch.setattr(pine_screener, "run_screener", fake_run)
    monkeypatch.setattr(
        "sources.divergence_classify.filter_by_direction", fake_filter,
    )

    try:
        # Call the real virtual-screener handler manually
        result = pine_screener._original_run("oscillator", "divergence_top", "1h", 0)
    finally:
        del pine_screener._original_run

    assert result == ["BINANCE:AAA.P"]
    assert captured["direction"] == "top"
