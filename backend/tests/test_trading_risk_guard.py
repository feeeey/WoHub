from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from api import trading as trading_api
from trading import binance_client as bn
from trading import service
from trading.risk_guard import (
    build_closed_order_outcomes,
    evaluate_loss_guard,
)


TZ = ZoneInfo("Asia/Shanghai")


def _ms(local_iso: str) -> int:
    dt = datetime.fromisoformat(local_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return int(dt.astimezone(timezone.utc).timestamp() * 1000)


def _outcome(local_iso: str, pnl: float, order_id: str) -> dict:
    return {
        "symbol": "BTCUSDT",
        "order_id": order_id,
        "realized_pnl": pnl,
        "time": _ms(local_iso),
        "fill_count": 1,
    }


def _now(local_iso: str) -> datetime:
    return datetime.fromisoformat(local_iso).replace(
        tzinfo=TZ).astimezone(timezone.utc)


def test_multi_fill_closing_order_counts_as_one_outcome():
    incomes = [
        {
            "incomeType": "REALIZED_PNL",
            "symbol": "BTCUSDT",
            "tradeId": "11",
        },
        {
            "incomeType": "REALIZED_PNL",
            "symbol": "BTCUSDT",
            "tradeId": "12",
        },
        {
            "incomeType": "REALIZED_PNL",
            "symbol": "BTCUSDT",
            "tradeId": "13",
        },
    ]
    trades = [
        {
            "symbol": "BTCUSDT", "id": 11, "orderId": 100,
            "realizedPnl": "-2", "time": _ms("2026-07-30T09:00:00"),
        },
        {
            "symbol": "BTCUSDT", "id": 12, "orderId": 100,
            "realizedPnl": "-3", "time": _ms("2026-07-30T09:00:01"),
        },
        {
            "symbol": "BTCUSDT", "id": 13, "orderId": 101,
            "realizedPnl": "4", "time": _ms("2026-07-30T10:00:00"),
        },
        {
            # Opening fill: no REALIZED_PNL income mapping, so it is ignored.
            "symbol": "BTCUSDT", "id": 99, "orderId": 102,
            "realizedPnl": "0", "time": _ms("2026-07-30T11:00:00"),
        },
    ]

    outcomes = build_closed_order_outcomes(incomes, trades)
    assert len(outcomes) == 2
    assert outcomes[0]["order_id"] == "100"
    assert outcomes[0]["realized_pnl"] == pytest.approx(-5)
    assert outcomes[0]["fill_count"] == 2


def test_missing_trade_mapping_fails_closed():
    incomes = [{
        "incomeType": "REALIZED_PNL",
        "symbol": "BTCUSDT",
        "tradeId": "11",
    }]
    with pytest.raises(ValueError, match="历史不完整"):
        build_closed_order_outcomes(incomes, [])


def test_income_history_sends_complete_signed_query(monkeypatch):
    captured = {}

    def fake_request(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(bn, "_request", fake_request)
    bn.income_history(
        "testnet", "key", "secret",
        income_type="REALIZED_PNL",
        start_time=100,
        end_time=200,
        page=3,
        limit=500,
    )

    assert captured["args"] == (
        "GET", "testnet", "/fapi/v1/income", "key", "secret",
        {
            "page": 3,
            "limit": 500,
            "incomeType": "REALIZED_PNL",
            "startTime": 100,
            "endTime": 200,
        },
    )
    assert captured["kwargs"] == {"signed": True}


def test_user_trades_sends_complete_signed_query(monkeypatch):
    captured = {}

    def fake_request(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(bn, "_request", fake_request)
    bn.user_trades(
        "mainnet", "key", "secret", "btcusdt",
        start_time=100,
        end_time=200,
        limit=500,
    )

    assert captured["args"] == (
        "GET", "mainnet", "/fapi/v1/userTrades", "key", "secret",
        {
            "symbol": "BTCUSDT",
            "limit": 500,
            "startTime": 100,
            "endTime": 200,
        },
    )
    assert captured["kwargs"] == {"signed": True}


def test_three_consecutive_losses_lock_until_next_midnight():
    outcomes = [
        _outcome("2026-07-30T09:00:00", -1, "1"),
        _outcome("2026-07-30T10:00:00", -1, "2"),
        _outcome("2026-07-30T11:00:00", -1, "3"),
    ]
    status = evaluate_loss_guard(
        outcomes, now=_now("2026-07-30T12:00:00"))

    assert status["allowed"] is False
    assert status["lock_scope"] == "day"
    assert status["today"]["current_loss_streak"] == 3
    assert status["today"]["max_loss_streak"] == 3
    assert status["lock_until"].startswith("2026-07-31T00:00:00+08:00")


def test_win_resets_streak_but_prior_three_loss_event_keeps_day_locked():
    outcomes = [
        _outcome("2026-07-30T09:00:00", -1, "1"),
        _outcome("2026-07-30T10:00:00", -1, "2"),
        _outcome("2026-07-30T11:00:00", -1, "3"),
        _outcome("2026-07-30T12:00:00", 2, "4"),
    ]
    status = evaluate_loss_guard(
        outcomes, now=_now("2026-07-30T13:00:00"))

    assert status["lock_scope"] == "day"
    assert status["today"]["current_loss_streak"] == 0
    assert status["today"]["max_loss_streak"] == 3


def test_loss_loss_win_loss_loss_is_allowed():
    outcomes = [
        _outcome("2026-07-30T09:00:00", -1, "1"),
        _outcome("2026-07-30T10:00:00", -1, "2"),
        _outcome("2026-07-30T11:00:00", 2, "3"),
        _outcome("2026-07-30T12:00:00", -1, "4"),
        _outcome("2026-07-30T13:00:00", -1, "5"),
    ]
    status = evaluate_loss_guard(
        outcomes, now=_now("2026-07-30T14:00:00"))

    assert status["allowed"] is True
    assert status["today"]["current_loss_streak"] == 2
    assert status["today"]["max_loss_streak"] == 2


def test_streak_resets_at_local_midnight():
    outcomes = [
        _outcome("2026-07-29T23:00:00", -1, "1"),
        _outcome("2026-07-29T23:30:00", -1, "2"),
        _outcome("2026-07-30T00:10:00", -1, "3"),
    ]
    status = evaluate_loss_guard(
        outcomes, now=_now("2026-07-30T01:00:00"))

    assert status["allowed"] is True
    assert status["today"]["current_loss_streak"] == 1


def test_consecutive_trigger_days_create_seven_day_lock():
    outcomes = [
        _outcome("2026-07-29T09:00:00", -1, "1"),
        _outcome("2026-07-29T10:00:00", -1, "2"),
        _outcome("2026-07-29T11:00:00", -1, "3"),
        _outcome("2026-07-30T09:00:00", -1, "4"),
        _outcome("2026-07-30T10:00:00", -1, "5"),
        _outcome("2026-07-30T11:00:00", -1, "6"),
    ]
    status = evaluate_loss_guard(
        outcomes, now=_now("2026-07-31T12:00:00"))

    assert status["allowed"] is False
    assert status["lock_scope"] == "week"
    assert status["weekly_trigger_days"] == ["2026-07-29", "2026-07-30"]
    assert status["lock_until"].startswith("2026-08-06T11:00:00+08:00")


def test_nonconsecutive_trigger_days_do_not_create_week_lock():
    outcomes = [
        _outcome("2026-07-28T09:00:00", -1, "1"),
        _outcome("2026-07-28T10:00:00", -1, "2"),
        _outcome("2026-07-28T11:00:00", -1, "3"),
        _outcome("2026-07-30T09:00:00", -1, "4"),
        _outcome("2026-07-30T10:00:00", -1, "5"),
        _outcome("2026-07-30T11:00:00", -1, "6"),
    ]
    status = evaluate_loss_guard(
        outcomes, now=_now("2026-07-30T12:00:00"))

    assert status["lock_scope"] == "day"
    assert status["weekly_trigger_days"] == []


def test_week_lock_expires_exactly_seven_days_after_second_trigger():
    outcomes = [
        _outcome("2026-07-20T09:00:00", -1, "1"),
        _outcome("2026-07-20T10:00:00", -1, "2"),
        _outcome("2026-07-20T11:00:00", -1, "3"),
        _outcome("2026-07-21T09:00:00", -1, "4"),
        _outcome("2026-07-21T10:00:00", -1, "5"),
        _outcome("2026-07-21T11:00:00", -1, "6"),
    ]
    status = evaluate_loss_guard(
        outcomes, now=_now("2026-07-28T11:00:00"))

    assert status["allowed"] is True
    assert status["lock_scope"] == "none"


def test_service_builds_guard_from_binance_history(monkeypatch):
    now = _now("2026-07-30T12:00:00")
    incomes = [
        {
            "incomeType": "REALIZED_PNL", "symbol": "BTCUSDT",
            "tradeId": str(i),
        }
        for i in (1, 2, 3)
    ]
    trades = [
        {
            "symbol": "BTCUSDT", "id": i, "orderId": 100 + i,
            "realizedPnl": "-1",
            "time": _ms(f"2026-07-30T{8 + i:02d}:00:00"),
        }
        for i in (1, 2, 3)
    ]

    monkeypatch.setattr(
        service, "_resolve", lambda cid: ("testnet", "key", "secret"))
    monkeypatch.setattr(
        service.bn, "income_history",
        lambda *args, **kwargs: incomes if kwargs["page"] == 1 else [],
    )
    monkeypatch.setattr(
        service.bn, "user_trades",
        lambda *args, **kwargs: trades,
    )

    status = service.get_loss_guard_status(1, now=now)
    assert status["lock_scope"] == "day"
    assert status["outcome_count"] == 3
    assert status["source"].startswith("Binance REALIZED_PNL")


def test_prepare_fixed_risk_order_refuses_locked_account(monkeypatch):
    monkeypatch.setattr(
        service, "get_loss_guard_status",
        lambda *args, **kwargs: {
            "allowed": False,
            "reason": "今日已出现三次连续亏损",
            "lock_until": "2026-07-31T00:00:00+08:00",
        },
    )
    with pytest.raises(ValueError, match="风险闸门已锁定"):
        service.prepare_fixed_risk_order(
            credential_id=1,
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=0.01,
            leverage=10,
        )


def test_risk_guard_route_returns_status(monkeypatch):
    monkeypatch.setattr(
        trading_api, "get_loss_guard_status",
        lambda credential_id: {
            "credential_id": credential_id,
            "allowed": True,
        },
    )
    assert trading_api.risk_guard(7) == {
        "credential_id": 7,
        "allowed": True,
    }
