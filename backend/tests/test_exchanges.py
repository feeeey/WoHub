"""
Tests for exchange adapters use live API calls.
Mark with @pytest.mark.network so they can be skipped in CI.
"""
import pytest

pytestmark = pytest.mark.network


def _validate_ticker(t):
    assert "symbol" in t
    assert t["symbol"].endswith("USDT")
    assert "lastPrice" in t and isinstance(t["lastPrice"], float)
    assert "priceChangePercent" in t and isinstance(t["priceChangePercent"], float)
    assert "volume24h" in t and isinstance(t["volume24h"], float)
    assert "exchange" in t


def _validate_funding(f):
    assert "symbol" in f
    assert f["symbol"].endswith("USDT")
    assert "fundingRate" in f and isinstance(f["fundingRate"], float)
    assert "exchange" in f


class TestBinance:
    def test_get_tickers(self):
        from sources.binance import get_tickers
        data = get_tickers()
        assert len(data) > 0
        _validate_ticker(data[0])
        assert data[0]["exchange"] == "Binance"

    def test_get_funding_rates(self):
        from sources.binance import get_funding_rates
        data = get_funding_rates()
        assert len(data) > 0
        _validate_funding(data[0])
        assert data[0]["exchange"] == "Binance"


class TestOkx:
    def test_get_tickers(self):
        from sources.okx import get_tickers
        data = get_tickers()
        assert len(data) > 0
        _validate_ticker(data[0])
        assert data[0]["exchange"] == "OKX"

    def test_get_funding_rates(self):
        from sources.okx import get_funding_rates
        data = get_funding_rates()
        assert len(data) > 0
        _validate_funding(data[0])
        assert data[0]["exchange"] == "OKX"


class TestBybit:
    def test_get_tickers(self):
        from sources.bybit import get_tickers
        data = get_tickers()
        assert len(data) > 0
        _validate_ticker(data[0])
        assert data[0]["exchange"] == "Bybit"

    def test_get_funding_rates(self):
        from sources.bybit import get_funding_rates
        data = get_funding_rates()
        assert len(data) > 0
        _validate_funding(data[0])
        assert data[0]["exchange"] == "Bybit"


class TestBitget:
    def test_get_tickers(self):
        from sources.bitget import get_tickers
        data = get_tickers()
        assert len(data) > 0
        _validate_ticker(data[0])
        assert data[0]["exchange"] == "Bitget"

    def test_get_funding_rates(self):
        from sources.bitget import get_funding_rates
        data = get_funding_rates()
        assert len(data) > 0
        _validate_funding(data[0])
        assert data[0]["exchange"] == "Bitget"
