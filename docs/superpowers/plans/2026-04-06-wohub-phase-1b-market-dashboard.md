# WoHub Phase 1b: Market Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate CryptoFuturesHub's exchange data aggregation into WoHub and build a live market dashboard with funding rates, gainers/losers, cross-exchange comparison, and auto-refresh.

**Architecture:** Four exchange adapters (Binance, OKX, Bybit, Bitget) normalize data into a common schema. A thread-safe TTL cache prevents API hammering. FastAPI routes serve the data. Vue 3 Market.vue renders live tables with tabs, filtering, search, and 30-second auto-refresh.

**Tech Stack:** Python 3.13 / FastAPI / requests / ThreadPoolExecutor / Vue 3 / Vite

---

## File Map

### Backend — New Files

| File | Responsibility |
|------|---------------|
| `backend/sources/__init__.py` | Empty package init |
| `backend/sources/http_client.py` | Shared requests.Session with proxy support + TTL cache |
| `backend/sources/binance.py` | Binance Futures API adapter |
| `backend/sources/okx.py` | OKX SWAP API adapter |
| `backend/sources/bybit.py` | Bybit Linear API adapter |
| `backend/sources/bitget.py` | Bitget USDT-FUTURES API adapter |
| `backend/sources/exchanges.py` | Aggregator: calls all 4 adapters in parallel, merges results |
| `backend/api/market.py` | Market data API routes |
| `backend/tests/test_exchanges.py` | Exchange adapter + aggregator tests |
| `backend/tests/test_market_api.py` | Market API endpoint tests |

### Backend — Modified Files

| File | Change |
|------|--------|
| `backend/config.py` | Add CACHE_TTL, MIN_VOLUME_24H, PROXY settings |
| `backend/api/__init__.py` | Register market router |
| `backend/pyproject.toml` | Add `requests` dependency |

### Frontend — Modified Files

| File | Change |
|------|--------|
| `frontend/src/views/Market.vue` | Full market dashboard (tabs, tables, search, auto-refresh) |
| `frontend/src/api/client.js` | Add market API methods |

---

### Task 1: Backend Config + HTTP Client + Dependencies

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/config.py`
- Create: `backend/sources/__init__.py`
- Create: `backend/sources/http_client.py`
- Create: `backend/tests/test_http_client.py`

- [ ] **Step 1: Add `requests` to dependencies**

In `backend/pyproject.toml`, add `"requests>=2.31.0"` to the `dependencies` list:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
    "python-multipart>=0.0.9",
    "itsdangerous>=2.2.0",
    "requests>=2.31.0",
]
```

Then install:

```bash
cd backend && source .venv/Scripts/activate && pip install -e ".[dev]"
```

- [ ] **Step 2: Add market config settings**

Add these attributes to the `Settings.__init__` method in `backend/config.py`, after the existing attributes:

```python
        self.cache_ttl = int(os.environ.get("CACHE_TTL", "15"))
        self.min_volume_24h = float(os.environ.get("MIN_VOLUME_24H", "100000"))
        self.proxy_enabled = os.environ.get("PROXY_ENABLED", "false").lower() == "true"
        self.proxy_host = os.environ.get("PROXY_HOST", "127.0.0.1")
        self.proxy_port = os.environ.get("PROXY_PORT", "24000")
```

- [ ] **Step 3: Write tests for http_client**

Create `backend/tests/test_http_client.py`:

