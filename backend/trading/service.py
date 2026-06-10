"""
High-level trading service.

Wraps the raw fapi client with:
* credential lookup + decryption
* idempotent leverage / margin-type preparation before placing an order
* DB persistence of attempted orders (for audit/history)
* dataclass-friendly Position / Balance shapes for the API layer
"""
import json
import secrets
import time
from typing import Any

import requests

from config import settings
from database import get_db
from app_logger import log as applog

from trading import binance_client as bn
from trading.binance_client import BinanceAPIError
from trading.credentials import get_credential
from trading.models import (
    OrderRequest, OrderResult, Position, Balance, BracketOrderResult,
    RecoveryResult,
)

from klines.fetcher import fetch_klines
from klines.structure import find_pivot, atr as compute_atr
from trading import position_plan as pp


def _resolve(credential_id: int) -> tuple[str, str, str]:
    """Decrypt credential by id. Raises ValueError if missing/disabled, or if a
    mainnet credential is used while security-critical settings are still at
    their insecure defaults (see config.insecure_defaults())."""
    creds = get_credential(credential_id)
    if not creds:
        raise ValueError(f"credential {credential_id} not found or disabled")
    if creds[0] == "mainnet":
        bad = settings.insecure_defaults()
        if bad:
            raise ValueError(
                "拒绝在不安全的默认配置下使用主网凭据：" + "、".join(bad) +
                " 仍为默认值。请设置强随机的 SECRET_KEY 与 APP_PASSWORD 后重启。"
                "（注意：设置或轮换 SECRET_KEY 会使已加密的 API secret 失效，需重新录入。）"
            )
    return creds  # (env, api_key, api_secret)


def test_credential(credential_id: int) -> dict[str, Any]:
    """Lightweight verification: calls account_info and returns env + a
    truncated key fingerprint. Never returns the secret."""
    env, api_key, secret = _resolve(credential_id)
    bn.account_info(env, api_key, secret)  # raises on failure
    return {"ok": True, "env": env, "api_key_tail": api_key[-6:]}


def get_account(credential_id: int) -> dict[str, Any]:
    env, api_key, secret = _resolve(credential_id)
    raw = bn.account_info(env, api_key, secret)

    # Pull USDT balance specifically (we trade USDT-M perp)
    balances: list[Balance] = []
    for a in raw.get("assets", []):
        if a.get("asset") in ("USDT", "BNFCR"):  # BNFCR = Binance funding receipts; future-safe
            balances.append(Balance(
                asset=a["asset"],
                wallet_balance=float(a.get("walletBalance", 0)),
                available_balance=float(a.get("availableBalance", 0)),
                unrealized_pnl=float(a.get("unrealizedProfit", 0)),
            ))

    positions = [p for p in raw.get("positions", []) if float(p.get("positionAmt", 0)) != 0]
    pos_objs: list[Position] = []
    for p in positions:
        pos_objs.append(Position(
            symbol=p["symbol"],
            position_amt=float(p["positionAmt"]),
            entry_price=float(p.get("entryPrice", 0)),
            mark_price=float(p.get("markPrice") or p.get("entryPrice", 0)),
            unrealized_pnl=float(p.get("unrealizedProfit", 0)),
            leverage=int(p.get("leverage", 1)),
            margin_type=p.get("marginType", "isolated"),
        ))

    return {
        "env": env,
        "total_wallet_balance": float(raw.get("totalWalletBalance", 0)),
        "total_unrealized_pnl": float(raw.get("totalUnrealizedProfit", 0)),
        "available_balance": float(raw.get("availableBalance", 0)),
        "balances": [b.to_dict() for b in balances],
        "positions": [p.to_dict() for p in pos_objs],
    }


