# Bracket SL Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No live position may exist whose requested stop-loss could not be placed — bracket orders verify, retry, and undo the entry when the SL is definitively rejected.

**Architecture:** All orders we submit carry a generated `clientOrderId` so ambiguous network failures can be resolved by querying Binance instead of blind resubmission. SL/TP placement gets bounded retries with fatal/transient classification; a definitively-failed SL triggers `_undo_entry` (cancel unfilled remainder + reduce-only market close of the filled quantity). A new `RecoveryResult` rides on `BracketOrderResult` so API and frontend can show exactly what happened.

**Tech Stack:** Python 3.11 / FastAPI backend, pytest (existing `backend/tests/test_trading.py` conventions: conftest resets DB, `monkeypatch.setattr("trading.binance_client.X", ...)`), Vue 3 frontend.

**Spec:** `docs/superpowers/specs/2026-06-10-bracket-sl-recovery-design.md`

**Run all test commands from `backend/`.**

---

### Task 1: Real HTTP timeouts (http_client + pine_screener)

`requests.Session` has no `timeout` attribute — `s.timeout = 10` lines are inert; every outbound call currently has NO timeout.

**Files:**
- Modify: `backend/sources/http_client.py`
- Modify: `backend/sources/pine_screener.py:94` (inert line), `:193` (post), `:270` (get)
- Test: `backend/tests/test_http_client.py` (append)

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_http_client.py`:

```python
def test_fetch_with_fallback_sets_default_timeout(monkeypatch):
    """Session objects have no working .timeout attr — a per-request timeout
    must be injected or a hung connection blocks the worker forever."""
    from sources import http_client
    captured = {}

    class _FakeSession:
        def get(self, url, **kwargs):
            captured.update(kwargs)

            class _R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {}
            return _R()

    monkeypatch.setattr(http_client, "get_session", lambda: _FakeSession())
    http_client.fetch_with_fallback("get", "https://example.com")
    assert captured["timeout"] == 10