```python
import time
from sources.http_client import get_session, cached


def test_get_session_returns_session():
    session = get_session()
    assert hasattr(session, "get")
    assert hasattr(session, "post")


def test_get_session_singleton():
    s1 = get_session()
    s2 = get_session()
    assert s1 is s2


def test_cached_returns_data():
    call_count = 0

    def fetcher():
        nonlocal call_count
        call_count += 1
        return [{"a": 1}], []

    data, errors = cached("test_key_1", fetcher, ttl=10)
    assert data == [{"a": 1}]
    assert errors == []
    assert call_count == 1


def test_cached_uses_cache_on_second_call():
    call_count = 0

    def fetcher():
        nonlocal call_count
        call_count += 1
        return [{"b": 2}], []

    cached("test_key_2", fetcher, ttl=10)
    cached("test_key_2", fetcher, ttl=10)
    assert call_count == 1


def test_cached_expires():
    call_count = 0

    def fetcher():
        nonlocal call_count
        call_count += 1
        return [{"c": 3}], []

    cached("test_key_3", fetcher, ttl=0.1)
    time.sleep(0.15)
    cached("test_key_3", fetcher, ttl=0.1)
    assert call_count == 2
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_http_client.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'sources'`

- [ ] **Step 5: Implement http_client**

Create `backend/sources/__init__.py` (empty file).

Create `backend/sources/http_client.py`:

```python
import threading
import time
import requests
from config import settings

_session = None
_session_lock = threading.Lock()

_cache = {}
_cache_lock = threading.Lock()


def get_session() -> requests.Session:
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        s = requests.Session()
        s.timeout = 10
        s.headers.update({"User-Agent": "WoHub/0.1"})
        if settings.proxy_enabled:
            proxy_url = f"http://{settings.proxy_host}:{settings.proxy_port}"
            s.proxies = {"http": proxy_url, "https": proxy_url}
        _session = s
        return _session


def cached(key: str, fetcher, ttl: float = None):
    if ttl is None:
        ttl = settings.cache_ttl
    now = time.time()
    with _cache_lock:
        entry = _cache.get(key)
        if entry and now - entry["ts"] < ttl:
            return entry["data"], entry["errors"]
    data, errors = fetcher()
    with _cache_lock:
        _cache[key] = {"data": data, "errors": errors, "ts": time.time()}
    return data, errors
```

- [ ] **Step 6: Run tests**

```bash
cd backend && python -m pytest tests/test_http_client.py -v
```

Expected: 5 passed

- [ ] **Step 7: Run all tests**

```bash
cd backend && python -m pytest -v
```

