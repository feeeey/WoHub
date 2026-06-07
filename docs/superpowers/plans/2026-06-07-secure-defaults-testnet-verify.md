# Secure-Default Mainnet Gate + Testnet E2E Verify — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block all Binance **mainnet** activity (and emit a loud startup warning) whenever `APP_PASSWORD`/`SECRET_KEY` are still at their insecure defaults, and ship a service-layer one-shot script that verifies the testnet `/plan → bracket → open-orders → close` path end-to-end.

**Architecture:** A `config.insecure_defaults()` helper is the single source of truth. Two `ValueError`-raising guards (in `service._resolve()` and `credentials.add_credential()`) close the mainnet money path; `main.py` lifespan logs a warning. The verifier (`scripts/verify_testnet.py`) drives `trading.service` against a throwaway temp DB, cleaning up in a `finally` block; its only non-trivial math is two pure helpers that get unit tests.

**Tech Stack:** FastAPI, pytest + pytest-asyncio, httpx ASGITransport, SQLite, cryptography (Fernet), Binance USDT-M fapi (testnet).

**Spec:** `docs/superpowers/specs/2026-06-07-secure-defaults-testnet-verify-design.md`

**Reused facts (verified by reading the code):**
- `backend/config.py` `Settings.__init__` reads `APP_PASSWORD` (default `"admin"`) and `SECRET_KEY` (default `"change-me-in-production"`); module exposes a singleton `settings`.
- `backend/trading/service.py` `_resolve(credential_id)` returns `(env, api_key, secret)` and is called by **every** Binance-touching function; it imports `from trading.credentials import get_credential` and `from config import settings`.
- `backend/trading/credentials.py` `add_credential(label, env, api_key, api_secret)` already validates `env in ("testnet","mainnet")`; imports `from config import settings`.
- `backend/api/trading.py`: `place`, `place_bracket`, `plan`, `credentials_add` map `except ValueError → 400`; `account`, `close`, `open_orders`, `binance_order_history`, `credentials_test` map `except ValueError → 404`.
- `backend/main.py` has a `lifespan` async context manager that runs `init_db(...)` then `start_all_enabled()`.
- `backend/app_logger.py` `log(source, level, message, detail=None)` (also prints to stdout).
- `backend/trading/position_plan.py`: `parse_filters(exchange_info, symbol) -> SymbolFilters(tick_size, step_size, min_qty, min_notional)`; `_round_step(value, step, mode)` with `mode in {"floor","ceil","nearest"}`.
- `build_position_plan(...)` returns a dict with keys incl. `entry_price, stop_price, take_profit_price, quantity, feasible, warnings, symbol, interval, direction`.
- `service.test_credential(id) -> {"ok","env","api_key_tail"}`; `get_account(id) -> {"total_wallet_balance","total_unrealized_pnl","available_balance","balances","positions":[{symbol,position_amt,...}]}`.
- `service.place_order(id, OrderRequest) -> OrderResult(ok, error, ...)`; `place_order_bracket(id, OrderRequest, stop_loss_price=, take_profit_price=) -> BracketOrderResult(ok, entry, stop_loss, take_profit)`; `list_open_orders(id, symbol=)`→ list of raw Binance dicts (keys incl. `orderId`, `type`); `cancel_open_order(id, symbol, order_id)`; `close_position(id, symbol) -> OrderResult`.
- `bn.exchange_info(env, api_key)` (no secret needed).
- Tests run from `c:\Users\real\Desktop\WoHub\backend` with `python -m pytest`. Conftest sets `APP_PASSWORD="testpass"`, `SECRET_KEY="test-secret-key"` (both non-default) and an autouse `auth_override`. Baseline: `201 passed` with `-m "not network"`.

---

## Task 1: `config.insecure_defaults()`

**Files:**
- Modify: `backend/config.py`
- Test: `backend/tests/test_security_config.py` (create)

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_security_config.py`

```python
from config import settings, DEFAULT_APP_PASSWORD, DEFAULT_SECRET_KEY


