"""
Trading endpoints — Binance USDT-M perpetual.

All endpoints require the standard cookie-session auth (handled at the router
level via the existing dependency wiring). Sensitive material (api_secret) is
never echoed back in any response.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app_logger import log as applog
from trading.credentials import (
    add_credential, list_credentials, delete_credential, set_enabled,
)
from trading.service import (
    test_credential, get_account, place_order, list_recent_orders,
    place_order_bracket, close_position, list_open_orders,
    cancel_open_order, list_binance_order_history,
)
from trading.models import OrderRequest


router = APIRouter(prefix="/trading", tags=["trading"])


# ---------- credentials ----------

class CredentialCreate(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    env: str = Field(pattern="^(testnet|mainnet)$")
    api_key: str = Field(min_length=10, max_length=256)
    api_secret: str = Field(min_length=10, max_length=256)


@router.get("/credentials")
def credentials_list():
    return {"credentials": list_credentials()}


@router.post("/credentials")
def credentials_add(body: CredentialCreate):
    try:
        new_id = add_credential(body.label, body.env, body.api_key, body.api_secret)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    applog("trading", "info", f"credential added id={new_id} env={body.env}")
    return {"id": new_id}


@router.delete("/credentials/{credential_id}")
def credentials_delete(credential_id: int):
    if not delete_credential(credential_id):
        raise HTTPException(status_code=404, detail="credential not found")
    return {"ok": True}


class EnabledToggle(BaseModel):
    enabled: bool


@router.post("/credentials/{credential_id}/enabled")
def credentials_toggle(credential_id: int, body: EnabledToggle):
    if not set_enabled(credential_id, body.enabled):
        raise HTTPException(status_code=404, detail="credential not found")
    return {"ok": True}


@router.post("/credentials/{credential_id}/test")
def credentials_test(credential_id: int):
    try:
        return test_credential(credential_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        # Surface Binance errors verbatim so the user can see e.g. -2015 IP whitelist
        raise HTTPException(status_code=400, detail=str(e))


# ---------- account snapshot ----------

@router.get("/account/{credential_id}")
def account(credential_id: int):
    try:
        return get_account(credential_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- order placement ----------

class OrderBody(BaseModel):
    credential_id: int
    symbol: str = Field(min_length=1, max_length=20)
    side: str = Field(pattern="^(BUY|SELL)$")
    order_type: str = Field(pattern="^(MARKET|LIMIT)$")
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    leverage: int = Field(default=1, ge=1, le=125)
    margin_type: str = Field(default="ISOLATED", pattern="^(ISOLATED|CROSSED)$")
    reduce_only: bool = False


@router.post("/order")
def place(body: OrderBody):
    req = OrderRequest(
        symbol=body.symbol.upper(),
        side=body.side,
        order_type=body.order_type,
        quantity=body.quantity,
        price=body.price,
        leverage=body.leverage,
        margin_type=body.margin_type,
        reduce_only=body.reduce_only,
    )
    try:
        result = place_order(body.credential_id, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result.to_dict()


# ---------- bracket order (entry + optional SL/TP) ----------

class BracketOrderBody(BaseModel):
    credential_id: int
    symbol: str = Field(min_length=1, max_length=20)
    side: str = Field(pattern="^(BUY|SELL)$")
    order_type: str = Field(pattern="^(MARKET|LIMIT)$")
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    leverage: int = Field(default=1, ge=1, le=125)
    margin_type: str = Field(default="ISOLATED", pattern="^(ISOLATED|CROSSED)$")
    reduce_only: bool = False
    stop_loss_price: float | None = Field(default=None, gt=0)
    take_profit_price: float | None = Field(default=None, gt=0)


@router.post("/order/bracket")
def place_bracket(body: BracketOrderBody):
    req = OrderRequest(
        symbol=body.symbol.upper(),
        side=body.side,
        order_type=body.order_type,
        quantity=body.quantity,
        price=body.price,
        leverage=body.leverage,
        margin_type=body.margin_type,
        reduce_only=body.reduce_only,
    )
    try:
        result = place_order_bracket(
            body.credential_id, req,
            stop_loss_price=body.stop_loss_price,
            take_profit_price=body.take_profit_price,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result.to_dict()


# ---------- close position ----------

class ClosePositionBody(BaseModel):
    credential_id: int
    symbol: str = Field(min_length=1, max_length=20)


@router.post("/position/close")
def close(body: ClosePositionBody):
    try:
        return close_position(body.credential_id, body.symbol).to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- open orders (pending on Binance) ----------

@router.get("/open-orders/{credential_id}")
def open_orders(credential_id: int, symbol: str | None = None):
    try:
        rows = list_open_orders(credential_id, symbol=symbol)
        return {"orders": rows}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class CancelBody(BaseModel):
    credential_id: int
    symbol: str = Field(min_length=1, max_length=20)
    order_id: int | str


@router.post("/open-orders/cancel")
def cancel(body: CancelBody):
    try:
        return cancel_open_order(body.credential_id, body.symbol, body.order_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- Binance order history (filled/canceled per symbol) ----------

@router.get("/history/{credential_id}")
def binance_order_history(credential_id: int, symbol: str, limit: int = 50):
    try:
        rows = list_binance_order_history(credential_id, symbol, limit=limit)
        return {"orders": rows}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------- order history (our DB audit log) ----------

@router.get("/orders")
def orders(limit: int = 50):
    return {"orders": list_recent_orders(limit=max(1, min(500, limit)))}
