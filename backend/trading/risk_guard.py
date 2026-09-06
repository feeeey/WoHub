"""Pure loss-streak risk guard for opening trades.

Binance can split one closing order into multiple fills. We therefore map
REALIZED_PNL income rows back to account trades and aggregate by orderId before
counting wins/losses. One closing order is one outcome.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DAILY_CONSECUTIVE_LOSS_LIMIT = 3
WEEK_LOCK_DURATION = timedelta(days=7)


def _trade_key(symbol: Any, trade_id: Any) -> tuple[str, str]:
    return str(symbol or "").upper(), str(trade_id if trade_id is not None else "")


def build_closed_order_outcomes(
    income_rows: list[dict],
    user_trade_rows: list[dict],
) -> list[dict[str, Any]]:
    """Map REALIZED_PNL rows to fills and aggregate them by closing order.

    Opening fills are excluded because they do not have a corresponding
    REALIZED_PNL income row. A multi-fill exit is collapsed to one order-level
    result so it can never manufacture a three-loss streak by itself.
    """
    realized_trade_keys: set[tuple[str, str]] = set()
    for row in income_rows:
        if row.get("incomeType") != "REALIZED_PNL":
            continue
        symbol, trade_id = _trade_key(row.get("symbol"), row.get("tradeId"))
        if not symbol or not trade_id:
            raise ValueError("已实现盈亏记录缺少 symbol/tradeId，无法安全判断连亏")
        realized_trade_keys.add((symbol, trade_id))

    # Query windows share an inclusive boundary; dedupe fills by symbol+id.
    trades_by_key: dict[tuple[str, str], dict] = {}
    for row in user_trade_rows:
        key = _trade_key(row.get("symbol"), row.get("id"))
        if key in realized_trade_keys:
            trades_by_key[key] = row

    missing = realized_trade_keys - trades_by_key.keys()
    if missing:
        raise ValueError(
            f"有 {len(missing)} 条已实现盈亏无法关联成交明细，禁止在历史不完整时开仓")

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for key, row in trades_by_key.items():
        symbol = key[0]
        order_id = str(row.get("orderId", ""))
        if not order_id:
            raise ValueError("成交明细缺少 orderId，无法合并多次成交")
        group_key = (symbol, order_id)
        item = grouped.setdefault(group_key, {
            "symbol": symbol,
            "order_id": order_id,
            "realized_pnl": 0.0,
            "time": 0,
            "fill_count": 0,
        })
        item["realized_pnl"] += float(row.get("realizedPnl", 0) or 0)
        item["time"] = max(item["time"], int(row.get("time", 0) or 0))
        item["fill_count"] += 1

    outcomes = [
        row for row in grouped.values()
        if row["time"] > 0 and row["realized_pnl"] != 0
    ]
    outcomes.sort(key=lambda row: (row["time"], row["symbol"], row["order_id"]))
    return outcomes


def _iso_local(timestamp_ms: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(
        timestamp_ms / 1000, tz=timezone.utc).astimezone(tz).isoformat()


def _empty_day(day: date) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "trade_count": 0,
        "loss_count": 0,
        "win_count": 0,
        "current_loss_streak": 0,
        "max_loss_streak": 0,
        "triggered_at_ms": None,
    }


def evaluate_loss_guard(
    outcomes: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Evaluate day and week locks from chronological order-level outcomes."""
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"未知交易时区：{timezone_name}") from e

    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    now_local = now_utc.astimezone(tz)
    today = now_local.date()

    days: dict[date, dict[str, Any]] = {}
    ordered = sorted(
        outcomes,
        key=lambda row: (
            int(row.get("time", 0)),
            str(row.get("symbol", "")),
            str(row.get("order_id", "")),
        ),
    )
    for outcome in ordered:
        timestamp_ms = int(outcome.get("time", 0) or 0)
        if timestamp_ms <= 0:
            continue
        day = datetime.fromtimestamp(
            timestamp_ms / 1000, tz=timezone.utc).astimezone(tz).date()
        summary = days.setdefault(day, _empty_day(day))
        pnl = float(outcome.get("realized_pnl", 0) or 0)
        summary["trade_count"] += 1
        if pnl < 0:
            summary["loss_count"] += 1
            summary["current_loss_streak"] += 1
            summary["max_loss_streak"] = max(
                summary["max_loss_streak"],
                summary["current_loss_streak"],
            )
            if (
                summary["current_loss_streak"] >= DAILY_CONSECUTIVE_LOSS_LIMIT
                and summary["triggered_at_ms"] is None
            ):
                summary["triggered_at_ms"] = timestamp_ms
        elif pnl > 0:
            summary["win_count"] += 1
            summary["current_loss_streak"] = 0

    triggered_days = sorted(
        day for day, summary in days.items()
        if summary["triggered_at_ms"] is not None
    )
    weekly_trigger_ms: int | None = None
    weekly_pair: tuple[date, date] | None = None
    for second_day in triggered_days:
        first_day = second_day - timedelta(days=1)
        if first_day not in days or days[first_day]["triggered_at_ms"] is None:
            continue
        trigger_ms = int(days[second_day]["triggered_at_ms"])
        if weekly_trigger_ms is None or trigger_ms > weekly_trigger_ms:
            weekly_trigger_ms = trigger_ms
            weekly_pair = (first_day, second_day)

    lock_scope = "none"
    lock_until_utc: datetime | None = None
    reason = ""

    if weekly_trigger_ms is not None:
        weekly_trigger = datetime.fromtimestamp(
            weekly_trigger_ms / 1000, tz=timezone.utc)
        weekly_until = weekly_trigger + WEEK_LOCK_DURATION
        if now_utc < weekly_until:
            lock_scope = "week"
            lock_until_utc = weekly_until
            assert weekly_pair is not None
            reason = (
                f"{weekly_pair[0].isoformat()} 与 {weekly_pair[1].isoformat()} "
                "连续两天均出现三连亏，触发 7 天开仓锁定"
            )

    today_summary = days.get(today, _empty_day(today))
    if lock_scope == "none" and today_summary["triggered_at_ms"] is not None:
        next_day = today + timedelta(days=1)
        lock_scope = "day"
        lock_until_utc = datetime.combine(
            next_day, time.min, tzinfo=tz).astimezone(timezone.utc)
        reason = "今日已出现三次连续亏损，次日零点前禁止开仓"

    recent = []
    for outcome in ordered[-20:]:
        pnl = float(outcome["realized_pnl"])
        recent.append({
            **outcome,
            "result": "loss" if pnl < 0 else "win",
            "local_time": _iso_local(int(outcome["time"]), tz),
        })

    daily_triggers = [
        {
            "date": day.isoformat(),
            "triggered_at": _iso_local(
                int(days[day]["triggered_at_ms"]), tz),
        }
        for day in triggered_days
    ]
    return {
        "allowed": lock_scope == "none",
        "locked": lock_scope != "none",
        "lock_scope": lock_scope,
        "reason": reason,
        "timezone": timezone_name,
        "now": now_local.isoformat(),
        "lock_until": (
            lock_until_utc.astimezone(tz).isoformat()
            if lock_until_utc else None
        ),
        "today": today_summary,
        "daily_triggers": daily_triggers,
        "recent_outcomes": recent,
        "weekly_trigger_days": (
            [weekly_pair[0].isoformat(), weekly_pair[1].isoformat()]
            if weekly_pair and lock_scope == "week" else []
        ),
        "zero_pnl_policy": "ignored",
        "fee_funding_excluded": True,
    }
