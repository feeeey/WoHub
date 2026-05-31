import pytest
from trading.position_plan import (
    SymbolFilters, parse_filters, _round_step,
    compute_plan, PositionPlan,
)
from klines.structure import StructurePoint, LONG, SHORT
from klines.models import Candle
from trading import service


def _fake_info(symbol="BTCUSDT", notional_type="MIN_NOTIONAL"):
    return {"symbols": [{
        "symbol": symbol,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": notional_type, "notional": "5"},
        ],
    }]}


FILTERS = SymbolFilters(tick_size=0.1, step_size=0.001, min_qty=0.001, min_notional=5.0)


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


def test_compute_plan_invalid_direction_raises():
    with pytest.raises(ValueError, match="direction"):
        compute_plan(direction="sideways", entry_price=100.0, structure=None,
                     atr_value=2.0, equity=1000.0, available_balance=1000.0,
                     leverage=10, filters=FILTERS)


def test_compute_plan_zero_leverage_raises():
    with pytest.raises(ValueError, match="leverage"):
        compute_plan(direction=LONG, entry_price=100.0,
                     structure=StructurePoint(95.0, 6, 0, 3), atr_value=2.0,
                     equity=1000.0, available_balance=1000.0, leverage=0, filters=FILTERS)


def test_compute_plan_stop_wrong_side_is_infeasible():
    # structure ABOVE entry for a long -> stop ends up >= entry -> infeasible
    plan = compute_plan(direction=LONG, entry_price=100.0,
                        structure=StructurePoint(102.0, 6, 0, 3), atr_value=2.0,
                        equity=10000.0, available_balance=10000.0, leverage=10,
                        filters=FILTERS, atr_mult=0.3)
    assert plan.feasible is False
    assert any("错误一侧" in w for w in plan.warnings)


# ---- Task 5: build_position_plan orchestrator ----


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
    monkeypatch.setattr(service.bn, "account_info",
                        lambda env, key, secret: {
                            "totalWalletBalance": 10000.0,
                            "totalUnrealizedProfit": 0.0,
                            "availableBalance": 10000.0,
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
    monkeypatch.setattr(service.bn, "account_info",
                        lambda env, key, secret: {
                            "totalWalletBalance": 0.0,
                            "totalUnrealizedProfit": 0.0,
                            "availableBalance": 0.0,
                        })
    with pytest.raises(ValueError):
        service.build_position_plan(
            credential_id=1, symbol="BTCUSDT", interval="4h", direction="long",
            order_type="LIMIT", entry_price=None)


# ---- Task 6: POST /trading/plan endpoint ----

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
