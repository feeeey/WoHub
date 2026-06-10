"""
Direct Binance USDT-M perpetual futures client (no ccxt).

Why hand-rolled:
* Reuses the project's http_client.fetch_with_fallback for proxy fallback.
* No multi-exchange abstraction layer to lag behind Binance changes.
* Tiny surface — only the trading endpoints we actually use.

Signing
-------
Binance fapi signed endpoints require:
  query string (sorted by key insertion order is fine, Binance accepts any order)
  + `timestamp` (ms epoch)
  + `signature` = HMAC-SHA256(query_string, api_secret)
  + header X-MBX-APIKEY: <api_key>

Recv window defaults to 5000ms; we send 5000 explicitly to be deterministic.

Errors
------
Binance returns 4xx with a JSON body like {"code": -2010, "msg": "..."}.
We raise BinanceAPIError carrying both for the caller. Network errors raise
the underlying requests exception (caller decides what to do).
"""
import hashlib
import hmac
import time
import json
from typing import Any
from urllib.parse import urlencode

import requests

from sources.http_client import fetch_with_fallback
from app_logger import log as applog


MAINNET_BASE = "https://fapi.binance.com"
TESTNET_BASE = "https://testnet.binancefuture.com"

RECV_WINDOW_MS = 5000

# Binance fapi error codes we want to handle specifically.
ERR_NO_NEED_TO_CHANGE_MARGIN_TYPE = -4046
ERR_LEVERAGE_NOT_MODIFIED = -4028  # rarely seen — leverage change yields different code in practice
ERR_ORDER_NOT_FOUND = -2013  # GET/DELETE /fapi/v1/order when the order does not exist
ERR_DUPLICATE_CLIENT_ORDER_ID = -4116  # newClientOrderId already used by a live order


class BinanceAPIError(Exception):
    """Wraps a Binance-side error (4xx with code/msg JSON body)."""

    def __init__(self, code: int, msg: str, http_status: int):
        super().__init__(f"Binance error {code}: {msg} (HTTP {http_status})")
        self.code = code
        self.msg = msg
        self.http_status = http_status


# Transient conditions where the same request may succeed on retry.
RETRYABLE_CODES = {-1001, -1003, -1007, -1021}  # internal error / rate limit / timeout / recvWindow


def is_retryable(e: BinanceAPIError) -> bool:
    """True when retrying the same request may succeed (transient server or
    rate-limit conditions). Everything else is fatal by default — for trigger
    orders a default-fatal posture avoids retry storms on filter violations
    (-4xxx), would-immediately-trigger (-2021), insufficient margin (-2019)."""
    return (
        e.code in RETRYABLE_CODES
        or e.http_status in (418, 429)
        or e.http_status >= 500
    )


def base_url(env: str) -> str:
    if env == "testnet":
        return TESTNET_BASE
    if env == "mainnet":
        return MAINNET_BASE
    raise ValueError(f"unknown env {env!r}")


