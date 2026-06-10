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
    get_order as bn_get_order,
    cancel_order as bn_cancel_order,
    is_retryable,
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


def test_service_place_order_continues_when_margin_type_fails(monkeypatch):
    """set_margin_type failure must NOT abort the order — margin type is a
    best-effort preference. The order proceeds and a warning is attached."""
    def fail_margin(*a, **kw):
        raise BinanceAPIError(code=-1109, msg="Invalid account.", http_status=400)

    def fake_order(env, k, s, **kw):
        return {"orderId": 7, "status": "FILLED",
                "executedQty": "0.001", "avgPrice": "70000"}

    monkeypatch.setattr("trading.binance_client.set_margin_type", fail_margin)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})
    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order(cred_id, OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.001, leverage=5, margin_type="ISOLATED",
    ))
    assert result.ok
    assert result.binance_order_id == "7"
    assert result.warning is not None and "保证金模式" in result.warning


def test_service_place_order_aborts_when_leverage_fails(monkeypatch):
    """set_leverage failure DOES abort — leverage affects margin/liquidation."""
    def fail_lev(*a, **kw):
        raise BinanceAPIError(code=-1109, msg="Invalid account.", http_status=400)

    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", fail_lev)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order(cred_id, OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.001, leverage=5, margin_type="ISOLATED",
    ))
    assert not result.ok
    assert "set_leverage" in result.error


def test_service_place_order_sends_client_order_id(monkeypatch):
    captured = {}
    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})

    def fake_order(env, k, s, **kw):
        captured.update(kw)
        return {"orderId": 1, "status": "FILLED", "executedQty": "0.001", "avgPrice": "70000"}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    service.place_order(cred_id, OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001))
    oid = captured["new_client_order_id"]
    assert oid.startswith("wohub-") and len(oid) <= 36


def test_service_place_order_network_error_resolved_as_placed(monkeypatch):
    """Transport died after send, but Binance accepted the order — get_order
    finds it by clientOrderId and the result is success, not a blind failure."""
    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})
    monkeypatch.setattr(
        "trading.binance_client.place_order",
        lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError("reset")))

    def fake_get_order(env, k, s, symbol, order_id=None, orig_client_order_id=None):
        assert orig_client_order_id.startswith("wohub-")
        return {"orderId": 55, "status": "FILLED", "executedQty": "0.001", "avgPrice": "70000"}

    monkeypatch.setattr("trading.binance_client.get_order", fake_get_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order(cred_id, OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001))
    assert result.ok
    assert result.binance_order_id == "55"


def test_service_place_order_network_error_order_absent_fails_clean(monkeypatch):
    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})
    monkeypatch.setattr(
        "trading.binance_client.place_order",
        lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError("reset")))
    monkeypatch.setattr(
        "trading.binance_client.get_order",
        lambda *a, **kw: (_ for _ in ()).throw(
            BinanceAPIError(code=-2013, msg="Order does not exist.", http_status=400)))

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order(cred_id, OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001))
    assert not result.ok
    assert "未送达" in result.error
    # failure persisted to the audit log
    assert service.list_recent_orders(limit=1)[0]["status"] == "FAILED"


def test_service_place_order_network_error_unknown_state(monkeypatch):
    """Both the order and the verification query failed — error must say the
    state is unknown so the user checks positions immediately."""
    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})
    monkeypatch.setattr(
        "trading.binance_client.place_order",
        lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError("reset")))
    monkeypatch.setattr(
        "trading.binance_client.get_order",
        lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout("query timed out")))

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order(cred_id, OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001))
    assert not result.ok
    assert "状态未知" in result.error


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


# ---------- bracket order ----------

