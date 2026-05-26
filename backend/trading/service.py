"""
High-level trading service.

Wraps the raw fapi client with:
* credential lookup + decryption
* idempotent leverage / margin-type preparation before placing an order
* DB persistence of attempted orders (for audit/history)
* dataclass-friendly Position / Balance shapes for the API layer
"""
import json
from typing import Any

from config import settings
from database import get_db
from app_logger import log as applog

from trading import binance_client as bn
from trading.binance_client import BinanceAPIError
from trading.credentials import get_credential
from trading.models import (
    OrderRequest, OrderResult, Position, Balance, BracketOrderResult,
)


def _resolve(credential_id: int) -> tuple[str, str, str]:
    """Decrypt credential by id. Raises ValueError if missing/disabled."""
    creds = get_credential(credential_id)
    if not creds:
        raise ValueError(f"credential {credential_id} not found or disabled")
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


def place_order(credential_id: int, req: OrderRequest) -> OrderResult:
    """Place one order. Sets leverage + margin type first (idempotent).

    Errors are caught and returned as `OrderResult(ok=False, error=...)` so
    the API layer can render them rather than 500.
    """
    req.validate()
    env, api_key, secret = _resolve(credential_id)

    # ---- preflight: leverage + margin type (both idempotent) ----
    try:
        bn.set_margin_type(env, api_key, secret, req.symbol, req.margin_type)
    except BinanceAPIError as e:
        result = OrderResult(ok=False, error=f"set_margin_type: {e}")
        _record_order(credential_id, env, req, result)
        return result

    try:
        bn.set_leverage(env, api_key, secret, req.symbol, req.leverage)
    except BinanceAPIError as e:
        result = OrderResult(ok=False, error=f"set_leverage: {e}")
        _record_order(credential_id, env, req, result)
        return result

    # ---- place the actual order ----
    try:
        raw = bn.place_order(
            env, api_key, secret,
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            quantity=req.quantity,
            price=req.price,
            reduce_only=req.reduce_only,
        )
    except BinanceAPIError as e:
        result = OrderResult(ok=False, error=str(e))
        _record_order(credential_id, env, req, result)
        return result

    result = OrderResult(
        ok=True,
        binance_order_id=str(raw.get("orderId", "")),
        status=raw.get("status"),
        executed_qty=float(raw.get("executedQty", 0)),
        avg_price=float(raw.get("avgPrice", 0)) or float(raw.get("price", 0) or 0),
        raw=raw,
    )
    _record_order(credential_id, env, req, result)
    return result


def _opposite_side(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY"


def place_order_bracket(
    credential_id: int,
    req: OrderRequest,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
) -> BracketOrderResult:
    """Place an entry order with optional protection orders (SL + TP).

    The protection orders are STOP_MARKET / TAKE_PROFIT_MARKET with
    closePosition=true — when triggered they liquidate the full position at
    market. This is the safest and most common bracket pattern.

    If the entry fails, neither SL nor TP is attempted. If the entry succeeds
    but SL or TP fails, the entry is NOT rolled back — the caller is told and
    can place the missing protection manually.
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
        sl_req = OrderRequest(
            symbol=req.symbol, side=close_side, order_type="STOP_MARKET",
            quantity=req.quantity,  # ignored when closePosition=true, but keeps validator happy
            leverage=req.leverage, margin_type=req.margin_type,
        )
        try:
            raw = bn.place_order(
                env, api_key, secret,
                symbol=req.symbol, side=close_side, order_type="STOP_MARKET",
                stop_price=stop_loss_price, close_position=True,
            )
            sl_result = OrderResult(
                ok=True,
                binance_order_id=str(raw.get("orderId", "")),
                status=raw.get("status"),
                raw=raw,
            )
        except BinanceAPIError as e:
            sl_result = OrderResult(ok=False, error=f"stop_loss: {e}")
        _record_order(credential_id, env, sl_req, sl_result)

    if take_profit_price is not None:
        tp_req = OrderRequest(
            symbol=req.symbol, side=close_side, order_type="TAKE_PROFIT_MARKET",
            quantity=req.quantity,
            leverage=req.leverage, margin_type=req.margin_type,
        )
        try:
            raw = bn.place_order(
                env, api_key, secret,
                symbol=req.symbol, side=close_side, order_type="TAKE_PROFIT_MARKET",
                stop_price=take_profit_price, close_position=True,
            )
            tp_result = OrderResult(
                ok=True,
                binance_order_id=str(raw.get("orderId", "")),
                status=raw.get("status"),
                raw=raw,
            )
        except BinanceAPIError as e:
            tp_result = OrderResult(ok=False, error=f"take_profit: {e}")
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
