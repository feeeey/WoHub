# Structure-Based Stop + Risk-Defined Position Sizing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Given a symbol/interval/direction, find the nearest confirmed fractal swing pivot (结构前低/前高), then compute an ATR-buffered stop, a 1.5 R:R take-profit, and a risk-%-of-equity position size — all rounded to Binance symbol filters — and surface it as a read-only `/trading/plan` endpoint plus a 「📐 智能计算」button on the 交易终端.

**Architecture:** Two pure modules — `klines/structure.py` (fractal pivots + ATR, no network) and `trading/position_plan.py` (sizing math + exchange-filter rounding, no network). A network orchestrator `trading.service.build_position_plan` fetches klines + account + exchangeInfo and calls the pure functions. The API endpoint is read-only and never places an order; the user reviews the filled values and submits via the **existing** bracket flow.

**Tech Stack:** Python 3.11 / FastAPI / pytest (backend), Vue 3 `<script setup>` + lightweight-charts (frontend). No new dependencies (Decimal from stdlib for rounding).

**Spec:** `docs/superpowers/specs/2026-05-31-structure-stop-position-plan-design.md`

**Conventions reused:**
- `Candle` dataclass: `open_time, close_time, open, high, low, close, volume, closed` + props `body/range/upper_wick/lower_wick/is_bullish`.
- `fetch_klines(symbol, interval, limit)` → chronological `list[Candle]`, last one may be `closed=False`.
- `trading.service.get_account(cid)` → dict with `total_wallet_balance`, `total_unrealized_pnl`, `available_balance`.
- `trading.binance_client.exchange_info(env, api_key)` → raw fapi exchangeInfo dict.
- `trading.service._resolve(cid)` → `(env, api_key, secret)`; `bn = trading.binance_client`.
- Tests are offline/service-level with `unittest.mock` / monkeypatch; run with `pytest -m "not network"`.

---

## Task 1: `klines/structure.py` — fractal pivot detection

**Files:**
- Create: `backend/klines/structure.py`
- Test: `backend/tests/test_structure.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_structure.py
from klines.models import Candle
from klines.structure import find_pivot, StructurePoint, LONG, SHORT


def mk(low, high, t=0, closed=True):
    mid = (low + high) / 2
    return Candle(open_time=t, close_time=t + 1, open=mid, high=high,
                  low=low, close=mid, volume=1.0, closed=closed)


def _long_series():
    # lows with clear pivot lows at index 2 (100) and index 6 (95)
    lows = [120, 115, 100, 112, 118, 116, 95, 108, 110, 113]
    return [mk(low, low + 20, t=i) for i, low in enumerate(lows)]


def _short_series():
    # highs with clear pivot highs at index 2 (100) and index 6 (105)
    highs = [80, 85, 100, 88, 82, 84, 105, 92, 90, 87]
    return [mk(high - 20, high, t=i) for i, high in enumerate(highs)]


def test_long_returns_nearest_confirmed_pivot_low_below_ref():
    sp = find_pivot(_long_series(), LONG, ref_price=109, k=2, lookback=150)
    assert isinstance(sp, StructurePoint)
    assert sp.price == 95
    assert sp.bar_index == 6
    assert sp.age_bars == 3          # last closed idx 9 - 6


def test_long_skips_pivots_at_or_above_ref():
    # ref below every pivot low -> nothing qualifies
    assert find_pivot(_long_series(), LONG, ref_price=90, k=2) is None


def test_short_returns_nearest_confirmed_pivot_high_above_ref():
    sp = find_pivot(_short_series(), SHORT, ref_price=91, k=2)
    assert sp.price == 105
    assert sp.bar_index == 6
    assert sp.age_bars == 3


def test_unclosed_tail_is_ignored_for_confirmation():
    series = _long_series()
    # mark only the last bar in-progress: it drops out of the closed set, but
    # pivot at idx6 still has idx7,8 closed to its right -> still confirmable.
    series[-1] = mk(series[-1].low, series[-1].high, t=9, closed=False)
    sp = find_pivot(series, LONG, ref_price=109, k=2)
    assert sp is not None and sp.bar_index == 6
    # age now relative to last *closed* bar (idx8) -> 8 - 6 = 2
    assert sp.age_bars == 2


def test_too_few_candles_returns_none():
    assert find_pivot([mk(1, 2), mk(1, 2)], LONG, ref_price=5, k=2) is None


def test_invalid_direction_raises():
    import pytest
    with pytest.raises(ValueError):
        find_pivot(_long_series(), "sideways", ref_price=100)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_structure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'klines.structure'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/klines/structure.py
"""Market-structure helpers: fractal swing pivots and ATR.

Pure functions over a chronological list of Candle (oldest first). No network,
no credentials — fully unit-testable offline and reusable by screeners/strategies.
"""
from dataclasses import dataclass, asdict
from typing import Any

from klines.models import Candle

LONG = "long"
SHORT = "short"


@dataclass
class StructurePoint:
    price: float        # the pivot's low (long) or high (short)
    bar_index: int      # index into the closed-candle list
    bar_time: int       # open_time of the pivot bar
    age_bars: int       # bars back from the last closed candle

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _closed(candles: list[Candle]) -> list[Candle]:
    return [c for c in candles if c.closed]


def _mk_point(closed: list[Candle], i: int, price: float, last_idx: int) -> StructurePoint:
    return StructurePoint(
        price=price,
        bar_index=i,
        bar_time=closed[i].open_time,
        age_bars=last_idx - i,
    )


def find_pivot(
    candles: list[Candle],
    direction: str,
    ref_price: float,
    k: int = 2,
    lookback: int = 150,
) -> StructurePoint | None:
    """Most recent confirmed fractal pivot a stop could sit beyond.

    direction == 'long'  -> nearest confirmed pivot LOW with low  < ref_price
    direction == 'short' -> nearest confirmed pivot HIGH with high > ref_price

    A pivot low at i requires low[i] strictly less than the low of the k bars on
    each side; the k right-hand bars must exist and be closed, so the newest
    detectable pivot is k bars old. Returns None if none found within lookback.
    """
    if direction not in (LONG, SHORT):
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")
    if k < 1:
        raise ValueError("k must be >= 1")

    closed = _closed(candles)
    n = len(closed)
    if n < 2 * k + 1:
        return None

    last_idx = n - 1
    start = last_idx - k                       # newest center with k bars to its right
    lo_bound = max(k, last_idx - lookback)     # oldest center to consider
    for i in range(start, lo_bound - 1, -1):
        c = closed[i]
        if direction == LONG:
            piv = c.low
            if piv >= ref_price:
                continue
            left_ok = all(piv < closed[j].low for j in range(i - k, i))
            right_ok = all(piv < closed[j].low for j in range(i + 1, i + k + 1))
            if left_ok and right_ok:
                return _mk_point(closed, i, piv, last_idx)
        else:
            piv = c.high
            if piv <= ref_price:
                continue
            left_ok = all(piv > closed[j].high for j in range(i - k, i))
            right_ok = all(piv > closed[j].high for j in range(i + 1, i + k + 1))
            if left_ok and right_ok:
                return _mk_point(closed, i, piv, last_idx)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_structure.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/klines/structure.py backend/tests/test_structure.py
git commit -m "feat(structure): fractal swing-pivot detection for stop placement"
```

