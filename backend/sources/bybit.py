from sources.http_client import fetch_with_fallback

BASE = "https://api.bybit.com"


def _fetch_linear():
    resp = fetch_with_fallback("get", f"{BASE}/v5/market/tickers?category=linear")
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