def test_bracket_order_with_sl_and_tp_places_three_orders(monkeypatch):
    """A successful bracket = entry + SL + TP, all recorded."""
    calls = []

    def fake_margin(*a, **kw):
        calls.append(("margin",))
        return {}

    def fake_lev(*a, **kw):
        calls.append(("leverage",))
        return {}

    def fake_order(env, k, s, **kw):
        calls.append((kw["order_type"], kw.get("side"),
                      kw.get("stop_price"), kw.get("close_position")))
        return {"orderId": 100 + len(calls), "status": "NEW"}

    monkeypatch.setattr("trading.binance_client.set_margin_type", fake_margin)
    monkeypatch.setattr("trading.binance_client.set_leverage", fake_lev)
    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    req = OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET",
        quantity=0.001, leverage=10, margin_type="ISOLATED",
    )
    result = service.place_order_bracket(
        cred_id, req,
        stop_loss_price=65000.0,
        take_profit_price=80000.0,
    )

    assert result.ok
    assert result.entry.ok and result.entry.binance_order_id
    assert result.stop_loss.ok and result.stop_loss.binance_order_id
    assert result.take_profit.ok and result.take_profit.binance_order_id

    # Verify the call sequence and details
    types = [c[0] for c in calls if isinstance(c, tuple) and len(c) > 1]
    assert "MARKET" in types
    assert "STOP_MARKET" in types
    assert "TAKE_PROFIT_MARKET" in types

    # SL/TP should be SELL side (opposite of BUY entry) with closePosition=True
    sl_call = next(c for c in calls if isinstance(c, tuple) and len(c) == 4 and c[0] == "STOP_MARKET")
    tp_call = next(c for c in calls if isinstance(c, tuple) and len(c) == 4 and c[0] == "TAKE_PROFIT_MARKET")
    assert sl_call[1] == "SELL"
    assert sl_call[2] == 65000.0
    assert sl_call[3] is True
    assert tp_call[1] == "SELL"
    assert tp_call[2] == 80000.0
    assert tp_call[3] is True

    # 3 orders persisted in audit log
    orders = service.list_recent_orders(limit=10)
    assert len(orders) == 3


def test_bracket_short_sell_uses_buy_for_protection_orders(monkeypatch):
    """A SHORT entry must have BUY-side SL/TP to close the position."""
    captured = []

    def fake_order(env, k, s, **kw):
        captured.append(kw.get("side"))
        return {"orderId": 1, "status": "NEW"}

    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})
    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="SELL", order_type="MARKET", quantity=0.001),
        stop_loss_price=80000.0,
        take_profit_price=65000.0,
    )
    # Entry is SELL; SL and TP must be BUY
    assert captured[0] == "SELL"   # entry
    assert captured[1] == "BUY"    # SL
    assert captured[2] == "BUY"    # TP


def test_bracket_without_sl_tp_only_places_entry(monkeypatch):
    """Bracket without SL/TP is just the entry."""
    calls = []
    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})

    def fake_order(env, k, s, **kw):
        calls.append(kw.get("order_type"))
        return {"orderId": 1, "status": "FILLED"}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001),
    )
    assert result.ok
    assert result.entry.ok
    assert result.stop_loss is None
    assert result.take_profit is None
    assert calls == ["MARKET"]   # only the entry


def test_bracket_entry_failure_skips_sl_tp(monkeypatch):
    """If the entry fails, no SL/TP attempt is made."""
    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})
    calls = []

    def fake_order(env, k, s, **kw):
        calls.append(kw.get("order_type"))
        raise BinanceAPIError(code=-2010, msg="Insufficient margin.", http_status=400)

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001),
        stop_loss_price=65000.0,
        take_profit_price=80000.0,
    )
    assert not result.ok
    assert not result.entry.ok
    assert result.stop_loss is None
    assert result.take_profit is None
    # Only the entry was attempted
    assert calls == ["MARKET"]


def test_bracket_partial_failure_reports_sl_error_but_keeps_entry(monkeypatch):
    """Entry succeeds, SL fails — entry should NOT be undone."""
    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})

    state = {"count": 0}

    def fake_order(env, k, s, **kw):
        state["count"] += 1
        # 1st call (MARKET entry) succeeds; 2nd (STOP_MARKET) fails; 3rd (TP) succeeds
        if kw.get("order_type") == "STOP_MARKET":
            raise BinanceAPIError(code=-1111, msg="Precision over the maximum.", http_status=400)
        return {"orderId": state["count"], "status": "NEW"}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001),
        stop_loss_price=65000.0,
        take_profit_price=80000.0,
    )
    assert not result.ok                # overall failure
    assert result.entry.ok              # but entry succeeded
    assert result.stop_loss and not result.stop_loss.ok
    assert "Precision" in result.stop_loss.error
    assert result.take_profit and result.take_profit.ok  # TP attempt still made