---

## Task 2: `klines/structure.py` — Wilder ATR

**Files:**
- Modify: `backend/klines/structure.py`
- Test: `backend/tests/test_structure.py`

- [ ] **Step 1: Write the failing tests** (append to `test_structure.py`)

```python
from klines.structure import atr


def test_atr_constant_true_range_equals_that_range():
    # every candle: high=110, low=100, close=105 -> TR = 10 for every i>=1
    candles = [mk(100, 110, t=i) for i in range(10)]
    for c in candles:
        c.close = 105
        c.open = 105
    assert atr(candles, period=3) == 10.0


def test_atr_insufficient_data_returns_none():
    candles = [mk(100, 110, t=i) for i in range(3)]
    assert atr(candles, period=14) is None


def test_atr_ignores_unclosed_tail():
    candles = [mk(100, 110, t=i) for i in range(10)]
    for c in candles:
        c.close = 105
        c.open = 105
    candles[-1] = mk(100, 200, t=9, closed=False)  # wild unclosed bar must be ignored
    candles[-1].close = 105
    assert atr(candles, period=3) == 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_structure.py -k atr -v`
Expected: FAIL — `ImportError: cannot import name 'atr'`

- [ ] **Step 3: Write minimal implementation** (append to `structure.py`)

```python
def atr(candles: list[Candle], period: int = 14) -> float | None:
    """Wilder's ATR over closed candles. None if insufficient data."""
    closed = _closed(candles)
    if len(closed) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(closed)):
        h, l = closed[i].high, closed[i].low
        prev_c = closed[i - 1].close
        trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    atr_val = sum(trs[:period]) / period          # seed = SMA of first `period` TRs
    for tr in trs[period:]:                        # then Wilder RMA
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_structure.py -v`
Expected: PASS (9 tests total)

- [ ] **Step 5: Commit**

```bash
git add backend/klines/structure.py backend/tests/test_structure.py
git commit -m "feat(structure): Wilder ATR over closed candles"
```

---

## Task 3: `trading/position_plan.py` — filters + rounding helpers