def _sign(query_string: str, api_secret: str) -> str:
    return hmac.new(
        api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _build_signed_query(params: dict[str, Any], api_secret: str) -> str:
    """Append timestamp + recvWindow, build the query string, append signature."""
    p = dict(params)
    p["recvWindow"] = RECV_WINDOW_MS
    p["timestamp"] = int(time.time() * 1000)
    qs = urlencode(p, doseq=False)
    sig = _sign(qs, api_secret)
    return qs + "&signature=" + sig


def _parse_error(resp: requests.Response) -> BinanceAPIError:
    try:
        body = resp.json()
        return BinanceAPIError(
            code=int(body.get("code", 0)),
            msg=str(body.get("msg", "")),
            http_status=resp.status_code,
        )
    except Exception:
        return BinanceAPIError(code=0, msg=resp.text[:200], http_status=resp.status_code)


def _request(
    method: str,
    env: str,
    path: str,
    api_key: str,
    api_secret: str | None,
    params: dict[str, Any] | None = None,
    signed: bool = False,
) -> Any:
    """Generic Binance fapi request.

    * `signed=True` adds the HMAC signature + timestamp.
    * Sensitive data NEVER goes to applog.
    """
    params = dict(params or {})
    url = base_url(env) + path
    headers = {"X-MBX-APIKEY": api_key}

    # http_client.fetch_with_fallback calls raise_for_status() before returning,
    # so 4xx triggers requests.HTTPError. We catch it, lift the response back
    # out, and translate to BinanceAPIError so the caller sees the proper
    # Binance code/msg (e.g. -4046 "no need to change margin type") rather than
    # a generic HTTPError that bubbles up as a 500.
    try:
        if signed:
            assert api_secret is not None, "signed=True requires api_secret"
            qs = _build_signed_query(params, api_secret)
            # POST/DELETE accept the query string for fapi; matches docs.
            full = url + "?" + qs
            applog("binance_trade", "debug",
                   f"{method} {path} params="
                   f"{ {k: v for k, v in params.items() if k not in ('signature',)} }")
            resp = fetch_with_fallback(method.lower(), full, headers=headers)
        else:
            applog("binance_trade", "debug", f"{method} {path} (public)")
            resp = fetch_with_fallback(
                method.lower(), url, params=params, headers=headers,
            )
    except requests.HTTPError as e:
        if e.response is None:
            raise
        raise _parse_error(e.response) from e

    return resp.json()


# ---- Public endpoints ----------------------------------------------------

def server_time(env: str, api_key: str) -> int:
    """GET /fapi/v1/time — returns ms epoch."""
    return _request("GET", env, "/fapi/v1/time", api_key, None)["serverTime"]


def exchange_info(env: str, api_key: str) -> dict:
    return _request("GET", env, "/fapi/v1/exchangeInfo", api_key, None)


# ---- Signed endpoints ----------------------------------------------------

def account_info(env: str, api_key: str, api_secret: str) -> dict:
    """GET /fapi/v2/account — balances, positions, multi-asset mode."""
    return _request("GET", env, "/fapi/v2/account", api_key, api_secret, signed=True)


def position_risk(env: str, api_key: str, api_secret: str, symbol: str | None = None) -> list[dict]:
    """GET /fapi/v2/positionRisk — current positions (one row per symbol/side)."""
    params = {"symbol": symbol} if symbol else {}
    return _request("GET", env, "/fapi/v2/positionRisk", api_key, api_secret, params, signed=True)


def set_leverage(env: str, api_key: str, api_secret: str, symbol: str, leverage: int) -> dict:
    """POST /fapi/v1/leverage — idempotent, Binance returns current setting."""
    return _request(
        "POST", env, "/fapi/v1/leverage", api_key, api_secret,
        {"symbol": symbol, "leverage": leverage},
        signed=True,
    )


def set_margin_type(env: str, api_key: str, api_secret: str, symbol: str, margin_type: str) -> dict | None:
    """POST /fapi/v1/marginType — swallows the 'no change needed' error so the
    caller can call this unconditionally before every order."""
    try:
        return _request(
            "POST", env, "/fapi/v1/marginType", api_key, api_secret,
            {"symbol": symbol, "marginType": margin_type},
            signed=True,
        )
    except BinanceAPIError as e:
        if e.code == ERR_NO_NEED_TO_CHANGE_MARGIN_TYPE:
            return None
        raise


def place_order(
    env: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: float | None = None,
    price: float | None = None,
    stop_price: float | None = None,
    reduce_only: bool = False,
    close_position: bool = False,
    time_in_force: str = "GTC",
    new_client_order_id: str | None = None,
) -> dict:
    """POST /fapi/v1/order.

    Supports:
      MARKET / LIMIT — needs `quantity` (+ `price` for LIMIT)
      STOP_MARKET / TAKE_PROFIT_MARKET — needs `stop_price`; when
        close_position=True the whole position is liquidated on trigger and
        `quantity` is omitted per Binance docs.

    `new_client_order_id` is the caller-supplied idempotency key (<=36 chars).

    The caller must have already set leverage / margin type for the symbol.
    """
    params: dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
    }
    if new_client_order_id:
        # Caller-supplied idempotency key. Binance rejects a duplicate id while
        # the first order is live, so a transport-level resend (proxy->direct
        # fallback in fetch_with_fallback) can never double-fill.
        params["newClientOrderId"] = new_client_order_id

    if order_type in ("MARKET", "LIMIT"):
        if quantity is None or quantity <= 0:
            raise ValueError(f"{order_type} requires quantity > 0")
        params["quantity"] = quantity
        if order_type == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            params["price"] = price
            params["timeInForce"] = time_in_force
        if reduce_only:
            params["reduceOnly"] = "true"

    elif order_type in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
        if stop_price is None:
            raise ValueError(f"{order_type} requires stop_price")
        params["stopPrice"] = stop_price
        if close_position:
            # Closes the whole position on trigger; quantity is forbidden by
            # the API in this mode. closePosition implies reduceOnly.
            params["closePosition"] = "true"
        else:
            if quantity is None or quantity <= 0:
                raise ValueError(f"{order_type} without closePosition requires quantity")
            params["quantity"] = quantity
            if reduce_only:
                params["reduceOnly"] = "true"

    else:
        raise ValueError(f"unsupported order_type: {order_type}")

    return _request("POST", env, "/fapi/v1/order", api_key, api_secret, params, signed=True)