def test_fetch_with_fallback_respects_caller_timeout(monkeypatch):
    from sources import http_client
    captured = {}

    class _FakeSession:
        def get(self, url, **kwargs):
            captured.update(kwargs)

            class _R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {}
            return _R()

    monkeypatch.setattr(http_client, "get_session", lambda: _FakeSession())
    http_client.fetch_with_fallback("get", "https://example.com", timeout=3)
    assert captured["timeout"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_http_client.py -v -k timeout`
Expected: FAIL — `KeyError: 'timeout'`

- [ ] **Step 3: Implement** — in `backend/sources/http_client.py`:

In `fetch_with_fallback`, add as the first line of the body (and extend the docstring):

```python
def fetch_with_fallback(method, url, **kwargs):
    """Try with proxy session first. If proxy fails, retry with direct connection.

    requests.Session has no timeout attribute, so a per-request timeout is
    injected here — otherwise a hung connection blocks the worker forever.
    Callers may override with an explicit timeout= kwarg.
    """
    kwargs.setdefault("timeout", 10)
```

Delete the two inert `s.timeout = 10` lines (in `get_session` and `_get_direct_session`).

In `backend/sources/pine_screener.py`: delete inert line 94 (`_session.timeout = 15`); add `timeout=15` to the two real calls:

```python
            resp = session.post(API_URL, data=request_data, cookies=cookies, timeout=15)
```

```python
    resp = session.get(
        "https://www.tradingview.com/api/v1/symbols_list/all/",
        headers=headers,
        cookies=cookies,
        timeout=15,
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_http_client.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add sources/http_client.py sources/pine_screener.py tests/test_http_client.py
git commit -m "fix(http): per-request timeouts — Session.timeout attribute is inert"
```

---

### Task 2: `binance_client.place_order` accepts `new_client_order_id`

**Files:**
- Modify: `backend/trading/binance_client.py:190-250` (place_order)
- Test: `backend/tests/test_trading.py` (append to "binance_client place_order extended types" section)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading.py::test_place_order_passes_new_client_order_id -v`
Expected: FAIL — `TypeError: place_order() got an unexpected keyword argument`

- [ ] **Step 3: Implement** — add parameter `new_client_order_id: str | None = None` to `place_order` (after `time_in_force`), and right after the initial `params` dict is built:

```python
    if new_client_order_id:
        # Caller-supplied idempotency key. Binance rejects a duplicate id while
        # the first order is live, so a transport-level resend (proxy->direct
        # fallback in fetch_with_fallback) can never double-fill.
        params["newClientOrderId"] = new_client_order_id
```

Also extend the docstring's parameter notes accordingly.

- [ ] **Step 4: Run test**

Run: `python -m pytest tests/test_trading.py::test_place_order_passes_new_client_order_id -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading/binance_client.py tests/test_trading.py
git commit -m "feat(trading): place_order supports newClientOrderId idempotency key"
```

---

### Task 3: `binance_client.get_order`

**Files:**
- Modify: `backend/trading/binance_client.py` (new function after `place_order`; new constant near the other ERR_ constants)
- Test: `backend/tests/test_trading.py`

- [ ] **Step 1: Write the failing tests** — also extend the existing `from trading.binance_client import (...)` at the top of the test file with `get_order as bn_get_order` ONLY. (Do NOT import `cancel_order`/`is_retryable` yet — they are added in Tasks 4-5; importing symbols that don't exist yet breaks the whole test module with ImportError. Each task adds exactly the imports it needs.)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trading.py -v -k get_order`
Expected: FAIL — ImportError (`get_order` does not exist)

- [ ] **Step 3: Implement** — in `binance_client.py`, next to the other error-code constants:

```python
ERR_ORDER_NOT_FOUND = -2013  # GET/DELETE /fapi/v1/order when the order does not exist
```

After `place_order`:

```python
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
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_trading.py -v -k get_order`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading/binance_client.py tests/test_trading.py
git commit -m "feat(trading): get_order single-order lookup (ambiguity resolution)"
```

---

### Task 4: `cancel_order` accepts `orig_client_order_id`

**Files:**
- Modify: `backend/trading/binance_client.py:253-258` (cancel_order)
- Test: `backend/tests/test_trading.py`

- [ ] **Step 1: Write the failing tests** — extend the binance_client import in the test file with `cancel_order as bn_cancel_order`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trading.py -v -k cancel_order_by_client`
Expected: FAIL — `TypeError: cancel_order() got an unexpected keyword argument`

- [ ] **Step 3: Implement** — replace `cancel_order` (signature stays positional-compatible: existing caller `service.cancel_open_order` passes `order_id` as 5th positional arg):

```python
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
```

- [ ] **Step 4: Run tests** (includes the pre-existing `test_cancel_open_order` which must still pass)

Run: `python -m pytest tests/test_trading.py -v -k cancel`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add trading/binance_client.py tests/test_trading.py
git commit -m "feat(trading): cancel_order by origClientOrderId"
```

---

### Task 5: `is_retryable` error classification

**Files:**
- Modify: `backend/trading/binance_client.py` (after the BinanceAPIError class)
- Test: `backend/tests/test_trading.py`

- [ ] **Step 1: Write the failing test** — extend the binance_client import in the test file with `is_retryable`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trading.py::test_is_retryable_classification -v`
Expected: FAIL — ImportError

- [ ] **Step 3: Implement** — in `binance_client.py` after the `BinanceAPIError` class:

```python
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
```

- [ ] **Step 4: Run test**

Run: `python -m pytest tests/test_trading.py::test_is_retryable_classification -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading/binance_client.py tests/test_trading.py
git commit -m "feat(trading): is_retryable error classification (default-fatal)"
```

---

### Task 6: models — `RecoveryResult` + `BracketOrderResult.recovery`

**Files:**
- Modify: `backend/trading/models.py`
- Test: `backend/tests/test_trading.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trading.py -v -k recovery`
Expected: FAIL — ImportError (`RecoveryResult`)

- [ ] **Step 3: Implement** — in `models.py`, before `BracketOrderResult`:

```python
@dataclass
class RecoveryResult:
    """What the service did after a stop-loss could not be placed.

    以损定仓：a position whose stop cannot be placed must not exist. The
    service cancels the unfilled remainder of the entry and market-closes the
    filled quantity. naked_position=True means that undo could not be
    completed — an unprotected position may remain and the user must act."""
    attempted: bool
    entry_cancel: OrderResult | None
    close: OrderResult | None
    naked_position: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "entry_cancel": self.entry_cancel.to_dict() if self.entry_cancel else None,
            "close": self.close.to_dict() if self.close else None,
            "naked_position": self.naked_position,
            "detail": self.detail,
        }
```

In `BracketOrderResult`: add field `recovery: RecoveryResult | None = None`, add `"recovery": self.recovery.to_dict() if self.recovery else None,` to `to_dict`, and update the class docstring (SL is no longer best-effort; a failed SL triggers undo — point to the spec).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_trading.py -v -k recovery`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading/models.py tests/test_trading.py
git commit -m "feat(trading): RecoveryResult model on BracketOrderResult"
```

---

### Task 7: service — clientOrderId on entry + ambiguous-entry resolution

**Files:**
- Modify: `backend/trading/service.py` (imports, new helpers, `place_order`)
- Test: `backend/tests/test_trading.py`

- [ ] **Step 1: Write the failing tests** — `import requests` is already imported in the test file:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trading.py -v -k "client_order_id or network_error"`
Expected: 4 FAIL (KeyError / unhandled ConnectionError)

- [ ] **Step 3: Implement** — in `service.py`:

Add imports at the top: `import secrets`, `import time`, `import requests` (keep stdlib/3rd-party grouping consistent with the existing file).

Add helpers after `_record_order`:

```python
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
```

In `place_order`, generate the id before the order call and extend the error handling. Replace the `# ---- place the actual order ----` block with:

```python
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
```

(The success-result construction below the try block stays unchanged — `raw` now comes from either path.)

- [ ] **Step 4: Run the trading test module**

Run: `python -m pytest tests/test_trading.py -v`
Expected: ALL PASS (pre-existing tests use `**kw` fakes, unaffected by the new kwarg)

- [ ] **Step 5: Commit**

```bash
git add trading/service.py tests/test_trading.py
git commit -m "feat(trading): clientOrderId on entries; resolve ambiguous network failures via get_order"
```

---

### Task 8: service — `_place_protection_with_retry`

**Files:**
- Modify: `backend/trading/service.py` (new constants + helper, after `_opposite_side`)
- Test: `backend/tests/test_trading.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_protection_retry_transient_then_success(monkeypatch):
    calls = {"n": 0}

    def fake_order(env, k, s, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BinanceAPIError(code=-1001, msg="Internal error.", http_status=500)
        return {"orderId": 9, "status": "NEW"}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)
    monkeypatch.setattr(service, "_sleep", lambda s: None)

    res = service._place_protection_with_retry(
        "testnet", "K", "S", symbol="BTCUSDT", side="SELL",
        order_type="STOP_MARKET", stop_price=65000.0, client_oid="wohub-t1")
    assert res.ok
    assert calls["n"] == 2


def test_protection_retry_fatal_fails_immediately(monkeypatch):
    calls = {"n": 0}

    def fake_order(env, k, s, **kw):
        calls["n"] += 1
        raise BinanceAPIError(code=-2021, msg="Order would immediately trigger.", http_status=400)

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)
    monkeypatch.setattr(service, "_sleep", lambda s: None)

    res = service._place_protection_with_retry(
        "testnet", "K", "S", symbol="BTCUSDT", side="SELL",
        order_type="STOP_MARKET", stop_price=65000.0, client_oid="wohub-t2")
    assert not res.ok
    assert calls["n"] == 1          # no retry on fatal codes
    assert "immediately trigger" in res.error


def test_protection_retry_ambiguous_verified_exists(monkeypatch):
    """Network error on submit, but get_order finds the trigger order —
    success without a second (duplicate) submission."""
    placed = {"n": 0}

    def fake_order(env, k, s, **kw):
        placed["n"] += 1
        raise requests.ConnectionError("reset mid-flight")

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)
    monkeypatch.setattr(
        "trading.binance_client.get_order",
        lambda env, k, s, symbol, order_id=None, orig_client_order_id=None:
            {"orderId": 77, "status": "NEW"})
    monkeypatch.setattr(service, "_sleep", lambda s: None)

    res = service._place_protection_with_retry(
        "testnet", "K", "S", symbol="BTCUSDT", side="SELL",
        order_type="STOP_MARKET", stop_price=65000.0, client_oid="wohub-t3")
    assert res.ok
    assert res.binance_order_id == "77"
    assert placed["n"] == 1


def test_protection_retry_reuses_same_client_oid(monkeypatch):
    oids = []

    def fake_order(env, k, s, **kw):
        oids.append(kw.get("new_client_order_id"))
        raise BinanceAPIError(code=-1001, msg="Internal error.", http_status=500)

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)
    monkeypatch.setattr(service, "_sleep", lambda s: None)

    res = service._place_protection_with_retry(
        "testnet", "K", "S", symbol="BTCUSDT", side="SELL",
        order_type="STOP_MARKET", stop_price=65000.0, client_oid="wohub-t4")
    assert not res.ok
    assert len(oids) == 3                      # PROTECTION_ATTEMPTS
    assert set(oids) == {"wohub-t4"}           # identical id every attempt
    assert "重试" in res.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trading.py -v -k protection_retry`
Expected: FAIL — AttributeError (`_sleep` / `_place_protection_with_retry` missing)

- [ ] **Step 3: Implement** — in `service.py` after `_opposite_side`:

```python
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
            if not bn.is_retryable(e):
                return OrderResult(ok=False, error=f"{order_type}: {e}")
        except requests.RequestException as e:
            last_err = e
            state, raw = _query_order_state(env, api_key, secret, symbol, client_oid)
            if state == "exists":
                return OrderResult(
                    ok=True, binance_order_id=str(raw.get("orderId", "")),
                    status=raw.get("status"), raw=raw,
                )
            if state == "unknown":
                return OrderResult(
                    ok=False, error=f"{order_type}: 网络异常且订单状态未知：{e}")
            # state == "absent": never reached Binance; safe to retry same id.
        if attempt < PROTECTION_ATTEMPTS - 1:
            _sleep(PROTECTION_BACKOFF_S[min(attempt, len(PROTECTION_BACKOFF_S) - 1)])
    return OrderResult(
        ok=False,
        error=f"{order_type}: 重试{PROTECTION_ATTEMPTS}次后仍失败：{last_err}")
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_trading.py -v -k protection_retry`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add trading/service.py tests/test_trading.py
git commit -m "feat(trading): bounded-retry protection placement with idempotent client id"
```

---

### Task 9: service — `_undo_entry` + bracket rewiring

**Files:**
- Modify: `backend/trading/service.py` (`place_order_bracket` rewritten; new `_undo_entry`, `_close_entry_qty`; import `RecoveryResult`)
- Modify: `backend/tests/test_trading.py` — **delete** `test_bracket_partial_failure_reports_sl_error_but_keeps_entry` (its "entry should NOT be undone" semantics is exactly what this feature removes; superseded by the tests below)
- Test: `backend/tests/test_trading.py`

- [ ] **Step 1: Delete the superseded test, add a `_raise` helper + new tests**

Add near the top of the bracket-test section:

```python
def _raise(exc):
    def _f(*a, **kw):
        raise exc
    return _f


def _stub_preflight(monkeypatch):
    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})
    monkeypatch.setattr(service, "_sleep", lambda s: None)
```

New tests:

```python
def test_bracket_sl_fatal_failure_undoes_entry(monkeypatch):
    """SL rejected fatally -> TP never attempted, filled entry closed at market."""
    _stub_preflight(monkeypatch)
    orders = []

    def fake_order(env, k, s, **kw):
        orders.append(kw)
        if kw.get("order_type") == "STOP_MARKET":
            raise BinanceAPIError(code=-2021, msg="Order would immediately trigger.",
                                  http_status=400)
        return {"orderId": len(orders), "status": "FILLED",
                "executedQty": str(kw.get("quantity", 0)), "avgPrice": "70000"}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)
    # undo step 1 probes for an orphaned SL by client id — nothing there:
    monkeypatch.setattr("trading.binance_client.cancel_order", _raise(
        BinanceAPIError(code=-2013, msg="Order does not exist.", http_status=400)))
    monkeypatch.setattr("trading.binance_client.position_risk",
                        lambda *a, **kw: [{"symbol": "BTCUSDT", "positionAmt": "0.001"}])

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001),
        stop_loss_price=65000.0, take_profit_price=80000.0)

    assert not result.ok
    assert result.entry.ok
    assert result.stop_loss and not result.stop_loss.ok
    assert result.take_profit is None                       # TP skipped entirely
    assert result.recovery and result.recovery.attempted
    assert result.recovery.close and result.recovery.close.ok
    assert result.recovery.naked_position is False
    # the recovery close is a reduce-only MARKET opposite the entry
    close_call = orders[-1]
    assert close_call["order_type"] == "MARKET"
    assert close_call["side"] == "SELL"
    assert close_call["reduce_only"] is True
    assert close_call["quantity"] == 0.001


