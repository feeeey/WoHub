"""
Divergence direction classification.

The Pine screener reports symbols with a divergence signal but doesn't say
whether it's a top or bottom divergence. We disambiguate here using the
simplest possible rule: the L0 (阴/阳) classification of the **live** trigger
candle.

  阴线 (close < open) -> TOP    (顶背离)
  阳线 (close >= open) -> BOTTOM (底背离)

IMPORTANT — we look at the current (in-progress) candle, not the previous
closed one. Pine evaluates the divergence on the live bar, so the L0 of that
same live bar is what determines direction. Using the previous closed bar
would offset the judgement by one full timeframe and produce systematic
mis-labels around inflection points.

This rule is intentionally crude — it can mis-label e.g. a small green
bounce that prints right at a top, or a small red retest at a bottom.
Acceptance: false positives will be filtered/refined by downstream logic in
a later iteration. For now we want the cheapest, most predictable rule.

UNCLEAR is reserved for data-layer failures (symbol not on Binance, empty
response, network error) — never for genuine ambiguity in the classification
itself.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

from klines.fetcher import fetch_klines, KlineRequestError
from klines.classification import classify
from app_logger import log as applog

TOP = "top"
BOTTOM = "bottom"
UNCLEAR = "unclear"


def _to_binance_symbol(sym: str) -> str:
    """Convert a Pine-screener symbol ('BINANCE:BTCUSDT.P') to the Binance fapi
    symbol ('BTCUSDT'). Anything before ':' and the trailing '.P' is stripped."""
    s = sym
    if ":" in s:
        s = s.split(":", 1)[1]
    if s.endswith(".P"):
        s = s[:-2]
    return s.upper()


def classify_divergence(symbol: str, interval: str) -> str:
    """Return TOP / BOTTOM / UNCLEAR for one symbol at one interval.

    Looks at the **live** (most-recent) Binance perp candle and labels by
    L0 (阴/阳 by close vs open). If the live candle is still forming, its
    `close` is the current price, which is exactly what the Pine screener
    was looking at when it fired the signal.
    """
    binance_sym = _to_binance_symbol(symbol)
    try:
        # limit=1 is sufficient: fapi returns the in-progress candle as the
        # last (and only here) element when one exists.
        raw = fetch_klines(binance_sym, interval, limit=1)
    except KlineRequestError:
        return UNCLEAR
    except Exception as e:
        applog("divergence_classify", "debug",
               f"klines fetch failed for {binance_sym} {interval}: {e}")
        return UNCLEAR

    if not raw:
        return UNCLEAR

    # The screener fires on the live bar, so we look at the same bar — closed
    # or not. classify() does not care about the closed flag.
    trigger = raw[-1]
    l0 = classify(trigger).l0
    # L0 treats close == open as 阳(1); we inherit that tiebreaker.
    return TOP if l0 == "阴(0)" else BOTTOM


def classify_batch(
    symbols: list[str],
    interval: str,
    max_workers: int = 10,
) -> dict[str, str]:
    """Concurrently classify a batch of symbols. Returns {symbol: direction}."""
    if not symbols:
        return {}
    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(classify_divergence, s, interval): s for s in symbols}
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
) -> list[str]:
    """Keep only symbols whose classified direction matches. Preserves the
    input ordering of `symbols` so upstream rank is not lost."""
    if direction not in (TOP, BOTTOM):
        raise ValueError(f"direction must be 'top' or 'bottom', got {direction!r}")
    if not symbols:
        return []
    classified = classify_batch(symbols, interval)
    return [s for s in symbols if classified.get(s) == direction]
