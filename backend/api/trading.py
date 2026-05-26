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


# ---------- order history (our DB audit log) ----------

@router.get("/orders")
def orders(limit: int = 50):
    return {"orders": list_recent_orders(limit=max(1, min(500, limit)))}
