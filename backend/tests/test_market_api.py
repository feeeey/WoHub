"""
Market API tests mock the exchange aggregator to avoid live API calls.
"""
import pytest
from unittest.mock import patch

MOCK_TICKERS = [
    {"symbol": "BTCUSDT", "lastPrice": 50000.0, "priceChangePercent": 5.5,
     "high24h": 52000.0, "low24h": 48000.0, "volume24h": 2000000000.0, "exchange": "Binance"},
    {"symbol": "ETHUSDT", "lastPrice": 3000.0, "priceChangePercent": -2.1,
     "high24h": 3100.0, "low24h": 2900.0, "volume24h": 500000000.0, "exchange": "Binance"},
    {"symbol": "BTCUSDT", "lastPrice": 50010.0, "priceChangePercent": 5.6,
     "high24h": 52010.0, "low24h": 48010.0, "volume24h": 1800000000.0, "exchange": "OKX"},
    {"symbol": "LOWVOL", "lastPrice": 1.0, "priceChangePercent": 10.0,
     "high24h": 1.1, "low24h": 0.9, "volume24h": 50000.0, "exchange": "Binance"},
]

MOCK_FUNDING = [
    {"symbol": "BTCUSDT", "fundingRate": 0.0001, "markPrice": 50000.0,
     "indexPrice": 50001.0, "nextFundingTime": 1680000000000, "exchange": "Binance"},
    {"symbol": "ETHUSDT", "fundingRate": -0.0005, "markPrice": 3000.0,
     "indexPrice": 3001.0, "nextFundingTime": 1680000000000, "exchange": "Binance"},
    {"symbol": "BTCUSDT", "fundingRate": 0.00015, "markPrice": 50010.0,
     "indexPrice": 0.0, "nextFundingTime": 1680000000000, "exchange": "OKX"},
]


def _mock_tickers():
    return MOCK_TICKERS[:], []


def _mock_funding():
    return MOCK_FUNDING[:], []


@pytest.mark.asyncio
@patch("api.market.fetch_all_funding_rates", side_effect=_mock_funding)
async def test_funding_rates(mock_fn, client):
    resp = await client.get("/api/market/funding-rates")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert "errors" in data
    assert len(data["data"]) == 3
    # Sorted by absolute funding rate descending
    assert abs(data["data"][0]["fundingRate"]) >= abs(data["data"][1]["fundingRate"])


@pytest.mark.asyncio
@patch("api.market.fetch_all_tickers", side_effect=_mock_tickers)
async def test_gainers(mock_fn, client):
    resp = await client.get("/api/market/gainers")
    assert resp.status_code == 200
    data = resp.json()
    # LOWVOL should be filtered out (volume < MIN_VOLUME_24H)
    symbols = [d["symbol"] for d in data["data"]]
    assert "LOWVOL" not in symbols
    # Sorted descending by priceChangePercent
    if len(data["data"]) >= 2:
        assert data["data"][0]["priceChangePercent"] >= data["data"][1]["priceChangePercent"]


@pytest.mark.asyncio
@patch("api.market.fetch_all_tickers", side_effect=_mock_tickers)
async def test_losers(mock_fn, client):
    resp = await client.get("/api/market/losers")
    assert resp.status_code == 200
    data = resp.json()
    # Sorted ascending by priceChangePercent
    if len(data["data"]) >= 2:
        assert data["data"][0]["priceChangePercent"] <= data["data"][1]["priceChangePercent"]


@pytest.mark.asyncio
@patch("api.market.fetch_all_tickers", side_effect=_mock_tickers)
@patch("api.market.fetch_all_funding_rates", side_effect=_mock_funding)
async def test_compare(mock_fund, mock_tick, client):
    resp = await client.get("/api/market/compare/BTC")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 2  # Binance + OKX have BTCUSDT
    exchanges = {d["exchange"] for d in data["data"]}
    assert "Binance" in exchanges
    assert "OKX" in exchanges


@pytest.mark.asyncio
@patch("api.market.fetch_all_tickers", side_effect=_mock_tickers)
async def test_compare_auto_appends_usdt(mock_fn, client):
    resp = await client.get("/api/market/compare/btc")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1