# ---------- close_position ----------

def test_close_position_long_places_opposite_market_sell(monkeypatch):
    """A long position is closed with a reduce-only SELL market order."""
    captured = {}

    def fake_pos(env, k, s, symbol=None):
        return [{"symbol": "BTCUSDT", "positionAmt": "0.005", "entryPrice": "70000"}]

    def fake_order(env, k, s, **kw):
        captured.update(kw)
        return {"orderId": 999, "status": "FILLED", "executedQty": "0.005", "avgPrice": "71000"}

    monkeypatch.setattr("trading.binance_client.position_risk", fake_pos)
    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.close_position(cred_id, "BTCUSDT")
    assert result.ok
    assert result.binance_order_id == "999"
    assert captured["side"] == "SELL"
    assert captured["quantity"] == 0.005
    assert captured["reduce_only"] is True
    assert captured["order_type"] == "MARKET"


def test_close_position_short_places_opposite_market_buy(monkeypatch):
    def fake_pos(env, k, s, symbol=None):
        return [{"symbol": "BTCUSDT", "positionAmt": "-0.003", "entryPrice": "70000"}]

    captured = {}
    def fake_order(env, k, s, **kw):
        captured.update(kw)
        return {"orderId": 1, "status": "FILLED"}

    monkeypatch.setattr("trading.binance_client.position_risk", fake_pos)
    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    service.close_position(cred_id, "BTCUSDT")
    assert captured["side"] == "BUY"
    assert captured["quantity"] == 0.003


def test_close_position_returns_error_when_no_position(monkeypatch):
    def fake_pos(env, k, s, symbol=None):
        # Binance returns a row even when no position — positionAmt is "0.000"
        return [{"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0"}]

    monkeypatch.setattr("trading.binance_client.position_risk", fake_pos)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.close_position(cred_id, "BTCUSDT")
    assert not result.ok
    assert "no open position" in result.error


# ---------- open orders / cancel ----------

def test_list_open_orders_passes_uppercase_symbol(monkeypatch):
    captured = {}

    def fake(env, k, s, symbol=None):
        captured["symbol"] = symbol
        return [{"orderId": 1, "symbol": symbol, "status": "NEW"}]

    monkeypatch.setattr("trading.binance_client.open_orders", fake)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    rows = service.list_open_orders(cred_id, symbol="btcusdt")
    assert captured["symbol"] == "BTCUSDT"
    assert len(rows) == 1


def test_cancel_open_order(monkeypatch):
    captured = {}

    def fake(env, k, s, symbol, order_id):
        captured["symbol"] = symbol
        captured["order_id"] = order_id
        return {"orderId": order_id, "status": "CANCELED"}

    monkeypatch.setattr("trading.binance_client.cancel_order", fake)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    res = service.cancel_open_order(cred_id, "btcusdt", 12345)
    assert captured["symbol"] == "BTCUSDT"
    assert captured["order_id"] == 12345
    assert res["status"] == "CANCELED"


# ---------- binance_client place_order extended types ----------

def test_place_order_stop_market_with_close_position(monkeypatch):
    """STOP_MARKET + close_position=True omits quantity per Binance docs."""
    from trading.binance_client import place_order as bn_place

    captured = {}

    def fake_fetch(method, url, **kwargs):
        captured["url"] = url
        r = _resp(200, {"orderId": 1, "status": "NEW"})
        r.raise_for_status()
        return r

    monkeypatch.setattr("trading.binance_client.fetch_with_fallback", fake_fetch)

    bn_place(
        "testnet", "K", "S",
        symbol="BTCUSDT", side="SELL", order_type="STOP_MARKET",
        stop_price=65000.0, close_position=True,
    )
    assert "type=STOP_MARKET" in captured["url"]
    assert "stopPrice=65000.0" in captured["url"]
    assert "closePosition=true" in captured["url"]
    # quantity must NOT be in the request (Binance forbids it with closePosition)
    assert "quantity=" not in captured["url"]


def test_place_order_stop_market_requires_stop_price():
    from trading.binance_client import place_order as bn_place
    with pytest.raises(ValueError, match="stop_price"):
        bn_place(
            "testnet", "K", "S",
            symbol="BTCUSDT", side="SELL", order_type="STOP_MARKET",
            close_position=True,
        )