Expected: all pass (previous 15 + new 5 = 20)

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/config.py backend/sources/__init__.py backend/sources/http_client.py backend/tests/test_http_client.py
git commit -m "feat: add HTTP client with TTL cache and proxy support"
```

---

### Task 2: Exchange Adapters

**Files:**
- Create: `backend/sources/binance.py`
- Create: `backend/sources/okx.py`
- Create: `backend/sources/bybit.py`
- Create: `backend/sources/bitget.py`
- Create: `backend/tests/test_exchanges.py`

- [ ] **Step 1: Write adapter tests**

Create `backend/tests/test_exchanges.py`:

```python
"""
Tests for exchange adapters use live API calls.
Mark with @pytest.mark.network so they can be skipped in CI.
We test that adapters return correctly normalized data.
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
```

- [ ] **Step 2: Implement Binance adapter**

Create `backend/sources/binance.py`:

```python
from sources.http_client import get_session

BASE = "https://fapi.binance.com"


def _active_symbols():
    resp = get_session().get(f"{BASE}/fapi/v1/exchangeInfo")
    resp.raise_for_status()
    return {
        s["symbol"]
        for s in resp.json()["symbols"]
        if s["status"] == "TRADING" and s["symbol"].endswith("USDT")
    }


def get_tickers():
    active = _active_symbols()
    resp = get_session().get(f"{BASE}/fapi/v1/ticker/24hr")
    resp.raise_for_status()
    result = []
    for t in resp.json():
        sym = t["symbol"]
        if sym not in active:
            continue
        result.append({
            "symbol": sym,
            "lastPrice": float(t["lastPrice"]),
            "priceChangePercent": float(t["priceChangePercent"]),
            "high24h": float(t["highPrice"]),
            "low24h": float(t["lowPrice"]),
            "volume24h": float(t["quoteVolume"]),
            "exchange": "Binance",
        })
    return result


def get_funding_rates():
    active = _active_symbols()
    resp = get_session().get(f"{BASE}/fapi/v1/premiumIndex")
    resp.raise_for_status()
    result = []
    for f in resp.json():
        sym = f["symbol"]
        if sym not in active:
            continue
        result.append({
            "symbol": sym,
            "fundingRate": float(f.get("lastFundingRate", 0)),
            "markPrice": float(f.get("markPrice", 0)),
            "indexPrice": float(f.get("indexPrice", 0)),
            "nextFundingTime": int(f.get("nextFundingTime", 0)),
            "exchange": "Binance",
        })
    return result
```

- [ ] **Step 3: Implement OKX adapter**

Create `backend/sources/okx.py`:

```python
from concurrent.futures import ThreadPoolExecutor
from sources.http_client import get_session

BASE = "https://www.okx.com"


def _to_symbol(inst_id: str) -> str:
    return inst_id.replace("-USDT-SWAP", "USDT")


def get_tickers():
    resp = get_session().get(f"{BASE}/api/v5/market/tickers?instType=SWAP")
    resp.raise_for_status()
    result = []
    for t in resp.json().get("data", []):
        if "-USDT-SWAP" not in t["instId"]:
            continue
        last = float(t["last"])
        open24h = float(t.get("open24h") or t["last"])
        pct = round(((last - open24h) / open24h * 100), 4) if open24h else 0
        result.append({
            "symbol": _to_symbol(t["instId"]),
            "lastPrice": last,
            "priceChangePercent": pct,
            "high24h": float(t.get("high24h") or 0),
            "low24h": float(t.get("low24h") or 0),
            "volume24h": float(t.get("volCcy24h") or 0),
            "exchange": "OKX",
        })
    return result


def get_funding_rates():
    resp = get_session().get(f"{BASE}/api/v5/public/instruments?instType=SWAP")
    resp.raise_for_status()
    instruments = [
        i["instId"]
        for i in resp.json().get("data", [])
        if "-USDT-SWAP" in i["instId"]
    ]

    def fetch_one(inst_id):
        try:
            r = get_session().get(
                f"{BASE}/api/v5/public/funding-rate?instId={inst_id}",
                timeout=5,
            )
            r.raise_for_status()
            items = r.json().get("data", [])
            if items:
                return items[0]
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        raw = list(pool.map(fetch_one, instruments))

    tickers = {t["symbol"]: t for t in get_tickers()}

    result = []
    for item in raw:
        if not item:
            continue
        sym = _to_symbol(item["instId"])
        ticker = tickers.get(sym, {})
        result.append({
            "symbol": sym,
            "fundingRate": float(item.get("fundingRate", 0)),
            "markPrice": ticker.get("lastPrice", 0.0),
            "indexPrice": 0.0,
            "nextFundingTime": int(item.get("nextFundingTime", 0)),
            "exchange": "OKX",
        })
    return result
```

- [ ] **Step 4: Implement Bybit adapter**

Create `backend/sources/bybit.py`:

```python
from sources.http_client import get_session

BASE = "https://api.bybit.com"


def _fetch_linear():
    resp = get_session().get(f"{BASE}/v5/market/tickers?category=linear")
    resp.raise_for_status()
    return [
        t for t in resp.json().get("result", {}).get("list", [])
        if t["symbol"].endswith("USDT")
    ]


def get_tickers():
    result = []
    for t in _fetch_linear():
        result.append({
            "symbol": t["symbol"],
            "lastPrice": float(t["lastPrice"]),
            "priceChangePercent": round(float(t.get("price24hPcnt", 0)) * 100, 4),
            "high24h": float(t.get("highPrice24h", 0)),
            "low24h": float(t.get("lowPrice24h", 0)),
            "volume24h": float(t.get("turnover24h", 0)),
            "exchange": "Bybit",
        })
    return result


def get_funding_rates():
    result = []
    for t in _fetch_linear():
        result.append({
            "symbol": t["symbol"],
            "fundingRate": float(t.get("fundingRate", 0)),
            "markPrice": float(t.get("markPrice", 0)),
            "indexPrice": float(t.get("indexPrice", 0)),
            "nextFundingTime": int(t.get("nextFundingTime", 0)),
            "exchange": "Bybit",
        })
    return result
```

- [ ] **Step 5: Implement Bitget adapter**

Create `backend/sources/bitget.py`:

```python
from sources.http_client import get_session

BASE = "https://api.bitget.com"


def get_tickers():
    resp = get_session().get(f"{BASE}/api/v2/mix/market/tickers?productType=USDT-FUTURES")
    resp.raise_for_status()
    result = []
    for t in resp.json().get("data", []):
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        result.append({
            "symbol": sym,
            "lastPrice": float(t.get("lastPr", 0)),
            "priceChangePercent": round(float(t.get("change24h", 0)) * 100, 4),
            "high24h": float(t.get("high24h", 0)),
            "low24h": float(t.get("low24h", 0)),
            "volume24h": float(t.get("usdtVolume", 0)),
            "exchange": "Bitget",
        })
    return result


def get_funding_rates():
    resp = get_session().get(
        f"{BASE}/api/v2/mix/market/current-fund-rate?productType=USDT-FUTURES"
    )
    resp.raise_for_status()
    rates = {r["symbol"]: r for r in resp.json().get("data", [])}

    tickers = {t["symbol"]: t for t in get_tickers()}

    result = []
    for sym, r in rates.items():
        if not sym.endswith("USDT"):
            continue
        ticker = tickers.get(sym, {})
        result.append({
            "symbol": sym,
            "fundingRate": float(r.get("fundingRate", 0)),
            "markPrice": ticker.get("lastPrice", 0.0),
            "indexPrice": 0.0,
            "nextFundingTime": int(r.get("nextUpdate", 0)),
            "exchange": "Bitget",
        })
    return result
```

- [ ] **Step 6: Run adapter tests**

```bash
cd backend && python -m pytest tests/test_exchanges.py -v -m network
```

Expected: 8 passed (may take 10-20 seconds due to live API calls). Some tests may fail if network is unavailable — that's expected. At least Binance and Bybit should pass (most reliable).

- [ ] **Step 7: Commit**

```bash
git add backend/sources/binance.py backend/sources/okx.py backend/sources/bybit.py backend/sources/bitget.py backend/tests/test_exchanges.py
git commit -m "feat: add exchange adapters for Binance, OKX, Bybit, Bitget"
```

---

### Task 3: Exchange Aggregator

**Files:**
- Create: `backend/sources/exchanges.py`
- Modify: `backend/tests/test_exchanges.py` (add aggregator tests)

- [ ] **Step 1: Write aggregator tests**

Append to `backend/tests/test_exchanges.py`:

```python
class TestAggregator:
    def test_fetch_all_tickers(self):
        from sources.exchanges import fetch_all_tickers
        data, errors = fetch_all_tickers()
        assert isinstance(data, list)
        assert isinstance(errors, list)
        assert len(data) > 0
        exchanges = {t["exchange"] for t in data}
        assert len(exchanges) >= 2  # at least 2 exchanges responded

    def test_fetch_all_funding_rates(self):
        from sources.exchanges import fetch_all_funding_rates
        data, errors = fetch_all_funding_rates()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_results_are_cached(self):
        from sources.exchanges import fetch_all_tickers
        d1, _ = fetch_all_tickers()
        d2, _ = fetch_all_tickers()
        # Should return same list object from cache
        assert d1 is d2
```

- [ ] **Step 2: Implement aggregator**

Create `backend/sources/exchanges.py`:

```python
from concurrent.futures import ThreadPoolExecutor
from sources.http_client import cached
from sources import binance, okx, bybit, bitget

_EXCHANGES = [
    ("Binance", binance),
    ("OKX", okx),
    ("Bybit", bybit),
    ("Bitget", bitget),
]


def _fetch_parallel(method_name: str):
    def fetcher():
        data = []
        errors = []

        def call(name, mod):
            try:
                fn = getattr(mod, method_name)
                return fn(), None
            except Exception as e:
                return None, {"exchange": name, "error": str(e)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(call, name, mod): name
                for name, mod in _EXCHANGES
            }
            for future in futures:
                result, error = future.result()
                if error:
                    errors.append(error)
                elif result:
                    data.extend(result)
        return data, errors

    return fetcher


def fetch_all_tickers():
    return cached("all_tickers", _fetch_parallel("get_tickers"))


def fetch_all_funding_rates():
    return cached("all_funding_rates", _fetch_parallel("get_funding_rates"))
```

- [ ] **Step 3: Run tests**

```bash
cd backend && python -m pytest tests/test_exchanges.py::TestAggregator -v -m network
```

Expected: 3 passed

- [ ] **Step 4: Commit**

```bash
git add backend/sources/exchanges.py backend/tests/test_exchanges.py
git commit -m "feat: add exchange aggregator with parallel fetching and caching"
```

---

### Task 4: Market API Routes

**Files:**
- Create: `backend/api/market.py`
- Modify: `backend/api/__init__.py`
- Create: `backend/tests/test_market_api.py`

- [ ] **Step 1: Write API tests**

Create `backend/tests/test_market_api.py`:

```python
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
```

- [ ] **Step 2: Implement market routes**

Create `backend/api/market.py`:

```python
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from config import settings
from sources.exchanges import fetch_all_tickers, fetch_all_funding_rates

router = APIRouter(prefix="/market")

_TV_PREFIX = {
    "Binance": "BINANCE",
    "OKX": "OKX",
    "Bybit": "BYBIT",
    "Bitget": "BITGET",
}


@router.get("/funding-rates")
def funding_rates():
    data, errors = fetch_all_funding_rates()
    sorted_data = sorted(data, key=lambda x: abs(x["fundingRate"]), reverse=True)
    return {"data": sorted_data, "errors": errors}


@router.get("/gainers")
def gainers():
    data, errors = fetch_all_tickers()
    filtered = [t for t in data if t["volume24h"] >= settings.min_volume_24h]
    sorted_data = sorted(filtered, key=lambda x: x["priceChangePercent"], reverse=True)
    return {"data": sorted_data[:100], "errors": errors}


@router.get("/losers")
def losers():
    data, errors = fetch_all_tickers()
    filtered = [t for t in data if t["volume24h"] >= settings.min_volume_24h]
    sorted_data = sorted(filtered, key=lambda x: x["priceChangePercent"])
    return {"data": sorted_data[:100], "errors": errors}


@router.get("/compare/{symbol}")
def compare(symbol: str):
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    tickers, t_errors = fetch_all_tickers()
    funding, f_errors = fetch_all_funding_rates()

    ticker_map = {}
    for t in tickers:
        if t["symbol"] == symbol:
            ticker_map[t["exchange"]] = t

    funding_map = {}
    for f in funding:
        if f["symbol"] == symbol:
            funding_map[f["exchange"]] = f

    result = []
    for exchange in ticker_map:
        entry = {**ticker_map[exchange]}
        fr = funding_map.get(exchange, {})
        entry["fundingRate"] = fr.get("fundingRate", 0)
        entry["markPrice"] = fr.get("markPrice", 0)
        entry["nextFundingTime"] = fr.get("nextFundingTime", 0)
        result.append(entry)

    return {"data": result, "errors": t_errors + f_errors}


@router.get("/export")
def export(exchange: str = "all"):
    data, _ = fetch_all_tickers()
    lines = []
    for t in data:
        ex = t["exchange"]
        if exchange != "all" and ex.lower() != exchange.lower():
            continue
        prefix = _TV_PREFIX.get(ex, ex.upper())
        lines.append(f"{prefix}:{t['symbol']}.P")
    lines.sort()
    return PlainTextResponse("\n".join(lines))
```

- [ ] **Step 3: Register market router**

In `backend/api/__init__.py`, add:

```python
from fastapi import APIRouter
from api.health import router as health_router
from auth import router as auth_router
from api.market import router as market_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(market_router)
```

- [ ] **Step 4: Run tests**

```bash
cd backend && python -m pytest tests/test_market_api.py -v
```

Expected: 5 passed

- [ ] **Step 5: Run all tests**

```bash
cd backend && python -m pytest -v -m "not network"
```

Expected: all non-network tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/api/market.py backend/api/__init__.py backend/tests/test_market_api.py
git commit -m "feat: add market API routes (funding, gainers, losers, compare, export)"
```

---

### Task 5: Frontend API Client Update

**Files:**
- Modify: `frontend/src/api/client.js`

- [ ] **Step 1: Add market methods to API client**

Add these methods to the `api` object in `frontend/src/api/client.js`, after the `health()` method:

```js
  async fundingRates() {
    return request('/market/funding-rates')
  },

  async gainers() {
    return request('/market/gainers')
  },

  async losers() {
    return request('/market/losers')
  },

  async compare(symbol) {
    return request(`/market/compare/${encodeURIComponent(symbol)}`)
  },

  async exportList(exchange = 'all') {
    const res = await fetch(`${BASE}/market/export?exchange=${exchange}`)
    return res.text()
  },
```

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.js
git commit -m "feat: add market API methods to frontend client"
```

---

### Task 6: Market Dashboard Vue Component

**Files:**
- Replace: `frontend/src/views/Market.vue`

- [ ] **Step 1: Implement Market.vue**

Replace `frontend/src/views/Market.vue`:

```vue
<template>
  <div>
    <div class="page-header">
      <h1>市场看板</h1>
      <p>实时资金费率与涨跌幅数据</p>
    </div>

    <!-- Tabs -->
    <div class="market-tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="switchTab(tab.key)"
      >{{ tab.label }}</button>
      <div class="tab-spacer"></div>
      <div class="market-meta">
        <span class="refresh-dot" :class="{ loading }"></span>
        <span class="refresh-text">{{ countdown }}s</span>
      </div>
    </div>

    <!-- Errors -->
    <div v-if="errors.length" class="error-bar">
      <span v-for="(e, i) in errors" :key="i">{{ e.exchange }}: {{ e.error }}</span>
    </div>

    <!-- Search + Filter -->
    <div class="market-toolbar">
      <input
        v-model="search"
        placeholder="搜索币种..."
        class="search-input"
      />
      <select v-model="exchangeFilter" class="exchange-select">
        <option value="all">全部交易所</option>
        <option value="Binance">Binance</option>
        <option value="OKX">OKX</option>
        <option value="Bybit">Bybit</option>
        <option value="Bitget">Bitget</option>
      </select>
    </div>

    <!-- Funding Rates Table -->
    <div v-if="activeTab === 'funding'" class="table-wrap card">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>币种</th>
            <th>交易所</th>
            <th>资金费率</th>
            <th>年化</th>
            <th>标记价格</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in filteredData" :key="idx">
            <td class="col-rank">{{ idx + 1 }}</td>
            <td class="col-symbol">{{ item.symbol }}</td>
            <td>{{ item.exchange }}</td>
            <td :class="rateClass(item.fundingRate)">{{ formatRate(item.fundingRate) }}</td>
            <td :class="rateClass(item.fundingRate)">{{ formatAnnual(item.fundingRate) }}</td>
            <td>{{ formatPrice(item.markPrice) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!filteredData.length && !loading" class="table-empty">暂无数据</div>
    </div>

    <!-- Gainers / Losers Table -->
    <div v-if="activeTab === 'gainers' || activeTab === 'losers'" class="table-wrap card">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>币种</th>
            <th>交易所</th>
            <th>最新价</th>
            <th>24h涨跌</th>
            <th>24h最高</th>
            <th>24h最低</th>
            <th>24h成交额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in filteredData" :key="idx">
            <td class="col-rank">{{ idx + 1 }}</td>
            <td class="col-symbol">{{ item.symbol }}</td>
            <td>{{ item.exchange }}</td>
            <td>{{ formatPrice(item.lastPrice) }}</td>
            <td :class="changeClass(item.priceChangePercent)">{{ formatPercent(item.priceChangePercent) }}</td>
            <td>{{ formatPrice(item.high24h) }}</td>
            <td>{{ formatPrice(item.low24h) }}</td>
            <td>{{ formatVolume(item.volume24h) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!filteredData.length && !loading" class="table-empty">暂无数据</div>
    </div>

    <!-- Compare Tab -->
    <div v-if="activeTab === 'compare'" class="card">
      <div class="compare-input">
        <input
          v-model="compareSymbol"
          placeholder="输入币种，如 BTC"
          @keyup.enter="doCompare"
          class="search-input"
        />
        <button class="btn btn-primary btn-sm" @click="doCompare">查询</button>
      </div>
      <table v-if="compareData.length" style="margin-top: 16px">
        <thead>
          <tr>
            <th>交易所</th>
            <th>最新价</th>
            <th>24h涨跌</th>
            <th>资金费率</th>
            <th>24h成交额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in compareData" :key="item.exchange">
            <td>{{ item.exchange }}</td>
            <td>{{ formatPrice(item.lastPrice) }}</td>
            <td :class="changeClass(item.priceChangePercent)">{{ formatPercent(item.priceChangePercent) }}</td>
            <td :class="rateClass(item.fundingRate)">{{ formatRate(item.fundingRate) }}</td>
            <td>{{ formatVolume(item.volume24h) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client.js'

const tabs = [
  { key: 'funding', label: '资金费率' },
  { key: 'gainers', label: '涨幅榜' },
  { key: 'losers', label: '跌幅榜' },
  { key: 'compare', label: '跨所对比' },
]

const activeTab = ref('funding')
const loading = ref(false)
const errors = ref([])
const search = ref('')
const exchangeFilter = ref('all')
const countdown = ref(30)
const rawData = ref([])
const compareSymbol = ref('')
const compareData = ref([])
let timer = null

const filteredData = computed(() => {
  let d = rawData.value
  if (exchangeFilter.value !== 'all') {
    d = d.filter(item => item.exchange === exchangeFilter.value)
  }
  if (search.value) {
    const q = search.value.toUpperCase()
    d = d.filter(item => item.symbol.includes(q))
  }
  return d
})

async function loadData() {
  loading.value = true
  try {
    let result
    if (activeTab.value === 'funding') result = await api.fundingRates()
    else if (activeTab.value === 'gainers') result = await api.gainers()
    else if (activeTab.value === 'losers') result = await api.losers()
    else return
    rawData.value = result.data || []
    errors.value = result.errors || []
  } catch (e) {
    errors.value = [{ exchange: 'Client', error: e.message }]
  } finally {
    loading.value = false
  }
}

async function doCompare() {
  if (!compareSymbol.value) return
  try {
    const result = await api.compare(compareSymbol.value)
    compareData.value = result.data || []
  } catch (e) {
    compareData.value = []
  }
}

function switchTab(key) {
  activeTab.value = key
  rawData.value = []
  if (key !== 'compare') loadData()
}

function startTimer() {
  countdown.value = 30
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      countdown.value = 30
      if (activeTab.value !== 'compare') loadData()
    }
  }, 1000)
}

// Formatting
function formatRate(rate) {
  if (!rate) return '0.0000%'
  return (rate >= 0 ? '+' : '') + (rate * 100).toFixed(4) + '%'
}

function formatAnnual(rate) {
  if (!rate) return '0.00%'
  const annual = rate * 3 * 365 * 100
  return (annual >= 0 ? '+' : '') + annual.toFixed(2) + '%'
}

function formatPercent(pct) {
  if (pct == null) return '-'
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}

function formatPrice(p) {
  if (!p) return '-'
  if (p >= 1000) return p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (p >= 1) return p.toFixed(4)
  return p.toPrecision(4)
}

function formatVolume(v) {
  if (!v) return '-'
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K'
  return v.toFixed(0)
}

function rateClass(rate) {
  if (!rate) return ''
  return rate > 0 ? 'clr-positive' : rate < 0 ? 'clr-negative' : ''
}

function changeClass(pct) {
  if (pct == null) return ''
  return pct > 0 ? 'clr-positive' : pct < 0 ? 'clr-negative' : ''
}

onMounted(() => {
  loadData()
  startTimer()
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.market-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 20px;
}

.tab-btn {
  padding: 8px 18px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: all var(--transition-fast);
}

.tab-btn:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.tab-btn.active {
  color: var(--accent);
  background: var(--accent-subtle);
}

.tab-spacer { flex: 1; }

.market-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-tertiary);
  font-size: 13px;
}

.refresh-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--success);
}

.refresh-dot.loading {
  animation: pulse 0.8s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.error-bar {
  background: var(--danger-subtle);
  color: var(--danger);
  border-radius: var(--radius-sm);
  padding: 10px 16px;
  margin-bottom: 16px;
  font-size: 13px;
  display: flex;
  gap: 16px;
}

.market-toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}

.search-input {
  max-width: 280px;
}

.exchange-select {
  max-width: 180px;
}

.table-wrap {
  overflow-x: auto;
  padding: 0;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

thead th {
  text-align: left;
  padding: 12px 16px;
  color: var(--text-secondary);
  font-weight: 600;
  font-size: 12px;
  letter-spacing: 0.03em;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

tbody td {
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-subtle, var(--border));
  white-space: nowrap;
}

tbody tr:hover {
  background: var(--bg-tertiary);
}

.col-rank {
  color: var(--text-tertiary);
  width: 40px;
}

.col-symbol {
  font-weight: 600;
}

.clr-positive { color: var(--success); }
.clr-negative { color: var(--danger); }

.table-empty {
  text-align: center;
  padding: 40px;
  color: var(--text-tertiary);
}

.compare-input {
  display: flex;
  gap: 12px;
  align-items: center;
}

.compare-input .search-input {
  max-width: 240px;
}
</style>
```

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Market.vue
git commit -m "feat: add market dashboard with funding rates, gainers/losers, and compare"
```

---

### Task 7: Integration Verification

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && source .venv/Scripts/activate && python -m pytest -v -m "not network"
```

Expected: all non-network tests pass

- [ ] **Step 2: Build and deploy**

```bash
cd /c/Users/real/Desktop/WoHub && docker compose up -d --build
```

- [ ] **Step 3: Verify market API**

```bash
curl -s http://localhost:8080/api/market/funding-rates | python -m json.tool | head -20
```

Expected: JSON with `data` array containing funding rate objects from multiple exchanges

```bash
curl -s http://localhost:8080/api/market/gainers | python -m json.tool | head -20
```

Expected: JSON with `data` array sorted by priceChangePercent descending

- [ ] **Step 4: Verify frontend**

Open http://localhost:8080, login, navigate to Market tab. Verify:
- Funding rates table loads with data from multiple exchanges
- Tab switching works (gainers, losers, compare)
- Search filters by symbol
- Exchange dropdown filters by exchange
- Auto-refresh countdown is visible
- Compare tab allows querying a symbol

- [ ] **Step 5: Commit verification**

```bash
git add -A && git status
```

If clean, no commit needed. Otherwise commit any remaining changes.

---

## Phase 1b Deliverables

1. **4 exchange adapters** (Binance, OKX, Bybit, Bitget) with normalized data output
2. **Thread-safe TTL cache** preventing API rate limit issues
3. **5 market API routes** (funding-rates, gainers, losers, compare, export)
4. **Live market dashboard** with tabs, tables, search, filtering, 30-second auto-refresh
5. **Tests**: HTTP client tests + mocked API endpoint tests + live exchange adapter tests
