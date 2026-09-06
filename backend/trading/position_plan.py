"""Pure position-planning math: structure-based stop, R:R take-profit, and
risk-defined position sizing, with Binance symbol-filter rounding.

No network and no credentials — all inputs are passed in. The network
orchestration lives in trading.service.build_position_plan.
"""
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP
from typing import Any

from klines.structure import StructurePoint, LONG, SHORT


MAX_RISK_PCT = 2.0
MIN_REWARD_RISK_RATIO = 1.5


@dataclass(frozen=True)
class SymbolFilters:
    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float


def parse_filters(
    exchange_info: dict,
    symbol: str,
    order_type: str = "LIMIT",
) -> SymbolFilters:
    """Pull executable price/quantity filters from fapi exchangeInfo.

    MARKET orders use MARKET_LOT_SIZE when Binance supplies it; otherwise the
    regular LOT_SIZE filter is the fallback.
    """
    symbol = symbol.upper()
    for s in exchange_info.get("symbols", []):
        if s.get("symbol") == symbol:
            tick = min_notional = 0.0
            lot_step = lot_min_qty = 0.0
            market_step = market_min_qty = 0.0
            for f in s.get("filters", []):
                t = f.get("filterType")
                if t == "PRICE_FILTER":
                    tick = float(f["tickSize"])
                elif t == "LOT_SIZE":
                    lot_step = float(f["stepSize"])
                    lot_min_qty = float(f["minQty"])
                elif t == "MARKET_LOT_SIZE":
                    market_step = float(f["stepSize"])
                    market_min_qty = float(f["minQty"])
                elif t in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_notional = float(f.get("notional", f.get("minNotional", 0)))
            use_market = order_type == "MARKET" and market_step > 0
            step = market_step if use_market else lot_step
            min_qty = market_min_qty if use_market else lot_min_qty
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