def test_bracket_sl_transient_then_success_no_recovery(monkeypatch):
    _stub_preflight(monkeypatch)
    state = {"sl_calls": 0}

    def fake_order(env, k, s, **kw):
        if kw.get("order_type") == "STOP_MARKET":
            state["sl_calls"] += 1
            if state["sl_calls"] == 1:
                raise BinanceAPIError(code=-1001, msg="Internal error.", http_status=500)
        return {"orderId": 1, "status": "NEW", "executedQty": "0.001"}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001),
        stop_loss_price=65000.0, take_profit_price=80000.0)
    assert result.ok
    assert result.recovery is None
    assert state["sl_calls"] == 2


def test_bracket_sl_failure_and_close_failure_flags_naked(monkeypatch):
    """SL fatal AND the recovery close rejected -> naked_position=True."""
    _stub_preflight(monkeypatch)

    def fake_order(env, k, s, **kw):
        if kw.get("order_type") == "STOP_MARKET":
            raise BinanceAPIError(code=-2021, msg="Order would immediately trigger.",
                                  http_status=400)
        if kw.get("reduce_only"):
            raise BinanceAPIError(code=-2019, msg="Margin is insufficient.", http_status=400)
        return {"orderId": 1, "status": "FILLED", "executedQty": "0.001", "avgPrice": "70000"}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)
    monkeypatch.setattr("trading.binance_client.cancel_order", _raise(
        BinanceAPIError(code=-2013, msg="Order does not exist.", http_status=400)))
    monkeypatch.setattr("trading.binance_client.position_risk",
                        lambda *a, **kw: [{"symbol": "BTCUSDT", "positionAmt": "0.001"}])

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001),
        stop_loss_price=65000.0)
    assert not result.ok
    assert result.recovery and result.recovery.naked_position is True
    assert result.recovery.close and not result.recovery.close.ok
    assert "平仓失败" in result.recovery.detail


