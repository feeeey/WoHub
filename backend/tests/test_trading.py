"""
Tests for the trading module (Binance USDT-M perp).

Covers:
* Fernet encryption round-trip
* Credentials CRUD against the test DB (fixture reset by conftest)
* HMAC-SHA256 signature against the Binance docs canonical example
* Signed request flow with mocked HTTP
* Service-layer place_order: preflight idempotency + DB persistence
* The set_margin_type "no change needed" (-4046) swallow
"""
import json
import types
import pytest
from unittest.mock import patch

from trading import credentials as creds
from trading.binance_client import (
    _sign, _build_signed_query, base_url, BinanceAPIError,
    place_order as bn_place_order,
    set_margin_type as bn_set_margin_type,
    ERR_NO_NEED_TO_CHANGE_MARGIN_TYPE,
)
from trading.models import OrderRequest, OrderResult
from trading import service


# ---------- Fernet round-trip ----------

def test_encrypt_then_decrypt_roundtrip():
    plain = "this-is-the-api-secret"
    enc = creds.encrypt_secret(plain)
    assert enc != plain
    assert creds.decrypt_secret(enc) == plain


def test_encrypt_two_calls_produce_different_ciphertexts():
    # Fernet uses a fresh IV per call -> ciphertexts must differ
    a = creds.encrypt_secret("same-input")
    b = creds.encrypt_secret("same-input")
    assert a != b
    assert creds.decrypt_secret(a) == creds.decrypt_secret(b) == "same-input"


def test_decrypt_rejects_tampered_token():
    with pytest.raises(ValueError):
        creds.decrypt_secret("not-a-real-token")


# ---------- credentials CRUD ----------

def test_credentials_full_lifecycle():
    new_id = creds.add_credential(
        label="testnet-main",
        env="testnet",
        api_key="testkey_abcdefg",
        api_secret="testsecret_hijklmn",
    )
    listing = creds.list_credentials()
    assert any(r["id"] == new_id for r in listing)

    # listing should not leak the encrypted secret column
    row = next(r for r in listing if r["id"] == new_id)
    assert "api_secret_enc" not in row
    assert row["env"] == "testnet"
    assert row["enabled"] == 1

    # get_credential gives the decrypted tuple
    env, key, secret = creds.get_credential(new_id)
    assert env == "testnet"
    assert key == "testkey_abcdefg"
    assert secret == "testsecret_hijklmn"

    # disabling returns None from get_credential
    creds.set_enabled(new_id, False)
    assert creds.get_credential(new_id) is None

    creds.set_enabled(new_id, True)
    assert creds.get_credential(new_id) is not None

    # delete removes the row
    assert creds.delete_credential(new_id)
    assert all(r["id"] != new_id for r in creds.list_credentials())


def test_add_credential_rejects_invalid_env():
    with pytest.raises(ValueError):
        creds.add_credential("x", "production", "key", "secret")


def test_add_credential_rejects_empty_fields():
    with pytest.raises(ValueError):
        creds.add_credential("x", "testnet", "", "secret")


# ---------- HMAC signature ----------

def test_sign_matches_binance_docs_example():
    # From Binance fapi docs, "Sample Signature":
    #   secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    #   query = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    #   expected = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    query = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1"
             "&price=0.1&recvWindow=5000&timestamp=1499827319559")
    assert _sign(query, secret) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_build_signed_query_appends_timestamp_recvwindow_signature():
    qs = _build_signed_query({"symbol": "BTCUSDT", "side": "BUY"}, "secret")
    # Must include all required fields
    assert "symbol=BTCUSDT" in qs
    assert "side=BUY" in qs
    assert "recvWindow=5000" in qs
    assert "timestamp=" in qs
    assert "&signature=" in qs


def test_base_url_routing():
    assert base_url("mainnet") == "https://fapi.binance.com"
    assert base_url("testnet") == "https://testnet.binancefuture.com"
    with pytest.raises(ValueError):
        base_url("staging")


# ---------- mocked Binance request flow ----------

