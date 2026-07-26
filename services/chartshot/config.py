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

# --- 超时预算 ---
# 三层超时必须逐层放大，否则外层先断、内层还在空转，把单 worker 队列堵死：
#   backend requests timeout  >  HTTP 等待(下面两个常量算出)  >  worker 单周期预算
# 指标等待单次上限。重试之间会被 PER_TF_BUDGET 截断，不会真的跑满 3 次。
INDICATOR_WAIT_TIMEOUT = 60
# 单个周期的总预算（指标等待+重试+截图下载）。预算耗尽就用当前画面截图——
# 指标没算完的图也比没有强，且能保证最坏耗时可控。
PER_TF_BUDGET = 100
# 浏览器启动的固定开销
CAPTURE_BASE_OVERHEAD = 30
# 单页导航超时
NAV_TIMEOUT = 45