def test_bracket_limit_unfilled_sl_failure_cancels_entry(monkeypatch):
    """Unfilled LIMIT entry: undo cancels the order; nothing to close."""
    _stub_preflight(monkeypatch)
    cancels = []

    def fake_order(env, k, s, **kw):
        if kw.get("order_type") == "STOP_MARKET":
            raise BinanceAPIError(code=-1111, msg="Precision over the maximum.",
                                  http_status=400)
        return {"orderId": 10, "status": "NEW", "executedQty": "0"}

    def fake_cancel(env, k, s, symbol, order_id=None, orig_client_order_id=None):
        cancels.append({"order_id": order_id, "orig": orig_client_order_id})
        if orig_client_order_id is not None:  # the orphan-SL probe finds nothing
            raise BinanceAPIError(code=-2013, msg="Order does not exist.", http_status=400)
        return {"orderId": order_id, "status": "CANCELED", "executedQty": "0"}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)
    monkeypatch.setattr("trading.binance_client.cancel_order", fake_cancel)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="BUY", order_type="LIMIT",
                     quantity=0.001, price=60000.0),
        stop_loss_price=55000.0)
    assert not result.ok
    assert result.recovery and result.recovery.attempted
    assert result.recovery.entry_cancel and result.recovery.entry_cancel.ok
    assert result.recovery.close is None             # nothing filled, nothing closed
    assert result.recovery.naked_position is False
    assert any(c["order_id"] == "10" for c in cancels)