def test_insecure_defaults_both(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET_KEY)
    monkeypatch.setattr(settings, "app_password", DEFAULT_APP_PASSWORD)
    assert settings.insecure_defaults() == ["SECRET_KEY", "APP_PASSWORD"]


def test_insecure_defaults_none(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "a-strong-random-key")
    monkeypatch.setattr(settings, "app_password", "a-strong-password")
    assert settings.insecure_defaults() == []


def test_insecure_defaults_only_secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET_KEY)
    monkeypatch.setattr(settings, "app_password", "a-strong-password")
    assert settings.insecure_defaults() == ["SECRET_KEY"]


def test_insecure_defaults_only_password(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "a-strong-random-key")
    monkeypatch.setattr(settings, "app_password", DEFAULT_APP_PASSWORD)
    assert settings.insecure_defaults() == ["APP_PASSWORD"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_security_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_APP_PASSWORD' from 'config'`.

- [ ] **Step 3: Implement** — replace `backend/config.py` body with constants + method

```python
import os

DEFAULT_APP_PASSWORD = "admin"
DEFAULT_SECRET_KEY = "change-me-in-production"


class Settings:
    def __init__(self):
        self.app_password = os.environ.get("APP_PASSWORD", DEFAULT_APP_PASSWORD)
        self.secret_key = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
        self.db_path = os.environ.get("DB_PATH", "data/wohub.db")
        self.chartshot_url = os.environ.get("CHARTSHOT_URL", "http://chartshot:5000")
        self.screenshots_dir = os.environ.get("SCREENSHOTS_DIR", "data/screenshots")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8080"))
        self.debug = os.environ.get("DEBUG", "false").lower() == "true"
        self.cache_ttl = int(os.environ.get("CACHE_TTL", "15"))
        self.min_volume_24h = float(os.environ.get("MIN_VOLUME_24H", "100000"))
        self.proxy_enabled = os.environ.get("PROXY_ENABLED", "false").lower() == "true"
        self.proxy_host = os.environ.get("PROXY_HOST", "host.docker.internal")
        self.proxy_port = os.environ.get("PROXY_PORT", "24000")

    def insecure_defaults(self) -> list[str]:
        """Names of security-critical settings still at their insecure default.
        Used to gate mainnet trading and to emit a startup warning."""
        bad = []
        if self.secret_key == DEFAULT_SECRET_KEY:
            bad.append("SECRET_KEY")
        if self.app_password == DEFAULT_APP_PASSWORD:
            bad.append("APP_PASSWORD")
        return bad


settings = Settings()
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_security_config.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_security_config.py
git commit -m "feat(security): config.insecure_defaults() detects default APP_PASSWORD/SECRET_KEY"
```

---

## Task 2: Mainnet gate in `service._resolve()`

**Files:**
- Modify: `backend/trading/service.py` (`_resolve`, lines 29-34)
- Test: `backend/tests/test_mainnet_gate.py` (create)

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_mainnet_gate.py`

```python
import pytest

from config import settings, DEFAULT_APP_PASSWORD, DEFAULT_SECRET_KEY
from trading import service


def _set_defaults(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET_KEY)
    monkeypatch.setattr(settings, "app_password", DEFAULT_APP_PASSWORD)


def _set_secure(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "strong-key")
    monkeypatch.setattr(settings, "app_password", "strong-pass")


def test_resolve_blocks_mainnet_under_defaults(monkeypatch):
    _set_defaults(monkeypatch)
    monkeypatch.setattr(service, "get_credential", lambda cid: ("mainnet", "k", "s"))
    with pytest.raises(ValueError) as ei:
        service._resolve(1)
    assert "主网" in str(ei.value)


def test_resolve_allows_testnet_under_defaults(monkeypatch):
    _set_defaults(monkeypatch)
    monkeypatch.setattr(service, "get_credential", lambda cid: ("testnet", "k", "s"))
    assert service._resolve(1) == ("testnet", "k", "s")


def test_resolve_allows_mainnet_when_secure(monkeypatch):
    _set_secure(monkeypatch)
    monkeypatch.setattr(service, "get_credential", lambda cid: ("mainnet", "k", "s"))
    assert service._resolve(1) == ("mainnet", "k", "s")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_mainnet_gate.py -k resolve -v`
Expected: `test_resolve_blocks_mainnet_under_defaults` FAILS (no ValueError raised — currently returns the tuple). Other two PASS.

- [ ] **Step 3: Implement** — replace `_resolve` in `backend/trading/service.py`

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_mainnet_gate.py -k resolve -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/trading/service.py backend/tests/test_mainnet_gate.py
git commit -m "feat(security): block mainnet credentials in _resolve under insecure defaults"
```

---

## Task 3: Mainnet gate in `add_credential()` + API-level test

**Files:**
- Modify: `backend/trading/credentials.py` (`add_credential`, lines 45-61)
- Test: append to `backend/tests/test_mainnet_gate.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_mainnet_gate.py`

```python
from trading.credentials import add_credential


def test_add_credential_blocks_mainnet_under_defaults(monkeypatch):
    _set_defaults(monkeypatch)
    with pytest.raises(ValueError) as ei:
        add_credential("m", "mainnet", "key0000000", "secret0000")
    assert "主网" in str(ei.value)


def test_add_credential_allows_testnet_under_defaults(monkeypatch):
    _set_defaults(monkeypatch)
    cid = add_credential("t", "testnet", "key0000000", "secret0000")
    assert cid > 0


@pytest.mark.asyncio
async def test_api_add_mainnet_credential_blocked_under_defaults(client, monkeypatch):
    _set_defaults(monkeypatch)
    resp = await client.post("/api/trading/credentials", json={
        "label": "m", "env": "mainnet",
        "api_key": "key0000000", "api_secret": "secret0000",
    })
    assert resp.status_code == 400
    assert "主网" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_api_add_testnet_credential_allowed_under_defaults(client, monkeypatch):
    _set_defaults(monkeypatch)
    resp = await client.post("/api/trading/credentials", json={
        "label": "t", "env": "testnet",
        "api_key": "key0000000", "api_secret": "secret0000",
    })
    assert resp.status_code != 400
```

(The autouse `auth_override` fixture lets these requests through the auth gate; `reset_db` gives each test a fresh temp DB, so `add_credential` writes succeed. `_set_defaults` is already defined at the top of this file from Task 2.)

- [ ] **Step 2: Run to verify the mainnet ones fail**

Run: `python -m pytest tests/test_mainnet_gate.py -k "add_credential or api_add" -v`
Expected: `test_add_credential_blocks_mainnet_under_defaults` and `test_api_add_mainnet_credential_blocked_under_defaults` FAIL (credential is created / 200 returned). The two testnet ones PASS.

- [ ] **Step 3: Implement** — add the guard in `backend/trading/credentials.py` `add_credential`, right after the `env` check

```python
def add_credential(label: str, env: str, api_key: str, api_secret: str) -> int:
    if env not in ("testnet", "mainnet"):
        raise ValueError(f"env must be 'testnet' or 'mainnet', got {env!r}")
    if env == "mainnet" and settings.insecure_defaults():
        raise ValueError(
            "拒绝在不安全的默认配置下新建主网凭据：" +
            "、".join(settings.insecure_defaults()) +
            " 仍为默认值。请设置强随机的 SECRET_KEY 与 APP_PASSWORD 后重启。"
        )
    if not api_key or not api_secret:
        raise ValueError("api_key and api_secret must be non-empty")
    enc = encrypt_secret(api_secret)
    db = get_db(settings.db_path)
    try:
        cursor = db.execute(
            "INSERT INTO trading_credentials (label, env, api_key, api_secret_enc, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            (label, env, api_key, enc),
        )
        db.commit()
        return cursor.lastrowid
    finally:
        db.close()
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_mainnet_gate.py -v`
Expected: PASS (all 7 tests in the file).

- [ ] **Step 5: Commit**

```bash
git add backend/trading/credentials.py backend/tests/test_mainnet_gate.py
git commit -m "feat(security): block creating mainnet credentials under insecure defaults"
```

---

## Task 4: Startup warning in `main.py` lifespan

**Files:**
- Modify: `backend/main.py`
- Test: append to `backend/tests/test_security_config.py`

- [ ] **Step 1: Write the failing test** — append to `backend/tests/test_security_config.py`

```python
from main import _insecure_default_warning


def test_warning_none_when_secure():
    assert _insecure_default_warning([]) is None


def test_warning_lists_names():
    msg = _insecure_default_warning(["SECRET_KEY", "APP_PASSWORD"])
    assert msg is not None
    assert "SECRET_KEY" in msg and "APP_PASSWORD" in msg
    assert "主网" in msg
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_security_config.py -k warning -v`
Expected: FAIL — `ImportError: cannot import name '_insecure_default_warning' from 'main'`.

- [ ] **Step 3: Implement** — in `backend/main.py`: add `import sys` at top, add the pure function, and call it in `lifespan` after `init_db`

Add near the top imports:
```python
import sys
```

Add a module-level function (above `lifespan`):
```python
def _insecure_default_warning(bad: list[str]) -> str | None:
    """Build the startup security-warning message, or None if config is safe."""
    if not bad:
        return None
    return ("不安全的默认配置：" + ", ".join(bad) +
            " 仍为默认值；主网交易已被禁用。设置强随机值后重启。")
```

In `lifespan`, right after `init_db(settings.db_path)`:
```python
    _msg = _insecure_default_warning(settings.insecure_defaults())
    if _msg:
        from app_logger import log as _applog
        _applog("security", "warn", _msg)
        _bar = "=" * 60
        print(f"\n{_bar}\n⚠️  WoHub: {_msg}\n{_bar}\n", file=sys.stderr, flush=True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_security_config.py -v`
Expected: PASS (6 tests total in the file).

- [ ] **Step 5: Manual smoke (visible warning)** — from `backend/`:

```bash
python -c "import main; print(main._insecure_default_warning(['SECRET_KEY','APP_PASSWORD']))"
```
Expected: prints the Chinese warning string containing `SECRET_KEY`, `APP_PASSWORD`, `主网`.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_security_config.py
git commit -m "feat(security): warn at startup when APP_PASSWORD/SECRET_KEY are default"
```

---

## Task 5: Pure helpers for the verifier

**Files:**
- Create: `backend/scripts/__init__.py` (empty — makes `scripts` importable for tests)
- Create: `backend/scripts/verify_testnet.py` (helpers only in this task)
- Test: `backend/tests/test_verify_testnet_helpers.py` (create)

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_verify_testnet_helpers.py`

```python
from scripts.verify_testnet import wrong_side_stop_price, sub_min_notional_qty


def test_wrong_side_stop_long_is_above_entry():
    # long SL must be BELOW entry to be valid; a wrong-side stop is ABOVE.
    assert wrong_side_stop_price("long", 100.0, 0.1) > 100.0


def test_wrong_side_stop_short_is_below_entry():
    assert wrong_side_stop_price("short", 100.0, 0.1) < 100.0


def test_sub_min_notional_qty_below_minimum():
    # entry=100, min_notional=20, step=0.1 -> qty*100 must be < 20 and > 0
    qty = sub_min_notional_qty(100.0, 20.0, 0.1)
    assert qty > 0
    assert qty * 100.0 < 20.0


def test_sub_min_notional_qty_falls_back_to_one_step():
    # half-notional floors to zero -> fall back to a single step (still > 0)
    qty = sub_min_notional_qty(1_000_000.0, 5.0, 0.001)
    assert qty == 0.001
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_verify_testnet_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts'` (or import error).

- [ ] **Step 3: Implement** — create `backend/scripts/__init__.py` (empty file) and create `backend/scripts/verify_testnet.py` with the path shim + helpers ONLY:

```python
#!/usr/bin/env python
"""Testnet end-to-end verification for the WoHub trading path. (helpers; full
CLI added in the next task)."""
import os
import sys

# Make backend/ importable when run as `python scripts/verify_testnet.py`.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def wrong_side_stop_price(direction: str, entry_price: float, tick_size: float) -> float:
    """A stop price on the WRONG side of entry so Binance rejects the
    STOP_MARKET (error -2021 'would immediately trigger'). long -> above entry,
    short -> below entry. Rounded to tick. Used to demonstrate the
    entry-fills-but-SL-rejects 'naked position' gap."""
    from trading.position_plan import _round_step
    raw = entry_price * (1.05 if direction == "long" else 0.95)
    return _round_step(raw, tick_size, "nearest")


def sub_min_notional_qty(entry_price: float, min_notional: float, step_size: float) -> float:
    """A quantity whose notional is deliberately below min_notional (a filter
    rejection). Falls back to one step if half-notional floors to zero."""
    from trading.position_plan import _round_step
    qty = _round_step((min_notional * 0.5) / entry_price, step_size, "floor")
    if qty <= 0:
        qty = step_size
    return qty
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_verify_testnet_helpers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/__init__.py backend/scripts/verify_testnet.py backend/tests/test_verify_testnet_helpers.py
git commit -m "feat(verify): pure helpers for testnet verification script"
```

---

## Task 6: Full verifier CLI (`scripts/verify_testnet.py`)

**Files:**
- Modify: `backend/scripts/verify_testnet.py` (add CLI + 7-step flow + cleanup; keep the helpers from Task 5)

No automated test (it places real testnet orders); verified offline via `--help` parse and online by the user with credentials.

- [ ] **Step 1: Implement** — extend `backend/scripts/verify_testnet.py` to its full form (replace the whole file, keeping the helpers identical):

```python
#!/usr/bin/env python
"""Testnet end-to-end verification for the WoHub trading path.

Drives the SAME service layer the automation will reuse:
  add_credential -> build_position_plan -> place_order_bracket
  -> list_open_orders -> close_position

Binance USDT-M *testnet only*. Uses a throwaway temp DB (never touches
data/wohub.db). Places REAL testnet orders and cleans up (cancel + close) in a
finally block. The API secret is never printed.

Usage (PowerShell):
  $env:BINANCE_TESTNET_KEY="..."; $env:BINANCE_TESTNET_SECRET="..."
  python scripts/verify_testnet.py --symbol BTCUSDT --interval 15m

Usage (bash):
  BINANCE_TESTNET_KEY=... BINANCE_TESTNET_SECRET=... \
    python scripts/verify_testnet.py --symbol BTCUSDT
"""
import argparse
import getpass
import os
import shutil
import sys
import tempfile

# Make backend/ importable when run as `python scripts/verify_testnet.py`.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def wrong_side_stop_price(direction: str, entry_price: float, tick_size: float) -> float:
    """A stop price on the WRONG side of entry so Binance rejects the
    STOP_MARKET (error -2021 'would immediately trigger'). long -> above entry,
    short -> below entry. Rounded to tick."""
    from trading.position_plan import _round_step
    raw = entry_price * (1.05 if direction == "long" else 0.95)
    return _round_step(raw, tick_size, "nearest")


def sub_min_notional_qty(entry_price: float, min_notional: float, step_size: float) -> float:
    """A quantity whose notional is deliberately below min_notional. Falls back
    to one step if half-notional floors to zero."""
    from trading.position_plan import _round_step
    qty = _round_step((min_notional * 0.5) / entry_price, step_size, "floor")
    if qty <= 0:
        qty = step_size
    return qty


class Report:
    def __init__(self):
        self.rows = []

    def record(self, step, status, detail=""):
        self.rows.append((step, status, detail))
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}.get(status, "[?]")
        print(f"{mark} {step}" + (f" -- {detail}" if detail else ""))

    def ok(self):
        return all(s != "FAIL" for _, s, _ in self.rows)

    def summary(self):
        print("\n" + "=" * 60 + "\nSUMMARY")
        for step, status, detail in self.rows:
            print(f"  {status:4}  {step}" + (f" -- {detail}" if detail else ""))
        print("=" * 60)


def _confirm(symbol, assume_yes):
    if assume_yes:
        return True
    ans = input(f"This will place REAL *testnet* orders on {symbol}. Continue? [y/N] ")
    return ans.strip().lower() in ("y", "yes")


def _cleanup(service, cred_id, symbol, quiet=False):
    """Best-effort: cancel all open orders for symbol, then close any position."""
    try:
        for o in service.list_open_orders(cred_id, symbol=symbol):
            oid = o.get("orderId")
            if oid is not None:
                try:
                    service.cancel_open_order(cred_id, symbol, oid)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        res = service.close_position(cred_id, symbol)
        if not quiet:
            print(f"  cleanup: close_position {symbol} ok={res.ok}"
                  + ("" if res.ok else f" ({res.error})"))
    except Exception as e:
        if not quiet:
            print(f"  cleanup: close_position {symbol} error: {e}")


def main(argv=None):
    p = argparse.ArgumentParser(description="WoHub testnet E2E verification")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="15m")
    p.add_argument("--risk-pct", type=float, default=1.0)
    p.add_argument("--rr", type=float, default=1.5)
    p.add_argument("--leverage", type=int, default=5)
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--keep", action="store_true", help="skip cleanup/teardown")
    args = p.parse_args(argv)
    symbol = args.symbol.upper()

    key = os.environ.get("BINANCE_TESTNET_KEY") or input("Binance TESTNET API key: ").strip()
    secret = os.environ.get("BINANCE_TESTNET_SECRET") or getpass.getpass("Binance TESTNET API secret: ").strip()
    if not key or not secret:
        print("Missing API key/secret.", file=sys.stderr)
        return 2
    if not _confirm(symbol, args.yes):
        print("Aborted.")
        return 1

    # Isolate: throwaway DB so we never touch the real one. Must be set before
    # importing config/service.
    tmpdir = tempfile.mkdtemp(prefix="wohub-verify-")
    os.environ["DB_PATH"] = os.path.join(tmpdir, "verify.db")

    rep = Report()
    service = None
    cred_id = None
    try:
        from database import init_db
        init_db(os.environ["DB_PATH"])
        from trading import service as service
        from trading import binance_client as bn
        from trading import position_plan as pp
        from trading.credentials import add_credential
        from trading.models import OrderRequest

        # Step 1: credential + reachability
        cred_id = add_credential("verify-testnet", "testnet", key, secret)
        info = service.test_credential(cred_id)
        if info["env"] != "testnet":
            rep.record("env is testnet", "FAIL", info["env"])
            return 1
        rep.record("credential reachable", "PASS",
                   f"env={info['env']} key=...{info['api_key_tail']}")

        _cleanup(service, cred_id, symbol, quiet=True)  # clear prior-run leftovers

        # Step 2: account snapshot
        acct = service.get_account(cred_id)
        equity = acct["total_wallet_balance"] + acct["total_unrealized_pnl"]
        rep.record("account snapshot", "PASS" if equity > 0 else "WARN",
                   f"equity={equity:.2f} avail={acct['available_balance']:.2f}")

        # Step 3: position plan (read-only)
        plan = service.build_position_plan(
            credential_id=cred_id, symbol=symbol, interval=args.interval,
            direction="long", order_type="MARKET", risk_pct=args.risk_pct,
            rr=args.rr, leverage=args.leverage,
        )
        if not plan["feasible"]:
            rep.record("position plan feasible", "FAIL", "; ".join(plan["warnings"]))
            return 1
        rep.record("position plan feasible", "PASS",
                   f"entry={plan['entry_price']} sl={plan['stop_price']} "
                   f"tp={plan['take_profit_price']} qty={plan['quantity']}")

        entry_price = plan["entry_price"]
        qty = plan["quantity"]
        filters = pp.parse_filters(bn.exchange_info("testnet", key), symbol)

        # Step 4: happy-path bracket (entry + SL + TP), then flatten
        br = service.place_order_bracket(
            cred_id,
            OrderRequest(symbol=symbol, side="BUY", order_type="MARKET",
                         quantity=qty, leverage=args.leverage),
            stop_loss_price=plan["stop_price"],
            take_profit_price=plan["take_profit_price"],
        )
        sl_ok = br.stop_loss is not None and br.stop_loss.ok
        tp_ok = br.take_profit is not None and br.take_profit.ok
        if br.entry.ok and sl_ok and tp_ok:
            opens = service.list_open_orders(cred_id, symbol=symbol)
            prot = [o for o in opens if o.get("type") in ("STOP_MARKET", "TAKE_PROFIT_MARKET")]
            rep.record("bracket entry+SL+TP", "PASS", f"{len(prot)} protective orders live")
        else:
            err = br.entry.error or (br.stop_loss and br.stop_loss.error) or (br.take_profit and br.take_profit.error)
            rep.record("bracket entry+SL+TP", "FAIL",
                       f"entry={br.entry.ok} sl={sl_ok} tp={tp_ok} err={err}")
        _cleanup(service, cred_id, symbol, quiet=True)

        # Step 5: deliberate filter rejection (sub-min-notional)
        bad_qty = sub_min_notional_qty(entry_price, filters.min_notional, filters.step_size)
        rej = service.place_order(cred_id, OrderRequest(
            symbol=symbol, side="BUY", order_type="MARKET",
            quantity=bad_qty, leverage=args.leverage))
        if not rej.ok and rej.error:
            rep.record("filter rejection surfaced", "PASS", rej.error[:120])
        else:
            rep.record("filter rejection surfaced", "FAIL", f"expected rejection, got ok={rej.ok}")
            _cleanup(service, cred_id, symbol, quiet=True)

        # Step 6: naked-position reproduction (entry fills, SL rejected)
        bad_stop = wrong_side_stop_price("long", entry_price, filters.tick_size)
        nb = service.place_order_bracket(cred_id, OrderRequest(
            symbol=symbol, side="BUY", order_type="MARKET",
            quantity=qty, leverage=args.leverage), stop_loss_price=bad_stop)
        if nb.entry.ok and (nb.stop_loss is None or not nb.stop_loss.ok):
            acct2 = service.get_account(cred_id)
            has_pos = any(po["symbol"] == symbol and po["position_amt"] != 0
                          for po in acct2["positions"])
            opens2 = service.list_open_orders(cred_id, symbol=symbol)
            stop_present = any(o.get("type") == "STOP_MARKET" for o in opens2)
            sl_err = nb.stop_loss.error[:80] if nb.stop_loss else "?"
            rep.record("naked-position gap reproduced", "WARN",
                       f"entry filled, SL rejected ({sl_err}); position_open={has_pos} "
                       f"stop_present={stop_present} -> Option B (bracket recovery) needed")
        else:
            rep.record("naked-position gap reproduced", "SKIP",
                       f"entry.ok={nb.entry.ok} (scenario not set up)")
        _cleanup(service, cred_id, symbol, quiet=True)

        return 0 if rep.ok() else 1
    except Exception as e:  # noqa: BLE001
        rep.record("unexpected error", "FAIL", str(e)[:200])
        return 1
    finally:
        if service is not None and cred_id is not None and not args.keep:
            _cleanup(service, cred_id, symbol, quiet=False)
        rep.summary()
        if args.keep:
            print(f"--keep set: temp DB left at {os.environ.get('DB_PATH')}; "
                  f"manually flatten {symbol} on testnet if needed.")
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Offline smoke — argument parsing** (from `backend/`)

Run: `python scripts/verify_testnet.py --help`
Expected: prints usage with `--symbol`, `--interval`, `--risk-pct`, `--rr`, `--leverage`, `--yes`, `--keep`; exit 0. (No network, no DB — argparse exits before `main()` body.)

- [ ] **Step 3: Confirm helpers still import after the rewrite**

Run: `python -m pytest tests/test_verify_testnet_helpers.py -v`
Expected: PASS (4 tests, unchanged).

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/verify_testnet.py
git commit -m "feat(verify): testnet E2E verification CLI (plan->bracket->open-orders->close)"
```

---

## Task 7: Full-suite verification + finish

**Files:** none (verification only)

- [ ] **Step 1: Full suite green** (from `backend/`)

Run: `python -m pytest -m "not network" -q`
Expected: all PASS — baseline `201` + new tests (4 config + 2 warning + 7 gate + 4 helpers = 17) ≈ `218 passed`, 0 failures. (Confirm 0 failures rather than the exact count.)

- [ ] **Step 2: Confirm no accidental real-DB writes** — the verifier sets `DB_PATH` to a temp dir; `data/wohub.db` must be untouched by the test run.

Run: `git status --short`
Expected: only the intended source/test/doc files changed; no `data/` changes.

- [ ] **Step 3: Finish the branch** — invoke `superpowers:finishing-a-development-branch` to merge `feature/secure-defaults-testnet-verify` to `main` and push (user deploys VPS via `git pull && docker compose up -d --build`; the user then runs `scripts/verify_testnet.py` with testnet credentials).

---

## Self-Review

**Spec coverage:**
- config defaults constants + `insecure_defaults()` → Task 1 ✓
- mainnet gate at `_resolve` (ValueError) → Task 2 ✓
- mainnet gate at `add_credential` + API 400/non-400 → Task 3 ✓
- startup warning (applog + stderr), testnet/local/pytest unaffected → Task 4 ✓ (pure fn tested; I/O glue is trivial)
- verifier temp-DB isolation, env-var/getpass creds, service-layer 7-step flow, deterministic reject + naked-position scenarios, finally-cleanup, `--keep`/`--yes` → Tasks 5-6 ✓
- pure helpers `sub_min_notional_qty` / `wrong_side_stop_price` unit-tested offline → Task 5 ✓
- full suite green, no real-DB writes, finish branch → Task 7 ✓

**Placeholder scan:** No TBD/TODO. Every code step shows full code; every run step shows the command + expected result. The "≈218" is a count caveat (assert 0 failures), not a placeholder.

**Type/name consistency:** `insecure_defaults()` returns `list[str]` and is consumed identically in `_resolve`, `add_credential`, and `_insecure_default_warning`. `DEFAULT_APP_PASSWORD`/`DEFAULT_SECRET_KEY` defined in Task 1, imported in Tasks 1-3 tests. Helper signatures `wrong_side_stop_price(direction, entry_price, tick_size)` and `sub_min_notional_qty(entry_price, min_notional, step_size)` identical in Task 5 (def + test) and Task 6 (def + call sites). `service`/`bn`/`pp` import aliases match the call sites. Plan-dict keys (`entry_price`, `stop_price`, `take_profit_price`, `quantity`, `feasible`, `warnings`) match `PositionPlan`/`build_position_plan`. `OrderRequest`, `BracketOrderResult.entry/stop_loss/take_profit`, `OrderResult.ok/error` match `trading/models.py`.
