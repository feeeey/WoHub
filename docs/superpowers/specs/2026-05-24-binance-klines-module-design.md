# Binance Perpetual K-Line & Candlestick Pattern Module — Design

**Date:** 2026-05-24
**Status:** Approved, ready for implementation
**Author:** Claude (brainstorm with user)

## Purpose

Provide a fast, accurate way to fetch any-timeframe K-line data (current forming candle + closed candles) for Binance perpetual futures (USDT-M), and label canonical candlestick patterns on the most recent candles. This module is the data-layer foundation for a future automated-trading subsystem on Binance.

## Non-Goals (this iteration)

- Order placement / signed Binance API
- WebSocket streaming (REST polling only)
- Position / risk management
- Multi-exchange support (Binance only; abstraction not premature-built)
- TA-Lib / heavy native deps
- ccxt — bypasses the project's existing proxy fallback layer and adds unnecessary abstraction for a single-exchange use case

## Module Layout

```
backend/klines/
  __init__.py          # Re-exports the public service API
  fetcher.py           # GET fapi.binance.com/fapi/v1/klines via http_client.fetch_with_fallback
  models.py            # Candle, PatternMatch dataclasses
  patterns.py          # 14 detection rules (single / double / triple)
  service.py           # Orchestrator: fetch -> detect -> assemble response
backend/api/klines.py  # FastAPI router: GET /api/klines/binance
backend/tests/test_klines_patterns.py  # Synthetic-candle unit tests (deterministic)
backend/tests/test_klines_fetcher.py   # @pytest.mark.network live test
```

Rationale: K-lines + pattern detection is a self-contained domain with its own logic; it does not belong inside `sources/binance.py` (which is a thin ticker / funding-rate shell). A dedicated package keeps each file small, focused, and independently testable, and gives the future auto-trading layer a clean import target.

## Data Model

```python
@dataclass
class Candle:
    open_time: int       # ms epoch
    close_time: int      # ms epoch
    open: float
    high: float
    low: float
    close: float
    volume: float        # quote-asset volume (USDT)
    closed: bool         # False ONLY for the in-progress last candle

@dataclass
class PatternMatch:
    name: str            # e.g. "BullishEngulfing"
    name_zh: str         # e.g. "看涨吞没"
    category: str        # "single" | "double" | "triple"
    direction: str       # "bullish" | "bearish" | "neutral"
    indices: list[int]   # Negative indices into the candle list
                         # ([-1] = last closed; [-2,-1] = last two)
    on_closed: bool      # True if detected purely on closed candles
    metrics: dict        # Per-candle drawing helpers:
                         #   body_top, body_bottom, upper_wick, lower_wick, range
```

Negative indices were chosen because the natural mental model is "the last candle", "the second-to-last candle"; this is also what an auto-trading strategy will reach for.

## Fetcher

```python
def fetch_klines(
    symbol: str,                    # forced uppercase, e.g. "BTCUSDT"
    interval: str,                  # one of the Binance fapi whitelist
    limit: int = 100,               # clamped to [1, 1500]
    end_time: int | None = None,    # ms epoch; None -> now
) -> list[Candle]
```

- Endpoint: `GET https://fapi.binance.com/fapi/v1/klines`
- Network: `sources.http_client.fetch_with_fallback` (auto proxy fallback)
- Interval whitelist (validation, no injection): `1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M`
- `closed` is derived from `close_time` vs server-side wallclock: the only `closed=False` is the final candle when its `close_time` is in the future. Important: this judgement uses the local clock, so a clock-skewed host could mislabel one candle; acceptable for v1.

## Pattern Catalog (14 patterns)

Thresholds are constants at the top of `patterns.py` for easy tuning.

### Single-candle (6)
| Name | name_zh | Direction | Rule (informal) |
|------|---------|-----------|-----------------|
| Doji | 十字星 | neutral | `body <= 0.1 * range` |
| Hammer | 锤子线 | bullish | small body in upper third, lower wick >= 2x body, upper wick <= body, prior trend down |
| InvertedHammer | 倒锤子 | bullish | small body in lower third, upper wick >= 2x body, lower wick <= body, prior trend down |
| ShootingStar | 流星 | bearish | shape of InvertedHammer but prior trend up |
| HangingMan | 上吊线 | bearish | shape of Hammer but prior trend up |
| Marubozu | 光头光脚 | continuation | `body >= 0.95 * range` |