def get_order(
    env: str, api_key: str, api_secret: str, symbol: str,
    order_id: int | str | None = None,
    orig_client_order_id: str | None = None,
) -> dict:
    """GET /fapi/v1/order — single-order status lookup.

    Used to resolve ambiguous submissions (network error after send): query by
    the clientOrderId we generated. Raises BinanceAPIError with code -2013
    (ERR_ORDER_NOT_FOUND) when the order does not exist on Binance.
    """
    if order_id is None and orig_client_order_id is None:
        raise ValueError("get_order requires order_id or orig_client_order_id")
    params: dict[str, Any] = {"symbol": symbol}
    if order_id is not None:
        params["orderId"] = order_id
    if orig_client_order_id is not None:
        params["origClientOrderId"] = orig_client_order_id
    return _request("GET", env, "/fapi/v1/order", api_key, api_secret, params, signed=True)


def cancel_order(
    env: str, api_key: str, api_secret: str, symbol: str,
    order_id: int | str | None = None,
    orig_client_order_id: str | None = None,
) -> dict:
    """DELETE /fapi/v1/order by orderId or by our generated clientOrderId.
    Raises BinanceAPIError -2013 when there is nothing to cancel."""
    if order_id is None and orig_client_order_id is None:
        raise ValueError("cancel_order requires order_id or orig_client_order_id")
    params: dict[str, Any] = {"symbol": symbol}
    if order_id is not None:
        params["orderId"] = order_id
    if orig_client_order_id is not None:
        params["origClientOrderId"] = orig_client_order_id
    return _request("DELETE", env, "/fapi/v1/order", api_key, api_secret, params, signed=True)


def open_orders(
    env: str, api_key: str, api_secret: str,
    symbol: str | None = None,
) -> list[dict]:
    """GET /fapi/v1/openOrders — all unfilled / pending orders.
    When `symbol` is None returns ALL symbols (weight 40); otherwise just that
    symbol (weight 1). Prefer per-symbol calls.
    """
    params = {"symbol": symbol} if symbol else {}
    return _request("GET", env, "/fapi/v1/openOrders", api_key, api_secret, params, signed=True)


def all_orders(
    env: str, api_key: str, api_secret: str,
    symbol: str, limit: int = 50,
) -> list[dict]:
    """GET /fapi/v1/allOrders — order history for one symbol (filled / canceled / etc).
    Per Binance docs, `symbol` is required for this endpoint.
    """
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be in [1, 1000]")
    return _request(
        "GET", env, "/fapi/v1/allOrders", api_key, api_secret,
        {"symbol": symbol, "limit": limit},
        signed=True,
    )