def test_bracket_undo_closes_only_entry_qty(monkeypatch):
    """Pre-existing same-direction position: undo closes only what the entry
    filled (min with live positionAmt), never the user's prior exposure."""
    _stub_preflight(monkeypatch)
    closes = []

    def fake_order(env, k, s, **kw):
        if kw.get("order_type") == "STOP_MARKET":
            raise BinanceAPIError(code=-2021, msg="Order would immediately trigger.",
                                  http_status=400)
        if kw.get("reduce_only"):
            closes.append(kw)
        return {"orderId": 1, "status": "FILLED",
                "executedQty": str(kw.get("quantity", 0)), "avgPrice": "70000"}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)
    monkeypatch.setattr("trading.binance_client.cancel_order", _raise(
        BinanceAPIError(code=-2013, msg="Order does not exist.", http_status=400)))
    # 1.0 pre-existing + 0.3 entry = 1.3 live
    monkeypatch.setattr("trading.binance_client.position_risk",
                        lambda *a, **kw: [{"symbol": "BTCUSDT", "positionAmt": "1.3"}])

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.3),
        stop_loss_price=65000.0)
    assert result.recovery and result.recovery.close and result.recovery.close.ok
    assert len(closes) == 1
    assert closes[0]["quantity"] == 0.3


def test_bracket_tp_failure_keeps_position(monkeypatch):
    """TP failure with the SL in place is NOT safety-critical: no undo."""
    _stub_preflight(monkeypatch)
    cancel_called = {"n": 0}

    def fake_order(env, k, s, **kw):
        if kw.get("order_type") == "TAKE_PROFIT_MARKET":
            raise BinanceAPIError(code=-1111, msg="Precision over the maximum.",
                                  http_status=400)
        return {"orderId": 1, "status": "FILLED", "executedQty": "0.001", "avgPrice": "70000"}

    def fake_cancel(*a, **kw):
        cancel_called["n"] += 1
        return {}

    monkeypatch.setattr("trading.binance_client.place_order", fake_order)
    monkeypatch.setattr("trading.binance_client.cancel_order", fake_cancel)

    cred_id = creds.add_credential("t", "testnet", "K", "S")
    result = service.place_order_bracket(
        cred_id,
        OrderRequest(symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.001),
        stop_loss_price=65000.0, take_profit_price=80000.0)
    assert not result.ok                    # overall failure (TP missing)
    assert result.entry.ok
    assert result.stop_loss.ok              # position IS protected
    assert result.take_profit and not result.take_profit.ok
    assert result.recovery is None          # no undo
    assert cancel_called["n"] == 0
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `python -m pytest tests/test_trading.py -v -k bracket`
Expected: new tests FAIL (no `recovery` attribute behavior); pre-existing bracket happy-path tests still pass

