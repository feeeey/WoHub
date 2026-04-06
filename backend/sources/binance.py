from sources.http_client import fetch_with_fallback

BASE = "https://fapi.binance.com"


def _active_symbols():
    resp = fetch_with_fallback("get", f"{BASE}/fapi/v1/exchangeInfo")
    return {
        s["symbol"]
        for s in resp.json()["symbols"]
        if s["status"] == "TRADING" and s["symbol"].endswith("USDT")
    }


def get_tickers():
    active = _active_symbols()
    resp = fetch_with_fallback("get", f"{BASE}/fapi/v1/ticker/24hr")
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
    resp = fetch_with_fallback("get", f"{BASE}/fapi/v1/premiumIndex")
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
