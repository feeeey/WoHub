"""Binance USDT-M perpetual /fapi/v1/klines fetcher.

Stays consistent with sources/binance.py: GET via http_client.fetch_with_fallback
so it automatically benefits from the proxy fallback mechanism. No external
deps (no ccxt).
"""
import time

from sources.http_client import fetch_with_fallback
from klines.models import Candle


BASE = "https://fapi.binance.com"

VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
}

MIN_LIMIT = 1
MAX_LIMIT = 1500


class KlineRequestError(ValueError):
    """Raised when request arguments are invalid."""


def fetch_klines(
    symbol: str,
    interval: str,
    limit: int = 100,
    end_time: int | None = None,
) -> list[Candle]:
    """Fetch klines from Binance perpetual futures.

    Returns Candles in chronological order. The final candle's `closed` flag
    reflects whether its `close_time` is in the past relative to the local
    clock at fetch time. Note: a clock-skewed host could mislabel the final
    candle; acceptable for v1.
    """
    if not symbol or not isinstance(symbol, str):
        raise KlineRequestError("symbol must be a non-empty string")
    symbol = symbol.upper().strip()

    if interval not in VALID_INTERVALS:
        raise KlineRequestError(
            f"interval must be one of {sorted(VALID_INTERVALS)}, got {interval!r}"
        )

    if not isinstance(limit, int) or limit < MIN_LIMIT or limit > MAX_LIMIT:
        raise KlineRequestError(
            f"limit must be an int in [{MIN_LIMIT}, {MAX_LIMIT}], got {limit!r}"
        )

    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time is not None:
        params["endTime"] = int(end_time)

    resp = fetch_with_fallback("get", f"{BASE}/fapi/v1/klines", params=params)
    rows = resp.json()

    now_ms = int(time.time() * 1000)
    candles: list[Candle] = []
    for row in rows:
        # Binance kline row schema:
        # [open_time, open, high, low, close, volume, close_time,
        #  quote_asset_volume, num_trades, ...]
        open_time = int(row[0])
        close_time = int(row[6])
        candles.append(Candle(
            open_time=open_time,
            close_time=close_time,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[7]),       # quote-asset volume (USDT)
            closed=close_time < now_ms,
        ))

    return candles
