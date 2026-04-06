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
