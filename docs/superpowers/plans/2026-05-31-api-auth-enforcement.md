# API Server-Side Auth Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce cookie-session authentication on all API endpoints except `/auth/*` and `/health`, so unauthenticated requests to protected routes (incl. real-order trading endpoints) get 401 — with zero regression to the existing 195 tests.

**Architecture:** Add a `require_auth` FastAPI dependency in `auth.py` (reuses the existing `is_authenticated` cookie check, raises 401). In `api/__init__.py`, keep health + auth routers public and mount all other routers under a single protected `APIRouter(dependencies=[Depends(require_auth)])`. Existing endpoint tests share one unauthenticated `client` fixture, so a `conftest.py` autouse fixture overrides `require_auth` to a no-op for them; a dedicated `test_auth_enforcement.py` (marked `no_auth_override`) verifies the real gate.

**Tech Stack:** FastAPI, pytest + pytest-asyncio, httpx ASGITransport.

**Spec:** `docs/superpowers/specs/2026-05-31-api-auth-enforcement-design.md`

**Reused facts (verified):**
- `backend/auth.py` already has `is_authenticated(session: Optional[str]) -> bool` and `_create_session_token()`; it imports `HTTPException` and `Cookie` from fastapi.
- `backend/api/__init__.py` builds `api_router = APIRouter(prefix="/api")` and includes: health, auth, market, channels, tasks, settings, ai, scanner, screenshots, klines, trading.
- `backend/tests/conftest.py` defines `from main import app`, a function-scoped `client` fixture (httpx `AsyncClient` over `ASGITransport(app=app)`), and an autouse `reset_db` fixture. `APP_PASSWORD` is set to `"testpass"`.
- `GET /api/settings/info` is a protected endpoint; `GET /api/health` and `GET /api/auth/status` are public.
- Tests run from `c:\Users\real\Desktop\WoHub\backend` with `python -m pytest` (Windows/PowerShell). Baseline: `195 passed, 13 deselected` with `-m "not network"`.

---

## Task 1: `require_auth` dependency

**Files:**
- Modify: `backend/auth.py`
- Test: `backend/tests/test_auth.py`

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_auth.py`)

```python
from auth import require_auth, _create_session_token
from fastapi import HTTPException


def test_require_auth_rejects_missing_session():
    with pytest.raises(HTTPException) as ei:
        require_auth(session=None)
    assert ei.value.status_code == 401


def test_require_auth_rejects_invalid_session():
    with pytest.raises(HTTPException) as ei:
        require_auth(session="garbage-not-a-token")
    assert ei.value.status_code == 401


def test_require_auth_accepts_valid_session():
    token = _create_session_token()
    assert require_auth(session=token) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_auth.py -k require_auth -v`
Expected: FAIL — `ImportError: cannot import name 'require_auth' from 'auth'`

- [ ] **Step 3: Add the dependency** to `backend/auth.py`, immediately after the `is_authenticated` function

```python
def require_auth(session: Optional[str] = Cookie(None)) -> None:
    """FastAPI dependency that rejects unauthenticated requests with 401.

    Attach to protected routers via `dependencies=[Depends(require_auth)]`.
    Reuses the same session-cookie verification as is_authenticated().
    """
    if not is_authenticated(session):
        raise HTTPException(status_code=401, detail="Not authenticated")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_auth.py -v`
Expected: PASS (the 3 new tests plus the existing auth tests)

- [ ] **Step 5: Commit**

```bash
git add backend/auth.py backend/tests/test_auth.py
git commit -m "feat(auth): add require_auth dependency (401 on missing/invalid session)"
```

---

## Task 2: Wire the protected router group + enforcement tests

**Files:**
- Modify: `backend/tests/conftest.py` (marker + autouse override)
- Modify: `backend/api/__init__.py` (public vs protected groups)
- Modify: `backend/api/trading.py` (docstring accuracy)
- Test: `backend/tests/test_auth_enforcement.py` (create)

- [ ] **Step 1: Add the marker + autouse override to `backend/tests/conftest.py`**

Append to the end of `backend/tests/conftest.py`:

```python
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_auth_override: run the test against the real require_auth gate "
        "(skip the bypass fixture)",
    )


@pytest.fixture(autouse=True)
def auth_override(request):
    """Existing endpoint tests share one unauthenticated `client`. Bypass the
    server-side auth gate for them by overriding require_auth to a no-op. Tests
    marked `no_auth_override` exercise the real gate instead."""
    from auth import require_auth
    if "no_auth_override" in request.keywords:
        # Ensure no leaked override from a prior test — exercise the real gate.
        app.dependency_overrides.pop(require_auth, None)
        yield
        return
    app.dependency_overrides[require_auth] = lambda: None
    yield
    app.dependency_overrides.pop(require_auth, None)
```

(`app` and `pytest` are already imported at the top of conftest.py.)

- [ ] **Step 2: Run the full suite to confirm the override is a harmless no-op so far**

Run: `python -m pytest -m "not network" -q`
Expected: PASS — `195 passed` (unchanged; require_auth exists but isn't wired to any route yet, so the override does nothing).

- [ ] **Step 3: Write the failing enforcement tests** — create `backend/tests/test_auth_enforcement.py`

```python
import pytest


@pytest.mark.no_auth_override
@pytest.mark.asyncio
async def test_protected_endpoint_rejects_without_cookie(client):
    resp = await client.get("/api/settings/info")
    assert resp.status_code == 401


@pytest.mark.no_auth_override
@pytest.mark.asyncio
async def test_protected_endpoint_allows_with_valid_cookie(client):
    login = await client.post("/api/auth/login", data={"password": "testpass"})
    session = login.cookies.get("session")
    resp = await client.get("/api/settings/info", cookies={"session": session})
    assert resp.status_code != 401