def _record_order(
    credential_id: int,
    env: str,
    req: OrderRequest,
    result: OrderResult,
) -> None:
    """Persist the order attempt for audit history."""
    try:
        db = get_db(settings.db_path)
        db.execute(
            "INSERT INTO trading_orders "
            "(credential_id, env, symbol, side, order_type, quantity, price, "
            " leverage, margin_type, binance_order_id, status, response_json, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                credential_id, env, req.symbol, req.side, req.order_type,
                req.quantity, req.price, req.leverage, req.margin_type,
                result.binance_order_id,
                result.status if result.ok else "FAILED",
                json.dumps(result.raw)[:8000],
                result.error,
            ),
        )
        db.commit()
        db.close()
    except Exception as e:
        applog("binance_trade", "warn", f"failed to record order: {e}")


def _new_client_order_id() -> str:
    """Idempotency key for every order we submit (<=36 chars per Binance).
    Lets us resolve ambiguous network failures via get_order, and makes a
    transport-level resend (proxy->direct fallback) a rejected duplicate
    instead of a second fill."""
    return f"wohub-{secrets.token_hex(10)}"


def _query_order_state(
    env: str, api_key: str, secret: str, symbol: str, client_oid: str,
) -> tuple[str, dict | None]:
    """After a network error left a submission ambiguous, ask Binance whether
    the order exists. Returns ("exists", raw) / ("absent", None) /
    ("unknown", None) — "unknown" means the caller must assume the worst.

    "exists" only proves the row is on Binance — callers must still check the
    status via _order_effective(); a CANCELED/EXPIRED row protects nothing."""
    for _ in range(2):  # one extra try if the query itself fails transiently
        try:
            raw = bn.get_order(env, api_key, secret, symbol,
                               orig_client_order_id=client_oid)
            return "exists", raw
        except BinanceAPIError as e:
            if e.code == bn.ERR_ORDER_NOT_FOUND:
                return "absent", None
            if bn.is_retryable(e):
                continue
            return "unknown", None
        except requests.RequestException:
            continue
    return "unknown", None


ORDER_DEAD_STATUSES = ("CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH")


def _order_effective(raw: dict | None) -> bool:
    """True when a recovered order landed in a state that does (or already
    did) its job: NEW / PARTIALLY_FILLED / FILLED. A CANCELED/EXPIRED/REJECTED
    row exists on Binance but protects and executes nothing — treating it as
    success would leave a position without its stop."""
    return bool(raw) and (raw.get("status") or "") not in ORDER_DEAD_STATUSES


def place_order(credential_id: int, req: OrderRequest) -> OrderResult:
    """Place one order. Sets leverage + margin type first (idempotent).

    Errors are caught and returned as `OrderResult(ok=False, error=...)` so
    the API layer can render them rather than 500.
    """
    req.validate()
    env, api_key, secret = _resolve(credential_id)

    # ---- preflight: margin type (best-effort) ----
    # Margin type is a per-symbol preference, not a hard requirement for placing
    # an order. If Binance refuses to set it — account in Multi-Assets / cross-only
    # mode, the symbol already uses this mode, or the config endpoint returns
    # -1109 on an otherwise-valid account — do NOT abort the order. Proceed with
    # the account's current margin mode and surface a warning; a genuinely
    # untradeable account still fails at set_leverage or the order itself.
    margin_warning: str | None = None
    try:
        bn.set_margin_type(env, api_key, secret, req.symbol, req.margin_type)
    except BinanceAPIError as e:
        margin_warning = f"未能设置保证金模式（{req.margin_type}），沿用账户当前模式：{e}"
        applog("binance_trade", "warn", margin_warning)

    # ---- preflight: leverage (required — affects margin & liquidation price) ----
    try:
        bn.set_leverage(env, api_key, secret, req.symbol, req.leverage)
    except BinanceAPIError as e:
        result = OrderResult(ok=False, error=f"set_leverage: {e}")
        _record_order(credential_id, env, req, result)
        return result

    # ---- place the actual order ----
    client_oid = _new_client_order_id()
    try:
        raw = bn.place_order(
            env, api_key, secret,
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            quantity=req.quantity,
            price=req.price,
            reduce_only=req.reduce_only,
            new_client_order_id=client_oid,
        )
    except (BinanceAPIError, requests.RequestException) as e:
        if (isinstance(e, BinanceAPIError)
                and e.code != bn.ERR_DUPLICATE_CLIENT_ORDER_ID):
            result = OrderResult(ok=False, error=str(e))
            _record_order(credential_id, env, req, result)
            return result
        # Ambiguous: transport died, OR a duplicate-id rejection says an
        # earlier transport-level resend already landed. Resolve by id.
        state, raw = _query_order_state(env, api_key, secret, req.symbol, client_oid)
        if state == "absent":
            result = OrderResult(ok=False, error=f"网络异常，订单未送达交易所：{e}")
            _record_order(credential_id, env, req, result)
            return result
        if state == "unknown":
            msg = f"网络异常且订单状态未知（{client_oid}），请立即检查持仓与挂单：{e}"
            applog("binance_trade", "error", msg)
            result = OrderResult(ok=False, error=msg)
            _record_order(credential_id, env, req, result)
            return result
        if not _order_effective(raw):
            result = OrderResult(
                ok=False, status=raw.get("status"), raw=raw,
                error=f"订单已送达但状态为 {raw.get('status')}，未生效")
            _record_order(credential_id, env, req, result)
            return result
        applog("binance_trade", "warn",
               f"下单请求异常（{e}），但订单已被交易所接受（{client_oid}）")
        # state == "exists" and effective: fall through with raw from get_order

    result = OrderResult(
        ok=True,
        binance_order_id=str(raw.get("orderId", "")),
        status=raw.get("status"),
        executed_qty=float(raw.get("executedQty", 0)),
        avg_price=float(raw.get("avgPrice", 0)) or float(raw.get("price", 0) or 0),
        raw=raw,
        warning=margin_warning,
    )
    _record_order(credential_id, env, req, result)
    return result