def test_place_order_passes_new_client_order_id(monkeypatch):
    captured = {}

    def fake_fetch(method, url, **kwargs):
        captured["url"] = url
        r = _resp(200, {"orderId": 1, "status": "NEW"})
        r.raise_for_status()
        return r

    monkeypatch.setattr("trading.binance_client.fetch_with_fallback", fake_fetch)

    bn_place_order(
        "testnet", "K", "S",
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001,
        new_client_order_id="wohub-abc123",
    )
    assert "newClientOrderId=wohub-abc123" in captured["url"]


def test_get_order_queries_by_client_order_id(monkeypatch):
    captured = {}

    def fake_fetch(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        r = _resp(200, {"orderId": 7, "status": "NEW", "executedQty": "0"})
        r.raise_for_status()
        return r

    monkeypatch.setattr("trading.binance_client.fetch_with_fallback", fake_fetch)

    out = bn_get_order("testnet", "K", "S", "BTCUSDT", orig_client_order_id="wohub-x")
    assert out["orderId"] == 7
    assert captured["method"] == "get"
    assert "/fapi/v1/order" in captured["url"]
    assert "origClientOrderId=wohub-x" in captured["url"]
    assert "&signature=" in captured["url"]


def test_get_order_requires_an_identifier():
    with pytest.raises(ValueError):
        bn_get_order("testnet", "K", "S", "BTCUSDT")


def test_cancel_order_by_client_order_id(monkeypatch):
    captured = {}

    def fake_fetch(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        r = _resp(200, {"orderId": 3, "status": "CANCELED"})
        r.raise_for_status()
        return r

    monkeypatch.setattr("trading.binance_client.fetch_with_fallback", fake_fetch)

    bn_cancel_order("testnet", "K", "S", "BTCUSDT", orig_client_order_id="wohub-y")
    assert captured["method"] == "delete"
    assert "origClientOrderId=wohub-y" in captured["url"]


def test_cancel_order_requires_an_identifier():
    with pytest.raises(ValueError):
        bn_cancel_order("testnet", "K", "S", "BTCUSDT")


def test_bracket_result_to_dict_includes_recovery():
    from trading.models import RecoveryResult, BracketOrderResult
    entry = OrderResult(ok=True, binance_order_id="1")
    rec = RecoveryResult(
        attempted=True, entry_cancel=None,
        close=OrderResult(ok=True, binance_order_id="2"),
        naked_position=False, detail="已平仓",
    )
    d = BracketOrderResult(ok=False, entry=entry, recovery=rec).to_dict()
    assert d["recovery"]["attempted"] is True
    assert d["recovery"]["naked_position"] is False
    assert d["recovery"]["close"]["binance_order_id"] == "2"
    assert d["recovery"]["entry_cancel"] is None
    assert d["recovery"]["detail"] == "已平仓"


def test_bracket_result_to_dict_recovery_none_by_default():
    from trading.models import BracketOrderResult
    d = BracketOrderResult(ok=True, entry=OrderResult(ok=True)).to_dict()
    assert d["recovery"] is None


def test_is_retryable_classification():
    # transient server / rate-limit conditions -> retryable
    assert is_retryable(BinanceAPIError(code=-1001, msg="", http_status=500))
    assert is_retryable(BinanceAPIError(code=-1003, msg="", http_status=429))
    assert is_retryable(BinanceAPIError(code=-1007, msg="", http_status=408))
    assert is_retryable(BinanceAPIError(code=-1021, msg="", http_status=400))
    assert is_retryable(BinanceAPIError(code=0, msg="", http_status=502))
    assert is_retryable(BinanceAPIError(code=0, msg="", http_status=418))
    # business rejections -> fatal (retry storms are pointless and dangerous)
    assert not is_retryable(BinanceAPIError(code=-2021, msg="would trigger", http_status=400))
    assert not is_retryable(BinanceAPIError(code=-1111, msg="precision", http_status=400))
    assert not is_retryable(BinanceAPIError(code=-2019, msg="margin", http_status=400))


# ---------- HTTP API ----------

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
