"""Pure position-planning math: structure-based stop, R:R take-profit, and
risk-defined position sizing, with Binance symbol-filter rounding.

No network and no credentials — all inputs are passed in. The network
orchestration lives in trading.service.build_position_plan.
"""
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
from typing import Any

from klines.structure import StructurePoint, LONG, SHORT


@dataclass(frozen=True)
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

    if leverage <= 0:
        raise ValueError(f"leverage must be >= 1, got {leverage!r}")

    warnings: list[str] = []
    structure_found = structure is not None
    struct_dict = structure.to_dict() if structure else None

    # ---- stop price ----
    if direction == LONG:
        if structure_found:
            raw_stop = structure.price - atr_mult * atr_value
        else:
            raw_stop = entry_price - atr_fallback_mult * atr_value
        stop_price = _round_step(raw_stop, filters.tick_size, "floor")
    else:  # SHORT
        if structure_found:
            raw_stop = structure.price + atr_mult * atr_value
        else:
            raw_stop = entry_price + atr_fallback_mult * atr_value
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

    if (direction == LONG and stop_price >= entry_price) or \
       (direction == SHORT and stop_price <= entry_price):
        warnings.append("止损价位于入场价错误一侧，计划无效")
        return PositionPlan(
            structure_found, struct_dict, atr_value, entry_price, stop_price,
            stop_distance, 0.0, rr, risk_pct, 0.0, equity, 0.0, 0.0, 0.0, False, warnings,
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
    notional = quantity * entry_price
    if quantity <= 0 or quantity < filters.min_qty:
        feasible = False
        warnings.append(f"数量 {quantity} 低于最小下单量 {filters.min_qty}")
    else:
        if notional < filters.min_notional:
            feasible = False
            warnings.append(f"名义价值 {notional:.2f} 低于最小 {filters.min_notional}")
    required_margin = notional / leverage
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