@dataclass
class FixedRiskOrderPlan:
    """A strict money-risk plan for one opening order.

    Quantity is derived from the stop distance when the user supplies a stop.
    Otherwise the supplied quantity is kept (after exchange-step rounding) and
    the stop distance is derived from the fixed money risk. Leverage never
    participates in the quantity formula; it is used only for the margin
    feasibility check.
    """

    equity: float
    available_balance: float
    risk_pct: float
    risk_amount: float
    entry_price: float
    quantity: float
    quantity_source: str
    stop_price: float
    stop_source: str
    take_profit_price: float
    take_profit_source: str
    reward_risk_ratio: float
    actual_reward_risk_ratio: float
    estimated_loss: float
    estimated_profit: float
    notional: float
    leverage: int
    required_margin: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_fixed_risk_order(
    *,
    direction: str,
    entry_price: float,
    equity: float,
    available_balance: float,
    leverage: int,
    filters: SymbolFilters,
    risk_pct: float = 1.0,
    reward_risk_ratio: float = MIN_REWARD_RISK_RATIO,
    quantity: float | None = None,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
) -> FixedRiskOrderPlan:
    """Build a strict fixed-money-risk order plan.

    Rules:
    * 0 < risk_pct <= 2
    * reward/risk >= 1.5
    * supplied stop -> quantity = risk money / stop distance
    * missing stop -> stop distance = risk money / supplied quantity
    * missing take-profit -> exact requested reward/risk target
    * exchange rounding must never increase the intended loss above the target
    """
    if direction not in (LONG, SHORT):
        raise ValueError("direction must be 'long' or 'short'")
    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")
    if equity <= 0:
        raise ValueError("合约总资金必须大于 0")
    if not (0 < risk_pct <= MAX_RISK_PCT):
        raise ValueError(f"单笔风险必须在 0% 到 {MAX_RISK_PCT}% 之间")
    if reward_risk_ratio < MIN_REWARD_RISK_RATIO:
        raise ValueError(f"盈亏比不得低于 {MIN_REWARD_RISK_RATIO}")
    if leverage < 1:
        raise ValueError("leverage must be >= 1")

    risk_amount = equity * risk_pct / 100.0

    if stop_price is not None:
        # Preserve a user-selected structural level by rounding it away from
        # entry, then size from that final executable stop.
        stop_mode = "floor" if direction == LONG else "ceil"
        final_stop = _round_step(float(stop_price), filters.tick_size, stop_mode)
        if final_stop <= 0:
            raise ValueError("止损价必须大于 0")
        if (direction == LONG and final_stop >= entry_price) or (
            direction == SHORT and final_stop <= entry_price
        ):
            raise ValueError("止损价位于入场价错误一侧")
        stop_distance = abs(entry_price - final_stop)
        final_quantity = _round_step(
            risk_amount / stop_distance, filters.step_size, "floor")
        quantity_source = "risk_from_stop"
        stop_source = "user"
    else:
        if quantity is None or quantity <= 0:
            raise ValueError("未设置止损价时必须提供开仓数量，以便反算止损位置")
        final_quantity = _round_step(float(quantity), filters.step_size, "floor")
        if final_quantity <= 0:
            raise ValueError("按交易所步进取整后的开仓数量为 0")
        raw_distance = risk_amount / final_quantity
        raw_stop = (
            entry_price - raw_distance if direction == LONG
            else entry_price + raw_distance
        )
        # Round towards entry so tick rounding cannot push the monetary loss
        # above the configured cap.
        stop_mode = "ceil" if direction == LONG else "floor"
        final_stop = _round_step(raw_stop, filters.tick_size, stop_mode)
        if final_stop <= 0:
            raise ValueError("按当前数量反算出的止损价不合法；请提高开仓数量")
        if (direction == LONG and final_stop >= entry_price) or (
            direction == SHORT and final_stop <= entry_price
        ):
            raise ValueError("风险金额小于最小价格步进对应的亏损，无法设置止损")
        stop_distance = abs(entry_price - final_stop)
        quantity_source = "user"
        stop_source = "risk_from_quantity"

    if final_quantity < filters.min_qty:
        raise ValueError(
            f"按风险计算的数量 {final_quantity} 低于最小下单量 {filters.min_qty}")

    notional = final_quantity * entry_price
    if notional < filters.min_notional:
        raise ValueError(
            f"按风险计算的名义价值 {notional:.2f} 低于最小 {filters.min_notional}")

    required_margin = notional / leverage
    if required_margin > available_balance:
        raise ValueError(
            f"所需保证金 {required_margin:.2f} 超过可用余额 {available_balance:.2f}")

    if take_profit_price is None:
        raw_tp = (
            entry_price + reward_risk_ratio * stop_distance
            if direction == LONG
            else entry_price - reward_risk_ratio * stop_distance
        )
        # Round away from entry so the executable target preserves at least
        # the requested reward/risk ratio.
        tp_mode = "ceil" if direction == LONG else "floor"
        final_tp = _round_step(raw_tp, filters.tick_size, tp_mode)
        tp_source = "risk_ratio"
    else:
        tp_mode = "ceil" if direction == LONG else "floor"
        final_tp = _round_step(float(take_profit_price), filters.tick_size, tp_mode)
        tp_source = "user"

    if final_tp <= 0:
        raise ValueError("止盈价必须大于 0")
    if (direction == LONG and final_tp <= entry_price) or (
        direction == SHORT and final_tp >= entry_price
    ):
        raise ValueError("止盈价位于入场价错误一侧")

    actual_rr = abs(final_tp - entry_price) / stop_distance
    if actual_rr + 1e-12 < reward_risk_ratio:
        raise ValueError(
            f"止盈价对应盈亏比 {actual_rr:.3f}，低于设定值 {reward_risk_ratio}")

    estimated_loss = final_quantity * stop_distance
    estimated_profit = final_quantity * abs(final_tp - entry_price)

    return FixedRiskOrderPlan(
        equity=equity,
        available_balance=available_balance,
        risk_pct=risk_pct,
        risk_amount=risk_amount,
        entry_price=entry_price,
        quantity=final_quantity,
        quantity_source=quantity_source,
        stop_price=final_stop,
        stop_source=stop_source,
        take_profit_price=final_tp,
        take_profit_source=tp_source,
        reward_risk_ratio=reward_risk_ratio,
        actual_reward_risk_ratio=actual_rr,
        estimated_loss=estimated_loss,
        estimated_profit=estimated_profit,
        notional=notional,
        leverage=leverage,
        required_margin=required_margin,
    )


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