class _FakeResp:
    """Minimal stand-in for requests.Response that mimics raise_for_status."""
    def __init__(self, status, body):
        self.status_code = status
        self.text = json.dumps(body)
        self._body = body
        self.reason = ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(
                f"{self.status_code} Client Error: for url: test",
                response=self,
            )
            raise err


def _resp(status, body):
    return _FakeResp(status, body)


def _fake_fetch_factory(resp):
    """Build a fetch_with_fallback stand-in that respects raise_for_status."""
    def fake(method, url, **kwargs):
        resp.raise_for_status()
        return resp
    return fake


# Real requests module for HTTPError construction
import requests


def test_place_order_market_buy_uses_signed_post(monkeypatch):
    captured = {}

    def fake_fetch(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        r = _resp(200, {
            "orderId": 9876,
            "status": "FILLED",
            "executedQty": "0.001",
            "avgPrice": "70000.0",
        })
        r.raise_for_status()  # no-op for 200
        return r

    monkeypatch.setattr("trading.binance_client.fetch_with_fallback", fake_fetch)

    out = bn_place_order(
        "testnet", "KEY", "SECRET",
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001,
    )
    assert out["orderId"] == 9876
    assert captured["method"] == "post"
    # Signature + key fingerprint must be on the request
    assert "/fapi/v1/order" in captured["url"]
    assert "&signature=" in captured["url"]
    assert "symbol=BTCUSDT" in captured["url"]
    assert "side=BUY" in captured["url"]
    assert "type=MARKET" in captured["url"]
    assert captured["headers"]["X-MBX-APIKEY"] == "KEY"


def test_place_order_limit_includes_price_and_tif(monkeypatch):
    captured = {}

    def fake_fetch(method, url, **kwargs):
        captured["url"] = url
        r = _resp(200, {"orderId": 1, "status": "NEW", "executedQty": "0"})
        r.raise_for_status()
        return r

    monkeypatch.setattr("trading.binance_client.fetch_with_fallback", fake_fetch)

    bn_place_order(
        "testnet", "K", "S",
        symbol="BTCUSDT", side="SELL", order_type="LIMIT",
        quantity=0.001, price=70000.0,
    )
    assert "type=LIMIT" in captured["url"]
    assert "price=70000.0" in captured["url"]
    assert "timeInForce=GTC" in captured["url"]


def test_set_margin_type_swallows_4046(monkeypatch):
    """The 'no need to change margin type' response should not surface as error."""
    err_resp = _resp(400, {
        "code": ERR_NO_NEED_TO_CHANGE_MARGIN_TYPE,
        "msg": "No need to change margin type.",
    })
    monkeypatch.setattr(
        "trading.binance_client.fetch_with_fallback",
        _fake_fetch_factory(err_resp),
    )
    assert bn_set_margin_type("testnet", "K", "S", "BTCUSDT", "ISOLATED") is None


def test_set_margin_type_propagates_other_errors(monkeypatch):
    err_resp = _resp(400, {"code": -2015, "msg": "Invalid API-key."})
    monkeypatch.setattr(
        "trading.binance_client.fetch_with_fallback",
        _fake_fetch_factory(err_resp),
    )
    with pytest.raises(BinanceAPIError) as exc:
        bn_set_margin_type("testnet", "K", "S", "BTCUSDT", "ISOLATED")
    assert exc.value.code == -2015


# ---------- service layer ----------

def test_service_place_order_calls_preflight_then_order(monkeypatch):
    """Verify the leverage/margin idempotent preflight runs before the order."""
    calls = []

    def fake_margin(env, k, s, sym, mt):
        calls.append(("margin", sym, mt))
        return {}

    def fake_lev(env, k, s, sym, lev):
        calls.append(("leverage", sym, lev))
        return {}

    def fake_order(env, k, s, **kw):
        calls.append(("order", kw["symbol"], kw["side"], kw["quantity"]))
        return {
            "orderId": 42, "status": "FILLED",
            "executedQty": "0.001", "avgPrice": "70000",
        }

    monkeypatch.setattr("trading.binance_client.set_margin_type", fake_margin)
    monkeypatch.setattr("trading.binance_client.set_leverage", fake_lev)
    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    # conftest resets the DB between tests, so no manual cleanup needed.
    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order(cred_id, OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.001, leverage=5, margin_type="ISOLATED",
    ))
    assert result.ok
    assert result.binance_order_id == "42"
    # Order of operations: margin -> leverage -> order
    assert [c[0] for c in calls] == ["margin", "leverage", "order"]


