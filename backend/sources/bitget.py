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
