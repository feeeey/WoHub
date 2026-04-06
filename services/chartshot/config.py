import os

COOKIE_DIR = os.path.join(os.path.dirname(__file__), "cookies")
COOKIE_FILE = "tradingview.py"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
CHART_LAYOUT_ID = os.environ.get("CHART_LAYOUT_ID", "ndpeiSwl")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))

TIMEFRAME_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "4h": "240", "8h": "480", "1d": "1D", "1w": "1W",
}

VALID_TIMEFRAMES = set(TIMEFRAME_MAP.keys())

SYMBOL_EXCHANGE_MAP = {
    "XAUUSD": "OANDA:XAUUSD",
    "XAGUSD": "OANDA:XAGUSD",
    "USDJPY": "FX:USDJPY",
}

MAX_RETRIES = 3
RETRY_BACKOFF = [3, 5, 8]
