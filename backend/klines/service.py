"""High-level entry point: fetch klines and detect patterns in one call."""
import time
from dataclasses import asdict
from typing import Any

from klines.fetcher import fetch_klines
from klines.models import Candle
from klines.patterns import detect_patterns


def get_candles_with_patterns(
    symbol: str,
    interval: str,
    limit: int = 100,
    include_current_in_detection: bool = False,
) -> dict[str, Any]:
    """Fetch Binance perpetual klines and detect candlestick patterns.

    Returns a dict with: symbol, interval, server_time, candles, current,
    last_closed, patterns. Dataclasses are serialised via asdict for direct
    JSON encoding.
    """
    candles = fetch_klines(symbol=symbol, interval=interval, limit=limit)
    matches = detect_patterns(candles, include_current=include_current_in_detection)

    current: Candle | None = None
    last_closed: Candle | None = None
    for cd in candles:
        if cd.closed:
            last_closed = cd
        else:
            current = cd

    return {
        "symbol": symbol.upper(),
        "interval": interval,
        "server_time": int(time.time() * 1000),
        "candles": [asdict(c) for c in candles],
        "current": asdict(current) if current else None,
        "last_closed": asdict(last_closed) if last_closed else None,
        "patterns": [m.to_dict() for m in matches],
    }
