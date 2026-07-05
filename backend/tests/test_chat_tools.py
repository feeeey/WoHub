from unittest.mock import patch
from agent import tools as T
from klines.models import Candle


def _mk(i, close, closed=True):
    return Candle(open_time=i * 1000, close_time=i * 1000 + 999, open=close,
                  high=close + 1, low=close - 1, close=close, volume=100.0, closed=closed)


def test_get_klines_compact_and_clamped():
    candles = [_mk(i, 100 + i) for i in range(50)] + [_mk(50, 150, closed=False)]
    with patch.object(T, "fetch_klines", return_value=candles) as fk:
        out = T.get_klines("BTCUSDT", "1h", limit=999)
        assert fk.call_args.kwargs["limit"] == 300          # 钳制
    assert out["candles"][0] == [0, 100.0, 101.0, 99.0, 100.0, 100.0]
    assert out["last_closed"] is False                      # 末棒未收盘如实告知
    assert len(out["candles"]) == 51


def test_get_klines_error_returned_not_raised():
    with patch.object(T, "fetch_klines", side_effect=RuntimeError("boom")):
        out = T.get_klines("BTCUSDT", "1h")
    assert "error" in out


def test_get_indicators_shape():
    candles = [_mk(i, 100 + i * 0.1) for i in range(80)]
    with patch.object(T, "fetch_klines", return_value=candles):
        out = T.get_indicators("BTCUSDT", "4h")
    assert out["symbol"] == "BTCUSDT" and "macd" in out["indicators"]


def test_list_watchlists_wraps_mapping():
    with patch("sources.pine_screener.fetch_watchlists",
               return_value={"主列表": 1, "山寨": 2}):
        out = T.list_watchlists()
    assert {"name": "主列表", "id": 1} in out["watchlists"]


def test_market_overview_sorts_and_trims():
    tickers = [{"symbol": f"S{i}", "exchange": "Binance", "lastPrice": 1,
                "priceChangePercent": float(i), "volume24h": 1000} for i in range(30)]
    funding = [{"symbol": "S1", "exchange": "Binance", "fundingRate": 0.001}]
    with patch.object(T, "fetch_all_tickers", return_value=(tickers, None)), \
         patch.object(T, "fetch_all_funding_rates", return_value=(funding, None)):
        out = T.market_overview(top_n=5)
    assert len(out["gainers"]) == 5 and out["gainers"][0]["priceChangePercent"] == 29.0
    assert len(out["losers"]) == 5 and out["losers"][0]["priceChangePercent"] == 0.0
