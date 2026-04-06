from concurrent.futures import ThreadPoolExecutor
from sources.http_client import fetch_with_fallback, get_session

BASE = "https://www.okx.com"


def _to_symbol(inst_id: str) -> str:
    return inst_id.replace("-USDT-SWAP", "USDT")


def get_tickers():
    resp = fetch_with_fallback("get", f"{BASE}/api/v5/market/tickers?instType=SWAP")
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
    resp = fetch_with_fallback("get", f"{BASE}/api/v5/public/instruments?instType=SWAP")
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
