"""
Tests for klines.fetcher.

Network tests are marked and skipped by default in `pytest -m "not network"`.
If your local network blocks fapi.binance.com directly, configure the project
proxy via env vars before running:

    PROXY_ENABLED=true PROXY_HOST=127.0.0.1 PROXY_PORT=10809 pytest -m network

Unit tests (no `network` mark) use monkeypatched HTTP and run anywhere.
"""
import json
import types
import pytest

from klines.fetcher import fetch_klines, KlineRequestError, VALID_INTERVALS


# ---------- argument validation (no network) ----------

def test_rejects_unknown_interval():
    with pytest.raises(KlineRequestError):
        fetch_klines("BTCUSDT", "7m", limit=5)


def test_rejects_zero_limit():
    with pytest.raises(KlineRequestError):
        fetch_klines("BTCUSDT", "1h", limit=0)


def test_rejects_oversize_limit():
    with pytest.raises(KlineRequestError):
        fetch_klines("BTCUSDT", "1h", limit=2000)


def test_rejects_empty_symbol():
    with pytest.raises(KlineRequestError):
        fetch_klines("", "1h")


# ---------- parsing + closed-flag logic (no network, monkeypatched HTTP) ----------

def _fake_resp(rows):
    r = types.SimpleNamespace()
    r.json = lambda: rows
    return r


def test_parses_rows_and_marks_only_last_as_unclosed(monkeypatch):
    # close_times: two in the past (closed) + one in the far future (current)
    rows = [
        # open_time, o, h, l, c, vol, close_time, quote_vol, num_trades, ...
        [1_700_000_000_000, "100.0", "101.0", "99.0", "100.5", "1.0",
         1_700_003_599_999, "12345.6", 10],
        [1_700_003_600_000, "100.5", "102.0", "100.0", "101.5", "1.0",
         1_700_007_199_999, "23456.7", 20],
        [1_700_007_200_000, "101.5", "103.0", "101.0", "102.0", "1.0",
         9_999_999_999_999, "34567.8", 30],
    ]
    captured = {}

    def fake_fetch(method, url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _fake_resp(rows)

    monkeypatch.setattr("klines.fetcher.fetch_with_fallback", fake_fetch)

    result = fetch_klines("btcusdt", "1h", limit=3)

    assert captured["url"].endswith("/fapi/v1/klines")
    assert captured["params"]["symbol"] == "BTCUSDT"
    assert captured["params"]["interval"] == "1h"
    assert captured["params"]["limit"] == 3

    assert len(result) == 3
    assert [cd.closed for cd in result] == [True, True, False]
    assert result[0].open == 100.0
    assert result[-1].volume == 34567.8


def test_passes_end_time_to_request(monkeypatch):
    captured = {}

    def fake_fetch(method, url, **kwargs):
        captured["params"] = kwargs.get("params")
        return _fake_resp([])

    monkeypatch.setattr("klines.fetcher.fetch_with_fallback", fake_fetch)
    fetch_klines("BTCUSDT", "1h", limit=5, end_time=1_700_000_000_000)
    assert captured["params"]["endTime"] == 1_700_000_000_000


def test_all_documented_intervals_are_accepted(monkeypatch):
    monkeypatch.setattr("klines.fetcher.fetch_with_fallback",
                        lambda *a, **kw: _fake_resp([]))
    for iv in VALID_INTERVALS:
        # must not raise
        fetch_klines("BTCUSDT", iv, limit=1)


# ---------- live network smoke (opt-in) ----------

@pytest.mark.network
def test_live_btcusdt_1h():
    candles = fetch_klines("BTCUSDT", "1h", limit=5)
    assert len(candles) == 5
    # times strictly increasing
    for a, b in zip(candles, candles[1:]):
        assert b.open_time > a.open_time
    # at most one unclosed candle (the last one), and earlier candles closed
    for cd in candles[:-1]:
        assert cd.closed, "all but possibly the last candle should be closed"
    # numeric sanity
    for cd in candles:
        assert cd.high >= cd.low
        assert cd.high >= max(cd.open, cd.close)
        assert cd.low <= min(cd.open, cd.close)
        assert cd.volume >= 0


@pytest.mark.network
def test_live_service_returns_full_payload():
    from klines.service import get_candles_with_patterns
    payload = get_candles_with_patterns("BTCUSDT", "1h", limit=20)
    assert payload["symbol"] == "BTCUSDT"
    assert payload["interval"] == "1h"
    assert len(payload["candles"]) == 20
    assert payload["last_closed"] is not None
    assert isinstance(payload["patterns"], list)