@pytest.mark.no_auth_override
@pytest.mark.asyncio
async def test_public_endpoints_allowed_without_cookie(client):
    assert (await client.get("/api/health")).status_code == 200
    status = await client.get("/api/auth/status")
    assert status.status_code == 200
    assert status.json()["authenticated"] is False
```

- [ ] **Step 4: Run the enforcement tests to verify the protected-without-cookie one fails**

Run: `python -m pytest tests/test_auth_enforcement.py -v`
Expected: `test_protected_endpoint_rejects_without_cookie` FAILS (currently returns 200, not 401, because auth isn't wired). The other two PASS.

- [ ] **Step 5: Wire the protected group** — replace the body of `backend/api/__init__.py` with:

```python
from fastapi import APIRouter, Depends
from api.health import router as health_router
from auth import router as auth_router, require_auth
from api.market import router as market_router
from api.channels import router as channels_router
from api.tasks import router as tasks_router
from api.settings import router as settings_router
from api.ai import router as ai_router
from api.scanner import router as scanner_router
from api.screenshots import router as screenshots_router
from api.klines import router as klines_router
from api.trading import router as trading_router

api_router = APIRouter(prefix="/api")

# --- public (no auth required) ---
api_router.include_router(health_router)
api_router.include_router(auth_router)

# --- protected (cookie-session auth required) ---
protected = APIRouter(dependencies=[Depends(require_auth)])
protected.include_router(market_router)
protected.include_router(channels_router)
protected.include_router(tasks_router)
protected.include_router(settings_router)
protected.include_router(ai_router)
protected.include_router(scanner_router)
protected.include_router(screenshots_router)
protected.include_router(klines_router)
protected.include_router(trading_router)
api_router.include_router(protected)
```

- [ ] **Step 6: Fix the now-stale docstring** in `backend/api/trading.py` (the module docstring at the top, lines 1-7). Replace the auth sentence:

Old:
```python
All endpoints require the standard cookie-session auth (handled at the router
level via the existing dependency wiring). Sensitive material (api_secret) is
never echoed back in any response.
```
New:
```python
All endpoints require cookie-session auth, enforced by the require_auth
dependency on the protected router group in api/__init__.py. Sensitive material
(api_secret) is never echoed back in any response.
```

- [ ] **Step 7: Run the enforcement tests, then the full suite**

Run: `python -m pytest tests/test_auth_enforcement.py -v`
Expected: all 3 PASS.

Run: `python -m pytest -m "not network" -q`
Expected: PASS — `198 passed` (195 existing + 3 new enforcement tests), 0 regressions. (Task 1 already added 3 auth unit tests, so the exact total may be 198; confirm no failures rather than the precise count.)

- [ ] **Step 8: Commit**

```bash
git add backend/tests/conftest.py backend/api/__init__.py backend/api/trading.py backend/tests/test_auth_enforcement.py
git commit -m "feat(api): enforce cookie-session auth on all routes except /auth and /health"
```

---

## Task 3: Manual verification + finish

**Files:** none (verification only)

- [ ] **Step 1: Full suite green**

Run: `python -m pytest -m "not network" -q`
Expected: all PASS, 0 failures.

- [ ] **Step 2: Live smoke (optional, app running on :8080)** — confirm the gate behaves end-to-end:
```bash
# protected route without cookie -> 401
curl -s -o NUL -w "%{http_code}" http://localhost:8080/api/trading/orders
# public route without cookie -> 200
curl -s -o NUL -w "%{http_code}" http://localhost:8080/api/health
```
Expected: `401` then `200`. (PowerShell: `curl.exe` or `(Invoke-WebRequest ... -SkipHttpErrorCheck).StatusCode`.)

- [ ] **Step 3: Frontend sanity** — `npm run dev`, confirm login still works and pages load after login (the browser sends the session cookie automatically; `client.js` already redirects to `/login` on 401, so an expired session bounces to login). No frontend code change is needed.

- [ ] **Step 4: Finish the branch** — invoke `superpowers:finishing-a-development-branch` to merge to `main` and push (the user deploys the VPS via `git pull && docker compose up -d --build`).

---

## Self-Review

**Spec coverage:**
- `require_auth` dependency (401 on missing/invalid, None on valid) → Task 1 ✓
- Public groups (health, auth) + protected group (9 routers) with `require_auth`, prefixes unchanged → Task 2 Step 5 ✓
- trading.py docstring corrected → Task 2 Step 6 ✓
- 401 data flow; frontend already handles 401; SPA/static unaffected (app-level, not in api_router) → covered by design; no code needed ✓
- conftest autouse override + `no_auth_override` marker; existing tests zero-change → Task 2 Steps 1-2 ✓
- `test_auth_enforcement.py`: protected→401 w/o cookie, →non-401 w/ cookie, public→200 → Task 2 Step 3 ✓
- Manual + branch finish → Task 3 ✓

**Placeholder scan:** All steps contain complete code/commands. No TBD/TODO. The "exact total may be 198" note is a count caveat, not a placeholder — the pass/fail assertion is explicit.

**Type/name consistency:** `require_auth` defined in Task 1 (auth.py), imported in Task 2 (api/__init__.py: `from auth import router as auth_router, require_auth`) and conftest (`from auth import require_auth`) — names match. `no_auth_override` marker string identical in `pytest_configure`, the fixture check, and the test markers. `_create_session_token` used in Task 1 test matches the existing auth.py function. `app.dependency_overrides` keyed by the `require_auth` function object consistently. Protected endpoint `GET /api/settings/info` used consistently in the enforcement tests.