- [ ] **Step 3: Implement** — in `service.py`:

Extend the models import: `from trading.models import (OrderRequest, OrderResult, Position, Balance, BracketOrderResult, RecoveryResult)`.

Add after `_place_protection_with_retry`:

```python
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
    except requests.RequestException as e:
        state, raw = _query_order_state(env, api_key, secret, req.symbol, close_oid)
        if state == "exists":
            result = OrderResult(
                ok=True, binance_order_id=str(raw.get("orderId", "")),
                status=raw.get("status"), raw=raw,
            )
        else:
            result = OrderResult(ok=False, error=f"平仓请求网络异常（{state}）：{e}")
    except BinanceAPIError as e:
        result = OrderResult(ok=False, error=str(e))
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
```

Rewrite `place_order_bracket`'s docstring + SL/TP section (entry handling and the early `if stop_loss_price is None and take_profit_price is None` return stay as-is):

```python
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
```

- [ ] **Step 4: Run the whole trading module**

Run: `python -m pytest tests/test_trading.py -v`
Expected: ALL PASS (including pre-existing bracket happy-path tests)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -m "not network" -q`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add trading/service.py tests/test_trading.py
git commit -m "feat(trading): undo entry when SL cannot be placed (no naked positions)"
```

---

### Task 10: Update `verify_testnet.py` Step 6 to expect recovery

