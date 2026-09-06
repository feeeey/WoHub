import pydantic
import pytest

from api import trading as trading_api
from trading import service
from trading.models import BracketOrderResult, OrderResult
from trading.position_plan import (
    SymbolFilters,
    compute_fixed_risk_order,
)


FILTERS = SymbolFilters(
    tick_size=0.1,
    step_size=0.001,
    min_qty=0.001,
    min_notional=5.0,
)


def test_fixed_risk_matches_user_example():
    plan = compute_fixed_risk_order(
        direction="long",
        entry_price=70_000,
        equity=500,
        available_balance=500,
        leverage=10,
        filters=FILTERS,
        risk_pct=1,
        reward_risk_ratio=1.5,
        stop_price=69_800,
    )

    assert plan.risk_amount == pytest.approx(5)
    assert plan.quantity == pytest.approx(0.025)
    assert plan.stop_price == pytest.approx(69_800)
    assert plan.take_profit_price == pytest.approx(70_300)
    assert plan.estimated_loss == pytest.approx(5)
    assert plan.estimated_profit == pytest.approx(7.5)


def test_quantity_is_independent_of_leverage():
    kwargs = dict(
        direction="long",
        entry_price=70_000,
        equity=500,
        available_balance=10_000,
        filters=FILTERS,
        risk_pct=1,
        reward_risk_ratio=1.5,
        stop_price=69_800,
    )
    at_1x = compute_fixed_risk_order(leverage=1, **kwargs)
    at_100x = compute_fixed_risk_order(leverage=100, **kwargs)

    assert at_1x.quantity == at_100x.quantity == pytest.approx(0.025)
    assert at_1x.required_margin == pytest.approx(
        at_100x.required_margin * 100)


def test_missing_stop_is_derived_from_quantity():
    plan = compute_fixed_risk_order(
        direction="long",
        entry_price=70_000,
        equity=500,
        available_balance=500,
        leverage=10,
        filters=FILTERS,
        quantity=0.025,
    )

    assert plan.quantity_source == "user"
    assert plan.stop_source == "risk_from_quantity"
    assert plan.stop_price == pytest.approx(69_800)
    assert plan.take_profit_price == pytest.approx(70_300)
    assert plan.estimated_loss <= plan.risk_amount


def test_missing_stop_short_is_symmetric():
    plan = compute_fixed_risk_order(
        direction="short",
        entry_price=70_000,
        equity=500,
        available_balance=500,
        leverage=10,
        filters=FILTERS,
        quantity=0.025,
    )

    assert plan.stop_price == pytest.approx(70_200)
    assert plan.take_profit_price == pytest.approx(69_700)


def test_risk_above_two_percent_is_rejected():
    with pytest.raises(ValueError, match="2.0%"):
        compute_fixed_risk_order(
            direction="long",
            entry_price=70_000,
            equity=500,
            available_balance=500,
            leverage=10,
            filters=FILTERS,
            risk_pct=2.01,
            stop_price=69_800,
        )


def test_reward_risk_below_one_point_five_is_rejected():
    with pytest.raises(ValueError, match="1.5"):
        compute_fixed_risk_order(
            direction="long",
            entry_price=70_000,
            equity=500,
            available_balance=500,
            leverage=10,
            filters=FILTERS,
            reward_risk_ratio=1.49,
            stop_price=69_800,
        )


def test_user_take_profit_must_meet_configured_ratio():
    with pytest.raises(ValueError, match="低于设定值"):
        compute_fixed_risk_order(
            direction="long",
            entry_price=70_000,
            equity=500,
            available_balance=500,
            leverage=10,
            filters=FILTERS,
            reward_risk_ratio=1.5,
            stop_price=69_800,
            take_profit_price=70_200,
        )


def _exchange_info():
    return {
        "symbols": [{
            "symbol": "BTCUSDT",
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                {
                    "filterType": "LOT_SIZE",
                    "stepSize": "0.001",
                    "minQty": "0.001",
                },
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }],
    }