**Files:**
- Create: `backend/trading/position_plan.py`
- Test: `backend/tests/test_position_plan.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_position_plan.py
import pytest
from trading.position_plan import (
    SymbolFilters, parse_filters, _round_step,
)


def _fake_info(symbol="BTCUSDT", notional_type="MIN_NOTIONAL"):
    return {"symbols": [{
        "symbol": symbol,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": notional_type, "notional": "5"},
        ],
    }]}


def test_parse_filters_extracts_all_fields():
    f = parse_filters(_fake_info(), "BTCUSDT")
    assert f == SymbolFilters(tick_size=0.1, step_size=0.001, min_qty=0.001, min_notional=5.0)


def test_parse_filters_symbol_case_insensitive():
    f = parse_filters(_fake_info(), "btcusdt")
    assert f.tick_size == 0.1


def test_parse_filters_unknown_symbol_raises():
    with pytest.raises(ValueError):
        parse_filters(_fake_info(), "ETHUSDT")


def test_round_step_floor_ceil_nearest():
    assert _round_step(94.47, 0.10, "floor") == 94.4
    assert _round_step(94.41, 0.10, "ceil") == 94.5
    assert _round_step(108.44, 0.10, "nearest") == 108.4
    assert _round_step(108.46, 0.10, "nearest") == 108.5


def test_round_step_zero_step_is_passthrough():
    assert _round_step(123.456, 0, "floor") == 123.456
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_position_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trading.position_plan'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/trading/position_plan.py
"""Pure position-planning math: structure-based stop, R:R take-profit, and
risk-defined position sizing, with Binance symbol-filter rounding.

No network and no credentials — all inputs are passed in. The network
orchestration lives in trading.service.build_position_plan.
"""
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
from typing import Any

from klines.structure import StructurePoint, LONG, SHORT


@dataclass
class SymbolFilters:
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float


def parse_filters(exchange_info: dict, symbol: str) -> SymbolFilters:
    """Pull tick/step/minQty/minNotional out of a raw fapi exchangeInfo dict."""
    symbol = symbol.upper()
    for s in exchange_info.get("symbols", []):
        if s.get("symbol") == symbol:
            tick = step = min_qty = min_notional = 0.0
            for f in s.get("filters", []):
                t = f.get("filterType")
                if t == "PRICE_FILTER":
                    tick = float(f["tickSize"])
                elif t == "LOT_SIZE":
                    step = float(f["stepSize"])
                    min_qty = float(f["minQty"])
                elif t in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_notional = float(f.get("notional", f.get("minNotional", 0)))
            return SymbolFilters(tick, step, min_qty, min_notional)
    raise ValueError(f"symbol {symbol!r} not found in exchangeInfo")


def _round_step(value: float, step: float, mode: str) -> float:
    """Round `value` to a multiple of `step`. mode: floor | ceil | nearest.
    Uses Decimal to avoid binary-float fuzz on exchange increments."""
    if step <= 0:
        return value
    q = Decimal(str(value)) / Decimal(str(step))
    rounding = {"floor": ROUND_FLOOR, "ceil": ROUND_CEILING}.get(mode, ROUND_HALF_UP)
    q = q.to_integral_value(rounding=rounding)
    return float(q * Decimal(str(step)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_position_plan.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/trading/position_plan.py backend/tests/test_position_plan.py
git commit -m "feat(position_plan): symbol-filter parsing + step rounding"
```

---

## Task 4: `trading/position_plan.py` — `compute_plan` (the core math)

**Files:**
- Modify: `backend/trading/position_plan.py`
- Test: `backend/tests/test_position_plan.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
from trading.position_plan import compute_plan, PositionPlan
from klines.structure import StructurePoint, LONG, SHORT

FILTERS = SymbolFilters(tick_size=0.1, step_size=0.001, min_qty=0.001, min_notional=5.0)


def test_compute_long_with_structure():
    sp = StructurePoint(price=95.0, bar_index=6, bar_time=0, age_bars=3)
    plan = compute_plan(
        direction=LONG, entry_price=100.0, structure=sp, atr_value=2.0,
        equity=10000.0, available_balance=10000.0, leverage=10, filters=FILTERS,
        risk_pct=1.0, rr=1.5, atr_mult=0.3,
    )
    assert plan.structure_found is True
    assert plan.stop_price == pytest.approx(94.4)        # 95 - 0.3*2 = 94.4, floored to 0.1
    assert plan.stop_distance == pytest.approx(5.6)
    assert plan.take_profit_price == pytest.approx(108.4)  # 100 + 1.5*5.6
    assert plan.risk_amount == pytest.approx(100.0)        # 10000 * 1%
    assert plan.quantity == pytest.approx(17.857)          # floor(100/5.6, 0.001)
    assert plan.feasible is True
    assert plan.warnings == []


def test_compute_short_with_structure_is_symmetric():
    sp = StructurePoint(price=105.0, bar_index=6, bar_time=0, age_bars=3)
    plan = compute_plan(
        direction=SHORT, entry_price=100.0, structure=sp, atr_value=2.0,
        equity=10000.0, available_balance=10000.0, leverage=10, filters=FILTERS,
        risk_pct=1.0, rr=1.5, atr_mult=0.3,
    )
    assert plan.stop_price == pytest.approx(105.6)         # 105 + 0.6, ceiled
    assert plan.stop_distance == pytest.approx(5.6)
    assert plan.take_profit_price == pytest.approx(91.6)   # 100 - 1.5*5.6


def test_compute_atr_fallback_when_no_structure():
    plan = compute_plan(
        direction=LONG, entry_price=100.0, structure=None, atr_value=2.0,
        equity=10000.0, available_balance=10000.0, leverage=10, filters=FILTERS,
        risk_pct=1.0, rr=1.5, atr_mult=0.3, atr_fallback_mult=1.5,
    )
    assert plan.structure_found is False
    assert plan.stop_price == pytest.approx(97.0)          # 100 - 1.5*2
    assert any("结构" in w for w in plan.warnings)


def test_compute_infeasible_min_notional():
    plan = compute_plan(
        direction=LONG, entry_price=100.0,
        structure=StructurePoint(95.0, 6, 0, 3), atr_value=2.0,
        equity=100.0, available_balance=100.0, leverage=10,
        filters=SymbolFilters(0.1, 0.001, 0.001, 20.0),
        risk_pct=1.0, rr=1.5, atr_mult=0.3,
    )
    # risk 1.0 / 5.6 ≈ 0.178 qty -> notional ≈ 17.8 < 20
    assert plan.feasible is False
    assert any("名义价值" in w for w in plan.warnings)


def test_compute_infeasible_min_qty():
    plan = compute_plan(
        direction=LONG, entry_price=100.0,
        structure=StructurePoint(95.0, 6, 0, 3), atr_value=2.0,
        equity=100.0, available_balance=100.0, leverage=10,
        filters=SymbolFilters(0.1, 0.001, 0.5, 1.0),
        risk_pct=1.0, rr=1.5, atr_mult=0.3,
    )
    assert plan.feasible is False
    assert any("下单量" in w for w in plan.warnings)


def test_compute_infeasible_margin():
    plan = compute_plan(
        direction=LONG, entry_price=100.0,
        structure=StructurePoint(95.0, 6, 0, 3), atr_value=2.0,
        equity=10000.0, available_balance=50.0, leverage=10, filters=FILTERS,
        risk_pct=1.0, rr=1.5, atr_mult=0.3,
    )
    # margin = 17.857*100/10 ≈ 178.6 > 50 available
    assert plan.feasible is False
    assert any("保证金" in w for w in plan.warnings)


def test_compute_plan_to_dict_shape():
    plan = compute_plan(
        direction=LONG, entry_price=100.0,
        structure=StructurePoint(95.0, 6, 0, 3), atr_value=2.0,
        equity=10000.0, available_balance=10000.0, leverage=10, filters=FILTERS,
    )
    d = plan.to_dict()
    for key in ("structure_found", "structure", "atr", "entry_price", "stop_price",
                "stop_distance", "take_profit_price", "rr", "risk_pct", "risk_amount",
                "equity", "quantity", "notional", "required_margin", "feasible", "warnings"):
        assert key in d
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_position_plan.py -k compute -v`
Expected: FAIL — `ImportError: cannot import name 'compute_plan'`

