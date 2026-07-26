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


@router.get("/symbols")
def symbols(exchange: str = "Binance", limit: int = 800):
    """可选标的列表，按 24h 成交量倒序 —— 给前端搜索选择器做本地过滤用。

    默认只给 Binance：ChartShot 对无前缀标的一律拼成 `BINANCE:{symbol}.P`，
    列出别家的标的会让人选到截不出图的东西。exchange=all 可取全部。
    取数失败时返回空列表 + errors，调用方仍应允许自由输入（ChartShot 也认
    `OANDA:XAUUSD` 这类列表外标的）。
    """
    data, errors = fetch_all_tickers()
    best = {}
    for t in data:
        if exchange != "all" and t["exchange"].lower() != exchange.lower():
            continue
        sym = t["symbol"]
        # 同一标的多交易所都有时，保留成交量最大的那条
        if sym not in best or t["volume24h"] > best[sym]["volume24h"]:
            best[sym] = t

    rows = sorted(best.values(), key=lambda x: -x["volume24h"])[:max(1, min(limit, 2000))]
    return {
        "symbols": [
            {
                "symbol": r["symbol"],
                "exchange": r["exchange"],
                "lastPrice": r["lastPrice"],
                "priceChangePercent": r["priceChangePercent"],
                "volume24h": r["volume24h"],
            }
            for r in rows
        ],
        "errors": errors,
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
