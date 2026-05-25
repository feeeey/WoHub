"""
Divergence direction classification.

The Pine screener reports symbols with a divergence signal but doesn't say
whether it's a top or bottom divergence. We disambiguate here using a simple
heuristic: look at the slope of close prices over the last N closed candles.

  trigger.close > ref.close  ->  uptrend  ->  TOP divergence
  trigger.close < ref.close  ->  downtrend -> BOTTOM divergence
  exact tie / not enough data / symbol unreachable -> UNCLEAR

The reasoning: a divergence signal fires when price and oscillator move in
opposite directions. The screener already confirmed the divergence exists; we
just need to know which side of the trend we're on, and the price trend itself
gives the answer.

N is intentionally small (5) — divergence signals tend to fire AT the trend
inflection, not late, so we want a short recent-trend view.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from klines.fetcher import fetch_klines, KlineRequestError
from app_logger import log as applog

N_DEFAULT = 5
TOP = "top"
BOTTOM = "bottom"
UNCLEAR = "unclear"


def _to_binance_symbol(sym: str) -> str:
    """Convert a Pine-screener symbol string ('BINANCE:BTCUSDT.P') to the
    Binance fapi symbol ('BTCUSDT'). Returns the cleaned form regardless of
    prefix — Binance is the lookup, so we strip everything down to the base.
    """
    s = sym
    if ":" in s:
        s = s.split(":", 1)[1]
    if s.endswith(".P"):
        s = s[:-2]
    return s.upper()


def classify_divergence(symbol: str, interval: str, n: int = N_DEFAULT) -> str:
    """Return TOP / BOTTOM / UNCLEAR for one symbol at one interval.

    UNCLEAR is returned defensively for: insufficient history, Binance does
    not list the symbol, network errors, or an exactly-flat close-to-close.
    """
    binance_sym = _to_binance_symbol(symbol)
    try:
        raw = fetch_klines(binance_sym, interval, limit=n + 2)
    except KlineRequestError:
        return UNCLEAR
    except Exception as e:
        applog("divergence_classify", "debug",
               f"klines fetch failed for {binance_sym} {interval}: {e}")
        return UNCLEAR

    closed = [c for c in raw if c.closed]
    if len(closed) < n + 1:
        return UNCLEAR

    trigger = closed[-1]
    ref = closed[-(n + 1)]

    if trigger.close > ref.close:
        return TOP
    if trigger.close < ref.close:
        return BOTTOM
    return UNCLEAR


def classify_batch(
    symbols: list[str],
    interval: str,
    n: int = N_DEFAULT,
    max_workers: int = 10,
) -> dict[str, str]:
    """Concurrently classify a batch of symbols. Returns {symbol: direction}.

    Concurrency bounded — Binance fapi /klines weight is 1; 2400/min budget,
    so 10 parallel workers won't get rate-limited at realistic batch sizes.
    """
    if not symbols:
        return {}
    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(classify_divergence, s, interval, n): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                out[sym] = fut.result()
            except Exception:
                out[sym] = UNCLEAR
    return out


def filter_by_direction(
    symbols: list[str],
    interval: str,
    direction: str,
    n: int = N_DEFAULT,
) -> list[str]:
    """Keep only the symbols whose classified direction matches.

    Preserves the input ordering of symbols (handy because the upstream
    screener can convey rank via order).
    """
    if direction not in (TOP, BOTTOM):
        raise ValueError(f"direction must be 'top' or 'bottom', got {direction!r}")
    if not symbols:
        return []
    classified = classify_batch(symbols, interval, n=n)
    return [s for s in symbols if classified.get(s) == direction]