- [ ] **Step 3: Write minimal implementation** (append to `position_plan.py`)

```python
@dataclass
class PositionPlan:
    structure_found: bool
    structure: dict | None
    atr: float
    entry_price: float
    stop_price: float
    stop_distance: float
    take_profit_price: float
    rr: float
    risk_pct: float
    risk_amount: float
    equity: float
    quantity: float
    notional: float
    required_margin: float
    feasible: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_plan(
    *,
    direction: str,
    entry_price: float,
    structure: StructurePoint | None,
    atr_value: float,
    equity: float,
    available_balance: float,
    leverage: int,
    filters: SymbolFilters,
    risk_pct: float = 1.0,
    rr: float = 1.5,
    atr_mult: float = 0.3,
    atr_fallback_mult: float = 1.5,
) -> PositionPlan:
    """Pure: structure (or ATR fallback) -> stop -> R:R TP -> risk-defined qty,
    all rounded to exchange filters. Sets feasible=False (with warnings) rather
    than raising on min-qty / min-notional / margin violations."""
    if direction not in (LONG, SHORT):
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")

    warnings: list[str] = []
    structure_found = structure is not None
    struct_dict = structure.to_dict() if structure else None

    # ---- stop price ----
    if direction == LONG:
        base = structure.price if structure_found else entry_price
        raw_stop = (base - atr_mult * atr_value) if structure_found \
            else (entry_price - atr_fallback_mult * atr_value)
        stop_price = _round_step(raw_stop, filters.tick_size, "floor")
    else:  # SHORT
        base = structure.price if structure_found else entry_price
        raw_stop = (base + atr_mult * atr_value) if structure_found \
            else (entry_price + atr_fallback_mult * atr_value)
        stop_price = _round_step(raw_stop, filters.tick_size, "ceil")
    if not structure_found:
        warnings.append("未找到结构，已用 ATR 兜底止损")

    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        warnings.append("止损距离为 0，无法计算仓位")
        return PositionPlan(
            structure_found, struct_dict, atr_value, entry_price, stop_price,
            0.0, 0.0, rr, risk_pct, 0.0, equity, 0.0, 0.0, 0.0, False, warnings,
        )

    # ---- take profit (fixed R:R) ----
    if direction == LONG:
        raw_tp = entry_price + rr * stop_distance
    else:
        raw_tp = entry_price - rr * stop_distance
    take_profit_price = _round_step(raw_tp, filters.tick_size, "nearest")

    # ---- position size (risk-defined) ----
    risk_amount = equity * (risk_pct / 100.0)
    quantity = _round_step(risk_amount / stop_distance, filters.step_size, "floor")

    feasible = True
    if quantity <= 0 or quantity < filters.min_qty:
        feasible = False
        warnings.append(f"数量 {quantity} 低于最小下单量 {filters.min_qty}")
    notional = quantity * entry_price
    if notional < filters.min_notional:
        feasible = False
        warnings.append(f"名义价值 {notional:.2f} 低于最小 {filters.min_notional}")
    required_margin = notional / leverage if leverage else 0.0
    if required_margin > available_balance:
        feasible = False
        warnings.append(
            f"所需保证金 {required_margin:.2f} 超过可用余额 {available_balance:.2f}")

    return PositionPlan(
        structure_found=structure_found, structure=struct_dict, atr=atr_value,
        entry_price=entry_price, stop_price=stop_price, stop_distance=stop_distance,
        take_profit_price=take_profit_price, rr=rr, risk_pct=risk_pct,
        risk_amount=risk_amount, equity=equity, quantity=quantity, notional=notional,
        required_margin=required_margin, feasible=feasible, warnings=warnings,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_position_plan.py -v`
Expected: PASS (12 tests total)

- [ ] **Step 5: Commit**

```bash
git add backend/trading/position_plan.py backend/tests/test_position_plan.py
git commit -m "feat(position_plan): compute_plan — stop, R:R TP, risk-defined sizing"
```