**Files:**
- Modify: `backend/scripts/verify_testnet.py:217-235`

- [ ] **Step 1: Replace the Step-6 block** — the scenario (wrong-side stop → -2021) now must end with the position GONE:

```python
        # Step 6: SL-rejection recovery (entry fills, SL rejected -> entry undone)
        bad_stop = wrong_side_stop_price("long", entry_price, filters.tick_size)
        nb = service.place_order_bracket(cred_id, OrderRequest(
            symbol=symbol, side="BUY", order_type="MARKET",
            quantity=qty, leverage=args.leverage), stop_loss_price=bad_stop)
        if nb.entry.ok and (nb.stop_loss is None or not nb.stop_loss.ok):
            acct2 = service.get_account(cred_id)
            has_pos = any(po["symbol"] == symbol and po["position_amt"] != 0
                          for po in acct2["positions"])
            recovered = nb.recovery is not None and nb.recovery.attempted
            if recovered and not has_pos and not nb.recovery.naked_position:
                rep.record("SL-rejection recovery", "PASS",
                           f"entry undone automatically: {nb.recovery.detail}")
            else:
                rep.record("SL-rejection recovery", "FAIL",
                           f"recovery={recovered} position_open={has_pos} "
                           f"naked={nb.recovery.naked_position if nb.recovery else '?'}")
        else:
            rep.record("SL-rejection recovery", "SKIP",
                       f"entry.ok={nb.entry.ok} (scenario not set up)")
        _cleanup(service, cred_id, symbol, quiet=True)
```

- [ ] **Step 2: Sanity check** (script only imports/parses; full run needs testnet creds):

Run: `python -c "import scripts.verify_testnet"`
Expected: no error

