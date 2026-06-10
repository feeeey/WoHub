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
    ("unknown", None) — "unknown" means the caller must assume the worst."""
    for _ in range(2):  # one extra try if the query itself hits a network error
        try:
            raw = bn.get_order(env, api_key, secret, symbol,
                               orig_client_order_id=client_oid)
            return "exists", raw
        except BinanceAPIError as e:
            if e.code == bn.ERR_ORDER_NOT_FOUND:
                return "absent", None
            return "unknown", None
        except requests.RequestException:
            continue
    return "unknown", None


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
    except BinanceAPIError as e:
        result = OrderResult(ok=False, error=str(e))
        _record_order(credential_id, env, req, result)
        return result
    except requests.RequestException as e:
        # Transport died — the order MAY have reached Binance. Resolve by id.
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
        applog("binance_trade", "warn",
               f"下单请求网络异常，但订单已被交易所接受（{client_oid}）")
        # state == "exists": fall through with raw from get_order

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