---

## Task 5: `trading.service.build_position_plan` — network orchestrator

**Files:**
- Modify: `backend/trading/service.py` (add top-of-file imports + new function at end)
- Test: `backend/tests/test_position_plan.py`

- [ ] **Step 1: Write the failing test** (append to `test_position_plan.py`)

```python
from klines.models import Candle
from trading import service


def _candle(close, t=0, closed=True):
    return Candle(open_time=t, close_time=t + 1, open=close, high=close + 1,
                  low=close - 1, close=close, volume=1.0, closed=closed)


def test_build_position_plan_orchestrates(monkeypatch):
    monkeypatch.setattr(service, "_resolve", lambda cid: ("testnet", "key", "secret"))
    # last candle close is the MARKET entry price
    monkeypatch.setattr(service, "fetch_klines",
                        lambda *a, **k: [_candle(100.0, t=i) for i in range(30)])
    monkeypatch.setattr(service, "compute_atr", lambda candles, period: 2.0)
    monkeypatch.setattr(service, "find_pivot",
                        lambda *a, **k: StructurePoint(price=95.0, bar_index=6,
                                                       bar_time=0, age_bars=3))
    monkeypatch.setattr(service, "get_account", lambda cid: {
        "total_wallet_balance": 10000.0, "total_unrealized_pnl": 0.0,
        "available_balance": 10000.0,
    })
    monkeypatch.setattr(service.bn, "exchange_info", lambda env, key: {
        "symbols": [{"symbol": "BTCUSDT", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]}]})

    out = service.build_position_plan(
        credential_id=1, symbol="btcusdt", interval="4h", direction="long",
        order_type="MARKET",
    )
    assert out["symbol"] == "BTCUSDT"
    assert out["interval"] == "4h"
    assert out["direction"] == "long"
    assert out["entry_price"] == pytest.approx(100.0)
    assert out["stop_price"] == pytest.approx(94.4)
    assert out["feasible"] is True


def test_build_position_plan_limit_requires_entry_price(monkeypatch):
    monkeypatch.setattr(service, "_resolve", lambda cid: ("testnet", "key", "secret"))
    monkeypatch.setattr(service, "fetch_klines",
                        lambda *a, **k: [_candle(100.0, t=i) for i in range(30)])
    monkeypatch.setattr(service, "compute_atr", lambda candles, period: 2.0)
    with pytest.raises(ValueError):
        service.build_position_plan(
            credential_id=1, symbol="BTCUSDT", interval="4h", direction="long",
            order_type="LIMIT", entry_price=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_position_plan.py -k build_position_plan -v`
Expected: FAIL — `AttributeError: module 'trading.service' has no attribute 'fetch_klines'` (or `build_position_plan`)

- [ ] **Step 3: Add module-level imports to `service.py`**

Add after the existing imports (after line 22, the `from trading.models import (...)` block):

```python
from klines.fetcher import fetch_klines
from klines.structure import find_pivot, atr as compute_atr
from trading import position_plan as pp
```

- [ ] **Step 4: Append `build_position_plan` to the end of `service.py`**

```python
def build_position_plan(
    *,
    credential_id: int,
    symbol: str,
    interval: str,
    direction: str,
    order_type: str,
    entry_price: float | None = None,
    risk_pct: float = 1.0,
    rr: float = 1.5,
    atr_mult: float = 0.3,
    atr_period: int = 14,
    fractal_k: int = 2,
    lookback: int = 150,
    leverage: int = 10,
) -> dict[str, Any]:
    """Read-only: fetch klines + account + exchangeInfo, find the structural
    pivot, and compute an ATR-buffered stop, R:R take-profit and risk-defined
    position size. Places NO order."""
    symbol = symbol.upper()

    need = lookback + atr_period + 2 * fractal_k + 5
    candles = fetch_klines(symbol, interval, limit=max(need, 50))
    if not candles:
        raise ValueError(f"no klines for {symbol} {interval}")

    if order_type == "LIMIT":
        if not entry_price or entry_price <= 0:
            raise ValueError("LIMIT 单需提供有效的 entry_price")
        entry = float(entry_price)
    else:
        entry = candles[-1].close  # live/last price

    atr_value = compute_atr(candles, atr_period)
    if atr_value is None:
        raise ValueError("K线不足，无法计算 ATR")

    structure = find_pivot(candles, direction, entry, k=fractal_k, lookback=lookback)

    acct = get_account(credential_id)
    equity = acct["total_wallet_balance"] + acct["total_unrealized_pnl"]
    available = acct["available_balance"]

    env, api_key, _secret = _resolve(credential_id)
    filters = pp.parse_filters(bn.exchange_info(env, api_key), symbol)

    plan = pp.compute_plan(
        direction=direction, entry_price=entry, structure=structure,
        atr_value=atr_value, equity=equity, available_balance=available,
        leverage=leverage, filters=filters, risk_pct=risk_pct, rr=rr,
        atr_mult=atr_mult,
    )
    out = plan.to_dict()
    out.update({"symbol": symbol, "interval": interval, "direction": direction})
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_position_plan.py -v`
Expected: PASS (14 tests total)

- [ ] **Step 6: Commit**

```bash
git add backend/trading/service.py backend/tests/test_position_plan.py
git commit -m "feat(trading): build_position_plan orchestrator (read-only)"
```

---

## Task 6: `POST /trading/plan` endpoint