def test_service_place_order_records_failure_with_error(monkeypatch):
    """When place_order raises, service should return OrderResult(ok=False)
    and still persist a row to trading_orders."""
    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})

    def fake_order(env, k, s, **kw):
        raise BinanceAPIError(code=-2010, msg="Insufficient margin.", http_status=400)

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order(cred_id, OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001,
    ))
    assert not result.ok
    assert "Insufficient margin" in result.error
    # Persistence
    recent = service.list_recent_orders(limit=10)
    latest = recent[0]
    assert latest["status"] == "FAILED"
    assert latest["error_message"] is not None


def test_service_place_order_validates_request():
    cred_id = creds.add_credential("t", "testnet", "K", "S")
    # LIMIT without price -> validate() raises ValueError before any HTTP call
    with pytest.raises(ValueError, match="price"):
        service.place_order(cred_id, OrderRequest(
            symbol="BTCUSDT", side="BUY", order_type="LIMIT", quantity=0.001,
        ))


def test_credential_delete_preserves_order_history():
    """ON DELETE SET NULL: deleting a credential keeps orphan order rows."""
    cred_id = creds.add_credential("t", "testnet", "K", "S")
    # Insert one order row directly (bypassing the network)
    from database import get_db
    from config import settings
    db = get_db(settings.db_path)
    db.execute(
        "INSERT INTO trading_orders (credential_id, env, symbol, side, "
        "order_type, quantity, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cred_id, "testnet", "BTCUSDT", "BUY", "MARKET", 0.001, "FILLED"),
    )
    db.commit()
    db.close()

    # Deletion must succeed (FK is SET NULL, not RESTRICT)
    assert creds.delete_credential(cred_id) is True

    # Order row still exists with credential_id NULL
    orders = service.list_recent_orders()
    assert len(orders) == 1
    assert orders[0]["credential_id"] is None
    assert orders[0]["symbol"] == "BTCUSDT"


# ---------- HTTP API ----------

@pytest.mark.asyncio
async def test_api_credentials_create_and_list(client):
    # login first (auth dependency cookie check)
    login = await client.post("/api/auth/login", data={"password": "testpass"})
    assert login.status_code == 200

    add = await client.post("/api/trading/credentials", json={
        "label": "testnet-1",
        "env": "testnet",
        "api_key": "ABCDEFGHIJ123",
        "api_secret": "supersecret123",
    })
    assert add.status_code == 200, add.text
    cred_id = add.json()["id"]

    listed = await client.get("/api/trading/credentials")
    assert listed.status_code == 200
    rows = listed.json()["credentials"]
    assert any(r["id"] == cred_id for r in rows)
    # secret must never appear in any response
    assert "supersecret123" not in listed.text


@pytest.mark.asyncio
async def test_api_order_validation_rejects_limit_without_price(client):
    await client.post("/api/auth/login", data={"password": "testpass"})
    add = await client.post("/api/trading/credentials", json={
        "label": "t", "env": "testnet",
        "api_key": "AAAAAAAAAA", "api_secret": "BBBBBBBBBB",
    })
    cred_id = add.json()["id"]

    bad = await client.post("/api/trading/order", json={
        "credential_id": cred_id,
        "symbol": "BTCUSDT",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 0.001,
        # price missing
    })
    # Pydantic-level: price is Optional but the service-level validate catches it
    # Either we get a 400 from service or 200 with ok=false; both acceptable.
    if bad.status_code == 200:
        assert bad.json()["ok"] is False
    else:
        assert bad.status_code in (400, 422)