def _account(position_amt="0", position_side="BOTH"):
    return {
        "totalWalletBalance": "500",
        "totalUnrealizedProfit": "0",
        "totalMarginBalance": "500",
        "availableBalance": "500",
        "positions": [{
            "symbol": "BTCUSDT",
            "positionAmt": position_amt,
            "positionSide": position_side,
        }],
    }


def _stub_risk_preview(monkeypatch, account=None):
    monkeypatch.setattr(
        service, "get_loss_guard_status",
        lambda *args, **kwargs: {
            "allowed": True,
            "lock_scope": "none",
            "today": {"current_loss_streak": 0, "max_loss_streak": 0},
        },
    )
    monkeypatch.setattr(
        service, "_resolve", lambda cid: ("testnet", "key", "secret"))
    monkeypatch.setattr(
        service.bn, "account_info",
        lambda *args, **kwargs: account or _account(),
    )
    monkeypatch.setattr(
        service.bn, "open_orders", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        service.bn, "mark_price",
        lambda *args, **kwargs: {"markPrice": "70000"},
    )
    monkeypatch.setattr(
        service.bn, "exchange_info",
        lambda *args, **kwargs: _exchange_info(),
    )


def test_service_preview_uses_total_margin_balance(monkeypatch):
    _stub_risk_preview(monkeypatch)
    plan = service.prepare_fixed_risk_order(
        credential_id=1,
        symbol="btcusdt",
        side="BUY",
        order_type="MARKET",
        quantity=None,
        leverage=10,
        stop_loss_price=69_800,
    )

    assert plan["equity"] == pytest.approx(500)
    assert plan["funds_source"] == "totalMarginBalance"
    assert plan["quantity"] == pytest.approx(0.025)
    assert plan["fee_slippage_excluded"] is True


def test_service_preview_rejects_same_symbol_position(monkeypatch):
    _stub_risk_preview(monkeypatch, account=_account(position_amt="0.01"))
    with pytest.raises(ValueError, match="禁止同币种加仓"):
        service.prepare_fixed_risk_order(
            credential_id=1,
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.025,
            leverage=10,
        )


def test_service_preview_rejects_hedge_mode(monkeypatch):
    _stub_risk_preview(monkeypatch, account=_account(position_side="LONG"))
    with pytest.raises(ValueError, match="单向持仓模式"):
        service.prepare_fixed_risk_order(
            credential_id=1,
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.025,
            leverage=10,
        )


def test_api_body_enforces_hard_risk_and_rr_limits():
    common = dict(
        credential_id=1,
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        quantity=0.025,
    )
    with pytest.raises(pydantic.ValidationError):
        trading_api.BracketOrderBody(**common, risk_pct=2.01)
    with pytest.raises(pydantic.ValidationError):
        trading_api.BracketOrderBody(**common, reward_risk_ratio=1.49)


def test_bracket_route_uses_authoritative_risk_plan(monkeypatch):
    plan = {
        "quantity": 0.025,
        "stop_price": 69_800,
        "take_profit_price": 70_300,
    }
    captured = {}

    def fake_prepare(body, **kwargs):
        captured["force_guard_refresh"] = kwargs.get(
            "force_guard_refresh", False)
        return plan

    monkeypatch.setattr(trading_api, "_prepare_risk_plan", fake_prepare)

    def fake_place(credential_id, req, stop_loss_price, take_profit_price):
        captured.update({
            "credential_id": credential_id,
            "quantity": req.quantity,
            "stop": stop_loss_price,
            "take_profit": take_profit_price,
        })
        return BracketOrderResult(
            ok=True,
            entry=OrderResult(ok=True, status="FILLED"),
        )

    monkeypatch.setattr(trading_api, "place_order_bracket", fake_place)
    body = trading_api.BracketOrderBody(
        credential_id=1,
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        quantity=99,
    )
    out = trading_api.place_bracket(body)

    assert captured == {
        "force_guard_refresh": True,
        "credential_id": 1,
        "quantity": 0.025,
        "stop": 69_800,
        "take_profit": 70_300,
    }
    assert out["risk_plan"] == plan