**Files:**
- Modify: `backend/api/trading.py` (import + Pydantic model + route)
- Test: `backend/tests/test_position_plan.py`

- [ ] **Step 1: Write the failing tests** (append to `test_position_plan.py`)

```python
from api import trading as trading_api


def test_plan_route_calls_service(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return {"ok": "canned", "symbol": kwargs["symbol"]}

    monkeypatch.setattr(trading_api, "build_position_plan", fake_build)
    body = trading_api.PlanBody(
        credential_id=1, symbol="BTCUSDT", interval="4h",
        direction="long", order_type="MARKET",
    )
    out = trading_api.plan(body)
    assert out["ok"] == "canned"
    assert captured["credential_id"] == 1
    assert captured["direction"] == "long"


def test_plan_body_rejects_bad_direction():
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        trading_api.PlanBody(
            credential_id=1, symbol="BTCUSDT", interval="4h",
            direction="sideways", order_type="MARKET",
        )


def test_plan_route_maps_valueerror_to_http_400(monkeypatch):
    from fastapi import HTTPException

    def boom(**kwargs):
        raise ValueError("LIMIT 单需提供有效的 entry_price")

    monkeypatch.setattr(trading_api, "build_position_plan", boom)
    body = trading_api.PlanBody(
        credential_id=1, symbol="BTCUSDT", interval="4h",
        direction="long", order_type="LIMIT",
    )
    with pytest.raises(HTTPException) as ei:
        trading_api.plan(body)
    assert ei.value.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_position_plan.py -k plan -v`
Expected: FAIL — `AttributeError: module 'api.trading' has no attribute 'PlanBody'`

- [ ] **Step 3: Add the import to `api/trading.py`**

Extend the existing `from trading.service import (...)` block (lines 15-19) to also import `build_position_plan`:

```python
from trading.service import (
    test_credential, get_account, place_order, list_recent_orders,
    place_order_bracket, close_position, list_open_orders,
    cancel_open_order, list_binance_order_history, build_position_plan,
)
```

- [ ] **Step 4: Add the model + route to `api/trading.py`** (append at end of file)

```python
# ---------- position plan (read-only: structure stop + risk sizing) ----------

class PlanBody(BaseModel):
    credential_id: int
    symbol: str = Field(min_length=1, max_length=20)
    interval: str = Field(min_length=1, max_length=8)
    direction: str = Field(pattern="^(long|short)$")
    order_type: str = Field(pattern="^(MARKET|LIMIT)$")
    entry_price: float | None = Field(default=None, gt=0)
    risk_pct: float = Field(default=1.0, gt=0, le=100)
    rr: float = Field(default=1.5, gt=0, le=100)
    atr_mult: float = Field(default=0.3, ge=0, le=50)
    atr_period: int = Field(default=14, ge=1, le=1000)
    fractal_k: int = Field(default=2, ge=1, le=50)
    lookback: int = Field(default=150, ge=10, le=1500)
    leverage: int = Field(default=10, ge=1, le=125)


@router.post("/plan")
def plan(body: PlanBody):
    try:
        return build_position_plan(
            credential_id=body.credential_id, symbol=body.symbol,
            interval=body.interval, direction=body.direction,
            order_type=body.order_type, entry_price=body.entry_price,
            risk_pct=body.risk_pct, rr=body.rr, atr_mult=body.atr_mult,
            atr_period=body.atr_period, fractal_k=body.fractal_k,
            lookback=body.lookback, leverage=body.leverage,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_position_plan.py -v`
Expected: PASS (17 tests total)

- [ ] **Step 6: Run the full backend suite (no regressions)**

Run: `cd backend && pytest -m "not network" -q`
Expected: PASS — all prior tests still green plus the new ones.

- [ ] **Step 7: Commit**

```bash
git add backend/api/trading.py backend/tests/test_position_plan.py
git commit -m "feat(api): POST /trading/plan — read-only structure stop + sizing"
```

---

## Task 7: Frontend API client method

**Files:**
- Modify: `frontend/src/api/client.js`

- [ ] **Step 1: Add the method** inside the `// ---- trading ----` section (e.g. right after `getBinanceOrderHistory`, before the closing `}` of `export const api`)

```javascript
  async buildTradingPlan(data) {
    return request('/trading/plan', { method: 'POST', body: JSON.stringify(data) })
  },
```

- [ ] **Step 2: Verify the build compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds (no syntax error).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.js
git commit -m "feat(client): buildTradingPlan API method"
```

---

## Task 8: TradeForm — 「📐 智能计算」button, params, and `applyPlan`

**Files:**
- Modify: `frontend/src/components/TradeForm.vue`

- [ ] **Step 1: Add the plan-params state and `computing` prop**

In `<script setup>`, extend props and add reactive plan params:

```javascript
const props = defineProps({
  symbol: { type: String, required: true },
  credentialId: { type: Number, default: null },
  submitting: { type: Boolean, default: false },
  computing: { type: Boolean, default: false },   // NEW: plan request in flight
})

const emit = defineEmits(['open-confirm', 'compute-plan'])   // add 'compute-plan'

const planParams = ref({ risk_pct: 1.0, rr: 1.5, atr_mult: 0.3 })
```

- [ ] **Step 2: Add `buildPlanRequest` and `applyPlan`, and expose `applyPlan`**

In `<script setup>` (after `buildPayload`):

```javascript
function buildPlanRequest() {
  const f = form.value
  return {
    direction: f.side === 'BUY' ? 'long' : 'short',
    order_type: f.order_type,
    entry_price: f.order_type === 'LIMIT' ? f.price : null,
    leverage: f.leverage,
    risk_pct: planParams.value.risk_pct,
    rr: planParams.value.rr,
    atr_mult: planParams.value.atr_mult,
  }
}