Hammer vs HangingMan and InvertedHammer vs ShootingStar are shape-identical; the differentiator is the prior trend, computed from the slope of the prior 3 closed candles (close[-5..-2]). If insufficient history, the shape-identical lookalike is reported instead of skipping.

### Two-candle (5)
- BullishEngulfing / BearishEngulfing — second candle's body fully engulfs the first body, opposite direction
- BullishHarami / BearishHarami — second candle's body fully inside the first body, opposite direction
- PiercingLine / DarkCloudCover — second candle opens past first's close in the opposing direction and closes back past the midpoint

### Three-candle (3)
- MorningStar / EveningStar — long body, small star (gap or near-gap), long body in opposite direction
- ThreeWhiteSoldiers / ThreeBlackCrows — three same-direction long bodies, each closing higher (lower)

## Service Layer

```python
def get_candles_with_patterns(
    symbol: str,
    interval: str,
    limit: int = 100,
    include_current_in_detection: bool = False,
) -> dict
```

Returns:
```json
{
  "symbol": "BTCUSDT",
  "interval": "1h",
  "server_time": 1748000000000,
  "candles": [<Candle>, ...],         // chronological (oldest first)
  "current": <Candle> | null,         // the one with closed=False, if any
  "last_closed": <Candle>,            // last closed candle
  "patterns": [<PatternMatch>, ...]   // by default, only on closed candles
}
```

`include_current_in_detection=False` is the auto-trading-safe default — pattern detection sees only closed candles and will not "repaint". When `True`, the current (forming) candle is appended to the detection input and any pattern touching it is flagged `on_closed=False`.

## HTTP API

```
GET /api/klines/binance
  symbol=BTCUSDT
  interval=1h
  limit=100        (1..1500, default 100)
  include_current=false
```

Auth: existing cookie session via `auth.py` (consistent with `market.py`). Returns the service-layer dict (dataclasses serialized via `dataclasses.asdict`).

## Caching & Rate Limiting

- **No cache.** The current (in-progress) candle must be fresh; the closed history is so cheap to refetch (Binance fapi /klines weight ≤ 5, budget 2400 weight/min) that an upstream cache layer adds risk without meaningful benefit at the foreseeable call volume.
- **No request lock.** Unlike Pine Screener (2-sec lock), Binance klines are a high-frequency-safe endpoint.
- If real production traffic later exposes a bottleneck, layering `http_client.cached()` is a one-line change.

## Testing

1. `test_klines_patterns.py` — **deterministic, synthetic-candle suite**. For each of the 14 patterns: at least one positive and one near-miss negative test. Tests construct `Candle` lists by hand, call `detect_patterns()`, and assert on the returned `PatternMatch` set. This is the safety net for any future threshold tuning.
2. `test_klines_fetcher.py` — `@pytest.mark.network`. Hits real `BTCUSDT 1h limit=5`. Asserts: ≥ 5 candles; only the last has `closed=False`; `open_time` strictly increasing; numeric fields parse as floats.
3. Optional smoke: a tiny `test_klines_service.py` that monkeypatches `fetch_klines` to return a fixed list and checks `service` output shape.

## Forward-Compatibility With Auto-Trading

This module is intentionally pure-functional and side-effect-free (besides the network call). The future auto-trading layer will:

- Call `service.get_candles_with_patterns(symbol, interval, include_current_in_detection=False)` on a polling schedule per timeframe
- Trigger on `on_closed=True` patterns to avoid mid-candle repaint
- Use `Candle.close_time` edge events to detect "a candle just closed"
- Place orders via a *separate*, signed-API module — explicitly out of scope here

## Open Questions

None. All ambiguities were resolved during brainstorming (module location, pattern coverage scope, closed-vs-current semantics, ccxt-vs-native).