def _opposite_side(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY"


PROTECTION_ATTEMPTS = 3
PROTECTION_BACKOFF_S = (0.5, 1.0)

# module-level alias so tests can stub the wait without touching time itself
_sleep = time.sleep


def _place_protection_with_retry(
    env: str, api_key: str, secret: str, *,
    symbol: str, side: str, order_type: str, stop_price: float,
    client_oid: str,
) -> OrderResult:
    """Place a closePosition trigger order (SL/TP) with bounded retries.

    Every attempt reuses the same clientOrderId: our own retry or a
    transport-level resend can never create a second live trigger order, and
    an ambiguous earlier attempt is recovered via get_order instead of
    re-submitted blind. Fatal rejections (filters, would-immediately-trigger)
    fail on the first attempt — see binance_client.is_retryable."""
    last_err: Exception | None = None
    for attempt in range(PROTECTION_ATTEMPTS):
        try:
            raw = bn.place_order(
                env, api_key, secret,
                symbol=symbol, side=side, order_type=order_type,
                stop_price=stop_price, close_position=True,
                new_client_order_id=client_oid,
            )
            return OrderResult(
                ok=True, binance_order_id=str(raw.get("orderId", "")),
                status=raw.get("status"), raw=raw,
            )
        except BinanceAPIError as e:
            last_err = e
            if e.code == bn.ERR_DUPLICATE_CLIENT_ORDER_ID:
                # An earlier transport-level resend (or a late-landing first
                # attempt after an "absent" verdict) already created the order
                # — verify instead of failing a submission that succeeded.
                state, raw = _query_order_state(env, api_key, secret, symbol, client_oid)
                if state == "exists" and _order_effective(raw):
                    return OrderResult(
                        ok=True, binance_order_id=str(raw.get("orderId", "")),
                        status=raw.get("status"),
                        executed_qty=float(raw.get("executedQty", 0) or 0),
                        avg_price=float(raw.get("avgPrice", 0) or 0), raw=raw,
                    )
                return OrderResult(
                    ok=False,
                    error=f"{order_type}: clientOrderId 重复但订单未生效（{state}）：{e}")
            if not bn.is_retryable(e):
                return OrderResult(ok=False, error=f"{order_type}: {e}")
        except requests.RequestException as e:
            last_err = e
            state, raw = _query_order_state(env, api_key, secret, symbol, client_oid)
            if state == "exists":
                if _order_effective(raw):
                    return OrderResult(
                        ok=True, binance_order_id=str(raw.get("orderId", "")),
                        status=raw.get("status"),
                        executed_qty=float(raw.get("executedQty", 0) or 0),
                        avg_price=float(raw.get("avgPrice", 0) or 0), raw=raw,
                    )
                # The row exists but is CANCELED/EXPIRED — it protects
                # nothing. Fail so the caller undoes the entry.
                return OrderResult(
                    ok=False,
                    error=f"{order_type}: 订单已送达但状态为 {raw.get('status')}，未生效")
            if state == "unknown":
                return OrderResult(
                    ok=False, error=f"{order_type}: 网络异常且订单状态未知：{e}")
            # state == "absent": never reached Binance; safe to retry same id.
        if attempt < PROTECTION_ATTEMPTS - 1:
            _sleep(PROTECTION_BACKOFF_S[min(attempt, len(PROTECTION_BACKOFF_S) - 1)])
    return OrderResult(
        ok=False,
        error=f"{order_type}: 重试{PROTECTION_ATTEMPTS}次后仍失败：{last_err}")


ENTRY_OPEN_STATUSES = ("NEW", "PARTIALLY_FILLED")


def _close_entry_qty(
    credential_id: int, env: str, api_key: str, secret: str,
    req: OrderRequest, qty: float,
) -> OrderResult | None:
    """Reduce-only market close of exactly the entry's filled quantity.
    Returns None when nothing attributable to the entry remains to close."""
    pos_amt: float | None
    try:
        rows = bn.position_risk(env, api_key, secret, symbol=req.symbol)
        pos_amt = sum(float(r.get("positionAmt", 0)) for r in rows)
    except (BinanceAPIError, requests.RequestException):
        pos_amt = None  # can't see the position; close qty blind (reduce-only is safe)

    if pos_amt is not None:
        entry_sign = 1.0 if req.side == "BUY" else -1.0
        if pos_amt * entry_sign <= 0:
            return None  # position gone or opposite-direction: nothing of ours left
        qty = min(qty, abs(pos_amt))

    close_side = _opposite_side(req.side)
    close_oid = _new_client_order_id()
    close_req = OrderRequest(
        symbol=req.symbol, side=close_side, order_type="MARKET",
        quantity=qty, leverage=req.leverage, margin_type=req.margin_type,
        reduce_only=True,
    )
    try:
        raw = bn.place_order(
            env, api_key, secret,
            symbol=req.symbol, side=close_side, order_type="MARKET",
            quantity=qty, reduce_only=True, new_client_order_id=close_oid,
        )
        result = OrderResult(
            ok=True, binance_order_id=str(raw.get("orderId", "")),
            status=raw.get("status"),
            executed_qty=float(raw.get("executedQty", 0)),
            avg_price=float(raw.get("avgPrice", 0) or 0), raw=raw,
        )
    except (BinanceAPIError, requests.RequestException) as e:
        if (isinstance(e, BinanceAPIError)
                and e.code != bn.ERR_DUPLICATE_CLIENT_ORDER_ID):
            result = OrderResult(ok=False, error=str(e))
        else:
            state, raw = _query_order_state(env, api_key, secret, req.symbol, close_oid)
            if state == "exists" and _order_effective(raw):
                result = OrderResult(
                    ok=True, binance_order_id=str(raw.get("orderId", "")),
                    status=raw.get("status"),
                    executed_qty=float(raw.get("executedQty", 0) or 0),
                    avg_price=float(raw.get("avgPrice", 0) or 0), raw=raw,
                )
            else:
                result = OrderResult(ok=False, error=f"平仓请求异常（{state}）：{e}")
    _record_order(credential_id, env, close_req, result)
    return result


def _undo_entry(
    credential_id: int, env: str, api_key: str, secret: str,
    req: OrderRequest, entry: OrderResult, sl_client_oid: str,
) -> RecoveryResult:
    """以损定仓：a position whose stop cannot be placed must not exist.
    Cancel the unfilled remainder of the entry and market-close the filled
    quantity. Sets naked_position when the undo could not be completed."""
    detail_parts: list[str] = []
    entry_cancel: OrderResult | None = None
    close_result: OrderResult | None = None
    naked = False

    # 1. Best-effort: remove a possibly-orphaned SL trigger order (an
    #    ambiguous attempt may have landed). Nothing there is the normal case.
    try:
        bn.cancel_order(env, api_key, secret, req.symbol,
                        orig_client_order_id=sl_client_oid)
        detail_parts.append("已撤销疑似残留的止损触发单")
    except (BinanceAPIError, requests.RequestException):
        pass

    executed = entry.executed_qty
    if executed <= 0 and entry.status == "FILLED":
        executed = req.quantity  # degenerate raw without executedQty

    # 2. Cancel the unfilled remainder of the entry.
    if entry.status in ENTRY_OPEN_STATUSES and entry.binance_order_id:
        try:
            raw = bn.cancel_order(env, api_key, secret, req.symbol,
                                  order_id=entry.binance_order_id)
            executed = float(raw.get("executedQty", executed) or 0)
            entry_cancel = OrderResult(
                ok=True, binance_order_id=str(raw.get("orderId", "")),
                status=raw.get("status"), executed_qty=executed, raw=raw,
            )
            detail_parts.append("已撤销未成交的入场单")
        except (BinanceAPIError, requests.RequestException) as e:
            entry_cancel = OrderResult(ok=False, error=str(e))
            naked = True  # the order may still fill later, unprotected
            detail_parts.append(f"撤销入场单失败：{e}")

    # 3. Close whatever filled.
    if executed > 0:
        close_result = _close_entry_qty(
            credential_id, env, api_key, secret, req, executed)
        if close_result is None:
            detail_parts.append("持仓已不存在或方向相反，无需平仓")
        elif close_result.ok:
            detail_parts.append(f"已市价平掉本次成交量 {executed}")
        else:
            naked = True
            detail_parts.append(f"自动平仓失败：{close_result.error}")

    detail = "；".join(detail_parts) or "无需任何撤销动作"
    if naked:
        applog("binance_trade", "error",
               f"⚠️ {req.symbol} 入场撤销未完成，可能存在无止损保护的持仓：{detail}")
    else:
        applog("binance_trade", "warn",
               f"{req.symbol} 止损设置失败，入场已撤销：{detail}")
    return RecoveryResult(
        attempted=True, entry_cancel=entry_cancel, close=close_result,
        naked_position=naked, detail=detail,
    )


def place_order_bracket(
    credential_id: int,
    req: OrderRequest,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
) -> BracketOrderResult:
    """Place an entry order with optional protection orders (SL + TP).

    Protection orders are STOP_MARKET / TAKE_PROFIT_MARKET with
    closePosition=true. The stop-loss is NOT best-effort: if it cannot be
    placed after bounded retries, the entry is undone (unfilled remainder
    cancelled, filled quantity market-closed) — 以损定仓, a position whose
    stop cannot exist must not exist. TP failure with the SL in place keeps
    the position and reports the failure. See
    docs/superpowers/specs/2026-06-10-bracket-sl-recovery-design.md.
    """
    entry = place_order(credential_id, req)
    if not entry.ok:
        return BracketOrderResult(ok=False, entry=entry)

    if stop_loss_price is None and take_profit_price is None:
        return BracketOrderResult(ok=True, entry=entry)

    # Both SL and TP must close in the opposite direction of the entry.
    close_side = _opposite_side(req.side)
    env, api_key, secret = _resolve(credential_id)

    sl_result: OrderResult | None = None
    tp_result: OrderResult | None = None

    if stop_loss_price is not None:
        sl_oid = _new_client_order_id()
        sl_req = OrderRequest(
            symbol=req.symbol, side=close_side, order_type="STOP_MARKET",
            quantity=req.quantity,  # ignored when closePosition=true, but keeps validator happy
            leverage=req.leverage, margin_type=req.margin_type,
        )
        sl_result = _place_protection_with_retry(
            env, api_key, secret,
            symbol=req.symbol, side=close_side, order_type="STOP_MARKET",
            stop_price=stop_loss_price, client_oid=sl_oid,
        )
        _record_order(credential_id, env, sl_req, sl_result)
        if not sl_result.ok:
            applog("binance_trade", "error",
                   f"{req.symbol} 止损单设置失败，启动入场撤销：{sl_result.error}")
            recovery = _undo_entry(
                credential_id, env, api_key, secret, req, entry, sl_oid)
            return BracketOrderResult(
                ok=False, entry=entry, stop_loss=sl_result,
                take_profit=None, recovery=recovery,
            )

    if take_profit_price is not None:
        tp_oid = _new_client_order_id()
        tp_req = OrderRequest(
            symbol=req.symbol, side=close_side, order_type="TAKE_PROFIT_MARKET",
            quantity=req.quantity,
            leverage=req.leverage, margin_type=req.margin_type,
        )
        tp_result = _place_protection_with_retry(
            env, api_key, secret,
            symbol=req.symbol, side=close_side, order_type="TAKE_PROFIT_MARKET",
            stop_price=take_profit_price, client_oid=tp_oid,
        )
        _record_order(credential_id, env, tp_req, tp_result)

    overall = entry.ok and (sl_result is None or sl_result.ok) and (tp_result is None or tp_result.ok)
    return BracketOrderResult(
        ok=overall, entry=entry, stop_loss=sl_result, take_profit=tp_result,
    )


def close_position(credential_id: int, symbol: str) -> OrderResult:
    """Close the position for `symbol` with a reduce-only market order in
    the opposite direction. Returns an error result if no position exists."""
    env, api_key, secret = _resolve(credential_id)
    rows = bn.position_risk(env, api_key, secret, symbol=symbol.upper())
    open_rows = [r for r in rows if float(r.get("positionAmt", 0)) != 0]
    if not open_rows:
        return OrderResult(ok=False, error=f"no open position on {symbol}")

    # One-way mode: at most one row per symbol. Sum just in case.
    total = sum(float(r["positionAmt"]) for r in open_rows)
    side = "SELL" if total > 0 else "BUY"
    qty = abs(total)

    req = OrderRequest(
        symbol=symbol.upper(), side=side, order_type="MARKET",
        quantity=qty, reduce_only=True,
    )
    try:
        raw = bn.place_order(
            env, api_key, secret,
            symbol=symbol.upper(), side=side, order_type="MARKET",
            quantity=qty, reduce_only=True,
        )
        result = OrderResult(
            ok=True,
            binance_order_id=str(raw.get("orderId", "")),
            status=raw.get("status"),
            executed_qty=float(raw.get("executedQty", 0)),
            avg_price=float(raw.get("avgPrice", 0)) or 0.0,
            raw=raw,
        )
    except BinanceAPIError as e:
        result = OrderResult(ok=False, error=str(e))
    _record_order(credential_id, env, req, result)
    return result


def close_all(credential_id: int) -> dict[str, Any]:
    """Kill-switch: cancel every open order, then flatten every position with
    reduce-only market orders.

    Cancels run FIRST so closePosition trigger orders cannot fire while we
    flatten. Individual failures never abort the sweep — the report carries
    one row per action so the caller sees exactly what is still open."""
    env, api_key, secret = _resolve(credential_id)
    report: list[dict[str, Any]] = []
    ok = True

    # 1. Cancel all open orders, per symbol.
    try:
        opens = bn.open_orders(env, api_key, secret)
    except (BinanceAPIError, requests.RequestException) as e:
        opens = []
        ok = False
        report.append({"action": "list_open_orders", "ok": False, "error": str(e)})
    for sym in sorted({o["symbol"] for o in opens}):
        try:
            bn.cancel_all_orders(env, api_key, secret, sym)
            report.append({"action": "cancel_all", "symbol": sym, "ok": True})
        except (BinanceAPIError, requests.RequestException) as e:
            ok = False
            report.append({"action": "cancel_all", "symbol": sym, "ok": False,
                           "error": str(e)})

    # 2. Flatten every non-zero position.
    try:
        rows = bn.position_risk(env, api_key, secret)
    except (BinanceAPIError, requests.RequestException) as e:
        rows = []
        ok = False
        report.append({"action": "list_positions", "ok": False, "error": str(e)})
    for r in rows:
        amt = float(r.get("positionAmt", 0) or 0)
        if amt == 0:
            continue
        sym = r["symbol"]
        side = "SELL" if amt > 0 else "BUY"
        qty = abs(amt)
        close_req = OrderRequest(symbol=sym, side=side, order_type="MARKET",
                                 quantity=qty, reduce_only=True)
        try:
            raw = bn.place_order(
                env, api_key, secret,
                symbol=sym, side=side, order_type="MARKET",
                quantity=qty, reduce_only=True,
                new_client_order_id=_new_client_order_id(),
            )
            result = OrderResult(
                ok=True, binance_order_id=str(raw.get("orderId", "")),
                status=raw.get("status"),
                executed_qty=float(raw.get("executedQty", 0) or 0),
                avg_price=float(raw.get("avgPrice", 0) or 0), raw=raw,
            )
        except (BinanceAPIError, requests.RequestException) as e:
            result = OrderResult(ok=False, error=str(e))
        _record_order(credential_id, env, close_req, result)
        entry: dict[str, Any] = {"action": "close", "symbol": sym, "qty": qty,
                                 "ok": result.ok}
        if not result.ok:
            ok = False
            entry["error"] = result.error
        report.append(entry)

    applog("binance_trade", "warn" if ok else "error",
           f"kill-switch executed: ok={ok}, actions={len(report)}")
    return {"ok": ok, "actions": report}


def list_open_orders(credential_id: int, symbol: str | None = None) -> list[dict]:
    env, api_key, secret = _resolve(credential_id)
    return bn.open_orders(env, api_key, secret, symbol=symbol.upper() if symbol else None)


def cancel_open_order(credential_id: int, symbol: str, order_id: int | str) -> dict:
    env, api_key, secret = _resolve(credential_id)
    return bn.cancel_order(env, api_key, secret, symbol.upper(), order_id)


def list_binance_order_history(
    credential_id: int, symbol: str, limit: int = 50,
) -> list[dict]:
    env, api_key, secret = _resolve(credential_id)
    return bn.all_orders(env, api_key, secret, symbol.upper(), limit=limit)


def list_recent_orders(limit: int = 50) -> list[dict]:
    """Recent order attempts from our own DB (audit log). Not a live Binance
    fetch — this is what *we* tried to submit, including failures."""
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            "SELECT id, credential_id, env, symbol, side, order_type, "
            "       quantity, price, leverage, margin_type, "
            "       binance_order_id, status, error_message, created_at "
            "FROM trading_orders ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


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
    position size. Places NO order.

    For MARKET orders the entry is derived from candles[-1].close — the last
    REST snapshot, not a live mark price (may lag by up to one polling cycle).
    """
    symbol = symbol.upper()

    need = min(max(lookback + atr_period + 2 * fractal_k + 5, 50), 1500)
    candles = fetch_klines(symbol, interval, limit=need)
    if not candles:
        raise ValueError(f"no klines for {symbol} {interval}")

    if order_type == "LIMIT":
        if entry_price is None or entry_price <= 0:
            raise ValueError("LIMIT 单需提供有效的 entry_price")
        entry = float(entry_price)
    else:
        entry = candles[-1].close  # live/last price

    atr_value = compute_atr(candles, atr_period)
    if atr_value is None:
        raise ValueError("K线不足，无法计算 ATR")

    structure = find_pivot(candles, direction, entry, k=fractal_k, lookback=lookback)

    env, api_key, secret = _resolve(credential_id)
    raw_acct = bn.account_info(env, api_key, secret)
    equity = float(raw_acct.get("totalWalletBalance", 0)) + float(raw_acct.get("totalUnrealizedProfit", 0))
    available = float(raw_acct.get("availableBalance", 0))
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