// Called by the parent (Trade.vue) after /trading/plan returns, to fill the form.
function applyPlan(plan) {
  if (plan.quantity && plan.quantity > 0) form.value.quantity = plan.quantity
  if (plan.stop_price && plan.stop_price > 0) {
    useSL.value = true
    form.value.stop_loss_price = plan.stop_price
  }
  if (plan.take_profit_price && plan.take_profit_price > 0) {
    useTP.value = true
    form.value.take_profit_price = plan.take_profit_price
  }
}

defineExpose({ applyPlan })
```

- [ ] **Step 3: Add the params inputs + button to the template**

Insert this block in `<template>` just **above** the `<!-- SL / TP -->` comment:

```html
    <!-- smart plan: structure stop + risk-defined sizing -->
    <div class="plan-row">
      <div class="plan-inputs">
        <label>风险% <input v-model.number="planParams.risk_pct" type="number" step="0.1" min="0.1" /></label>
        <label>盈亏比 <input v-model.number="planParams.rr" type="number" step="0.1" min="0.1" /></label>
        <label>ATR× <input v-model.number="planParams.atr_mult" type="number" step="0.05" min="0" /></label>
      </div>
      <button
        class="btn btn-secondary plan-btn"
        :disabled="!credentialId || !symbol || computing"
        @click="emit('compute-plan', buildPlanRequest())"
      >
        {{ computing ? '计算中…' : '📐 智能计算（结构止损+仓位）' }}
      </button>
    </div>
```

- [ ] **Step 4: Add scoped styles** (append inside `<style scoped>`)

```css
.plan-row {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 14px;
  padding: 12px;
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--bg-primary);
}
.plan-inputs {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.plan-inputs label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}
.plan-inputs input {
  width: 72px;
  padding: 5px 8px;
  font-size: 13px;
}
.plan-btn { width: 100%; font-weight: 600; }
```

- [ ] **Step 5: Verify the build compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/TradeForm.vue
git commit -m "feat(TradeForm): 智能计算 button + risk/rr/atr params + applyPlan"
```

---

## Task 9: Trade.vue — wire the plan call, summary, and chart structure line

**Files:**
- Modify: `frontend/src/views/Trade.vue`

> **Read `frontend/src/views/Trade.vue` in full first.** It is large; integrate using the existing names found there. The names below (`selectedCredentialId`, `symbol`, `interval`, `chart`/series ref, `redrawPriceLines`) are the expected reuse points — confirm the exact identifiers when you read the file and adapt the snippets accordingly. Note where `<TradeForm ... />` is rendered and how the chart object + current price-lines array are stored.

- [ ] **Step 1: Add a ref to the TradeForm and plan state**

In `<script setup>`:

```javascript
import { ref } from 'vue'   // (already imported — ensure ref is available)

const tradeFormRef = ref(null)
const planResult = ref(null)
const planComputing = ref(false)
const planError = ref('')
```

- [ ] **Step 2: Add the compute handler**

```javascript
async function onComputePlan(req) {
  if (!selectedCredentialId.value || !symbol.value) return
  planComputing.value = true
  planError.value = ''
  try {
    const plan = await api.buildTradingPlan({
      credential_id: selectedCredentialId.value,
      symbol: symbol.value,
      interval: interval.value,
      ...req,
    })
    planResult.value = plan
    tradeFormRef.value?.applyPlan(plan)   // fill quantity / SL / TP
    redrawPriceLines()                    // re-draw incl. the new structure line (Step 4)
  } catch (e) {
    planError.value = e.message || String(e)
  } finally {
    planComputing.value = false
  }
}
```

- [ ] **Step 3: Bind the ref + handler on the `<TradeForm>` element**

Add `ref="tradeFormRef"`, `:computing="planComputing"`, and `@compute-plan="onComputePlan"` to the existing `<TradeForm ... />` tag:

```html
<TradeForm
  ref="tradeFormRef"
  :symbol="symbol"
  :credential-id="selectedCredentialId"
  :submitting="submitting"
  :computing="planComputing"
  @open-confirm="onOpenConfirm"
  @compute-plan="onComputePlan"
/>
```

(Keep whatever existing props/handlers Trade.vue already passes; only add the three new attributes.)

- [ ] **Step 4: Draw the structure pivot as a chart price line**

Inside the existing `redrawPriceLines()` function, after the position/SL/TP/limit lines are drawn, add a structure line when a plan exists:

```javascript
  if (planResult.value && planResult.value.structure_found && planResult.value.structure) {
    priceLineRefs.push(candleSeries.createPriceLine({
      price: planResult.value.structure.price,
      color: '#cca44d',            // --warning terracotta-ish; matches design tokens
      lineWidth: 1,
      lineStyle: 2,                // dashed
      axisLabelVisible: true,
      title: `结构(${planResult.value.structure.age_bars}根前)`,
    }))
  }
```

(Use whatever the file calls its series variable and its price-line accumulator array — confirm when reading the file.)

- [ ] **Step 5: Add a compact plan summary panel**

Place near the TradeForm in `<template>`:

```html
<div v-if="planResult" class="plan-summary">
  <span v-if="!planResult.structure_found" class="plan-warn">未找到结构，已用 ATR 兜底</span>
  <div class="plan-stat"><label>结构点</label><b>{{ planResult.structure ? planResult.structure.price : '—' }}</b></div>
  <div class="plan-stat"><label>止损</label><b>{{ planResult.stop_price }}</b></div>
  <div class="plan-stat"><label>止盈</label><b>{{ planResult.take_profit_price }}</b></div>
  <div class="plan-stat"><label>数量</label><b>{{ planResult.quantity }}</b></div>
  <div class="plan-stat"><label>风险额</label><b>{{ planResult.risk_amount.toFixed(2) }}</b></div>
  <div class="plan-stat"><label>所需保证金</label><b>{{ planResult.required_margin.toFixed(2) }}</b></div>
  <div v-for="(w, i) in planResult.warnings" :key="i" class="plan-warn">⚠ {{ w }}</div>
  <div v-if="!planResult.feasible" class="plan-warn">该方案不可行，请调整参数后再下单</div>
</div>
<div v-if="planError" class="plan-warn">计算失败：{{ planError }}</div>
```

Add scoped styles:

```css
.plan-summary {
  display: flex; flex-wrap: wrap; gap: 12px 18px;
  padding: 12px; margin-top: 12px;
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--bg-secondary);
}
.plan-stat { display: flex; flex-direction: column; gap: 2px; }
.plan-stat label { font-size: 11px; color: var(--text-tertiary); }
.plan-stat b { font-size: 14px; color: var(--text-primary); }
.plan-warn { width: 100%; font-size: 12px; color: var(--warning); }
```

- [ ] **Step 6: Verify the build compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/Trade.vue
git commit -m "feat(Trade): wire 智能计算 — fill form, structure chart line, summary"
```

---

## Task 10: Manual end-to-end verification (testnet) + finish

**Files:** none (verification only)

- [ ] **Step 1: Backend smoke** — full suite green

Run: `cd backend && pytest -m "not network" -q`
Expected: all PASS.

- [ ] **Step 2: Live read-only check via proxy** (local dev; uses real testnet credential id)

Run (PowerShell, with the backend running on :8080 and an authenticated session cookie, or call the service directly in a Python REPL):
```python
from trading.service import build_position_plan
print(build_position_plan(credential_id=<TESTNET_ID>, symbol="BTCUSDT",
      interval="4h", direction="long", order_type="MARKET"))
```
Expected: a dict with a plausible `stop_price` below the last price, `take_profit_price` above it (≈1.5×), a positive `quantity`, `structure` populated (or `structure_found=false` with an ATR-fallback warning). Confirms NO order was placed (check 交易终端 委托单/持仓 unchanged).

- [ ] **Step 3: Frontend manual check** — `npm run dev`, open 交易终端, select a testnet credential + BTCUSDT + 4h, click 「📐 智能计算」. Confirm: quantity/SL/TP fields auto-fill, SL/TP checkboxes tick, chart shows a dashed structure line, summary panel shows 风险额/保证金, and submitting still goes through the **existing** bracket confirm modal (logic unchanged).

- [ ] **Step 4: Update CLAUDE.md** — add a one-line note under the trading section that `/trading/plan` is a read-only structure-stop + risk-sizing helper (so future sessions know it exists). Keep it brief.

- [ ] **Step 5: Finish the branch** — invoke `superpowers:finishing-a-development-branch` to choose merge-to-main / PR / cleanup. (User deploys from `main` via `git pull && docker compose up -d --build`.)

---

## Self-Review

**Spec coverage:**
- Fractal pivot (long/short, ref filter, confirmation, not-found) → Task 1 ✓
- ATR (Wilder, closed-only, insufficient→None) → Task 2 ✓
- Filter parsing + tick/step rounding → Task 3 ✓
- Stop (ATR buffer, safe-side rounding), R:R TP, risk-%-of-equity sizing, feasibility (minQty/minNotional/margin), ATR fallback → Task 4 ✓
- Entry price (LIMIT=typed, MARKET=last close), equity=wallet+uPnL, network orchestration → Task 5 ✓
- Read-only `POST /trading/plan` + validation + error mapping → Task 6 ✓
- Frontend client method → Task 7 ✓; 智能计算 button + params + applyPlan → Task 8 ✓; Trade.vue wiring + chart structure line + summary → Task 9 ✓
- Security (no secret echoed; read-only; existing bracket flow unchanged) → endpoint returns only plan dict; no secret touched ✓
- Manual testnet verification + branch finish → Task 10 ✓

**Placeholder scan:** Backend steps contain complete code. Frontend Task 9 intentionally defers exact identifier names to a mandated full read of the large `Trade.vue`, with concrete snippets to adapt — this is reuse-into-existing-file, not a placeholder.

**Type consistency:** `StructurePoint(price, bar_index, bar_time, age_bars)` used consistently across Tasks 1/4/5. `SymbolFilters(tick_size, step_size, min_qty, min_notional)` consistent across Tasks 3/4/5. `compute_plan(...)` keyword signature matches its call in Task 5. `build_position_plan(...)` signature matches the route call in Task 6 and the test in Task 5. `PlanBody` field names match `build_position_plan` kwargs. `applyPlan(plan)` reads `plan.quantity/stop_price/take_profit_price` — names match the `PositionPlan.to_dict()` keys from Task 4.