- [ ] **Step 3: Run the full suite** (the verifier's pure helpers are unchanged; only the orchestration block moved)

Run: `python -m pytest -m "not network" -q`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_testnet.py
git commit -m "feat(verify): testnet step 6 asserts SL-rejection recovery instead of reproducing the gap"
```

---

### Task 11: Frontend — surface recovery outcome on the Trade page

**Files:**
- Modify: `frontend/src/views/Trade.vue` (`submitOrder` at ~line 666; new `recoveryAlert` ref near the other refs ~line 429; template banner; styles)

- [ ] **Step 1: Add state** — next to the other refs (e.g. after `const submitError = ref('')` / nearby state declarations):

```js
const recoveryAlert = ref(null)   // { level: 'danger'|'warn'|'info', text: string }
```

- [ ] **Step 2: Replace `submitOrder`:**

```js
async function submitOrder() {
  submitting.value = true
  submitError.value = ''
  try {
    const res = await api.placeBracketOrder(pendingPayload.value)
    recoveryAlert.value = null
    if (res.recovery) {
      recoveryAlert.value = res.recovery.naked_position
        ? { level: 'danger',
            text: `⚠️ 止损设置失败且自动撤销未完成——可能存在无止损持仓，请立即检查持仓并手动处理！（${res.recovery.detail}）` }
        : { level: 'warn',
            text: `止损单设置失败，已自动撤销本次入场（以损定仓：无止损不持仓）。${res.recovery.detail}` }
    } else if (res.ok && res.entry?.warning) {
      recoveryAlert.value = { level: 'info', text: res.entry.warning }
    }
    if (!res.ok) {
      const errs = []
      if (!res.entry.ok) errs.push(`入场失败：${res.entry.error}`)
      if (res.stop_loss && !res.stop_loss.ok) errs.push(`止损失败：${res.stop_loss.error}`)
      if (res.take_profit && !res.take_profit.ok) errs.push(`止盈失败：${res.take_profit.error}`)
      submitError.value = errs.join(' · ') || '未知错误'
      if (res.recovery) {
        // entry was undone (or needs attention) — close the modal so the
        // banner and refreshed positions are visible
        confirmOpen.value = false
        await loadAccountAndOrders()
      }
      return
    }
    confirmOpen.value = false
    await loadAccountAndOrders()
  } catch (e) {
    submitError.value = e.message
  } finally {
    submitting.value = false
  }
}
```

- [ ] **Step 3: Add the banner to the template** — directly below the credential bar / above the chart area (anchor: the element that shows account info, before the chart container). Banner is dismissable:

```html
<div v-if="recoveryAlert" class="recovery-banner" :class="recoveryAlert.level">
  <span class="recovery-text">{{ recoveryAlert.text }}</span>
  <button class="recovery-close" @click="recoveryAlert = null">×</button>
</div>
```

- [ ] **Step 4: Add styles** (append to the `<style>` block, follow existing conventions):

```css
.recovery-banner {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px; border-radius: 8px; margin: 8px 0;
  font-size: 13px; line-height: 1.5;
}
.recovery-banner.danger { background: rgba(220, 53, 69, 0.12); border: 1px solid rgba(220, 53, 69, 0.6); color: #dc3545; font-weight: 600; }
.recovery-banner.warn   { background: rgba(255, 165, 0, 0.10);  border: 1px solid rgba(255, 165, 0, 0.5);  color: #c87f0a; }
.recovery-banner.info   { background: rgba(13, 110, 253, 0.08); border: 1px solid rgba(13, 110, 253, 0.4); color: #4a8fe7; }
.recovery-banner .recovery-text { flex: 1; }
.recovery-banner .recovery-close {
  background: none; border: none; cursor: pointer; color: inherit;
  font-size: 16px; padding: 0 4px;
}
```

- [ ] **Step 5: Build to verify**

Run (from `frontend/`): `npm run build`
Expected: build succeeds, no warnings about undefined refs

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/Trade.vue
git commit -m "feat(trade): surface SL-recovery outcome (banner: undone / naked-position alert)"
```

---

### Task 12: Full verification + docs

- [ ] **Step 1: Full backend suite**

Run (from `backend/`): `python -m pytest -m "not network" -q`
Expected: ALL PASS, no new warnings from trading tests

- [ ] **Step 2: Update the roadmap memory** (the agent session does this — naked-position gap is now CLOSED; next Option-B items: account-level guards)

- [ ] **Step 3: Final commit if anything is left uncommitted**

```bash
git status --short
```
Expected: clean
