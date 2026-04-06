# WoHub Phase 1a: Project Scaffolding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the foundational project structure — FastAPI backend, Vue 3 frontend, SQLite database with full schema, simple password auth, and Docker Compose — a working shell that all subsequent phases build upon.

**Architecture:** Monorepo with `backend/` (FastAPI + SQLite), `frontend/` (Vue 3 + Vite), `chartshot/` (stub for Phase 1c). Docker Compose runs two services. Frontend is Vite-built and served by FastAPI in production; dev-proxied during development.

**Tech Stack:** Python 3.13 / FastAPI / SQLite / Vue 3 / Vite / Docker Compose

---

## File Map

### Backend

| File | Responsibility |
|------|---------------|
| `backend/pyproject.toml` | Python project config + dependencies |
| `backend/main.py` | FastAPI app, lifespan events, static serving |
| `backend/config.py` | Configuration from env vars with defaults |
| `backend/database.py` | SQLite connection + full schema init |
| `backend/auth.py` | Password auth dependency + login/logout routes |
| `backend/api/__init__.py` | Router aggregation |
| `backend/api/health.py` | Health check endpoint |
| `backend/tests/conftest.py` | Pytest fixtures (test client, temp DB) |
| `backend/tests/test_database.py` | Schema creation tests |
| `backend/tests/test_auth.py` | Auth flow tests |
| `backend/tests/test_api.py` | Health endpoint test |

### Frontend

| File | Responsibility |
|------|---------------|
| `frontend/package.json` | Node project + dependencies |
| `frontend/vite.config.js` | Build config + API proxy |
| `frontend/index.html` | HTML entry point |
| `frontend/src/main.js` | Vue app bootstrap |
| `frontend/src/App.vue` | Root layout with sidebar navigation |
| `frontend/src/router/index.js` | Vue Router config |
| `frontend/src/views/Tasks.vue` | Task management placeholder |
| `frontend/src/views/Market.vue` | Market dashboard placeholder |
| `frontend/src/views/Channels.vue` | Channel management placeholder |
| `frontend/src/views/Settings.vue` | Settings placeholder |
| `frontend/src/views/Login.vue` | Login page |
| `frontend/src/api/client.js` | HTTP client wrapper |
| `frontend/src/assets/style.css` | Global dark theme CSS |

### Docker + Root

| File | Responsibility |
|------|---------------|
| `docker-compose.yml` | Service orchestration (2 containers) |
| `backend/Dockerfile` | Backend image (builds frontend + serves) |
| `chartshot/main.py` | Minimal health-check stub |
| `chartshot/requirements.txt` | ChartShot stub dependencies |
| `chartshot/Dockerfile` | ChartShot placeholder image |
| `.gitignore` | Git ignore rules |

---

### Task 1: Project Structure + Backend Dependencies

**Files:**
- Create: `.gitignore`
- Create: `backend/pyproject.toml`
- Create: `backend/tests/__init__.py`
- Create: `backend/api/__init__.py`

- [ ] **Step 1: Create `.gitignore`**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
.venv/
venv/

# Node
node_modules/
frontend/dist/

# Data
backend/data/*.db
backend/data/cookies.json
data/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# Env
.env

# Existing projects (reference only)
CryptoFuturesHub/
pine-screener/
ChartShot/
```

- [ ] **Step 2: Create `backend/pyproject.toml`**

```toml
[project]
name = "wohub"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
    "python-multipart>=0.0.9",
    "itsdangerous>=2.2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "httpx>=0.27.0",
]
```

- [ ] **Step 3: Create empty `__init__.py` files**

Create `backend/tests/__init__.py` and `backend/api/__init__.py` as empty files.

- [ ] **Step 4: Install dependencies and verify**

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -e ".[dev]"
python -c "import fastapi; print(fastapi.__version__)"
```

Expected: prints FastAPI version (0.115.x+)

- [ ] **Step 5: Commit**

```bash
git add .gitignore backend/pyproject.toml backend/tests/__init__.py backend/api/__init__.py
git commit -m "chore: initialize backend project structure and dependencies"
```

---

### Task 2: Configuration Module

**Files:**
- Create: `backend/config.py`
- Create: `backend/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config.py`:

```python
from config import Settings


def test_default_settings():
    settings = Settings()
    assert settings.app_password == "admin"
    assert settings.secret_key == "change-me-in-production"
    assert settings.db_path == "data/wohub.db"
    assert settings.chartshot_url == "http://chartshot:5000"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8080
    assert settings.debug is False


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret123")
    monkeypatch.setenv("DEBUG", "true")
    settings = Settings()
    assert settings.app_password == "secret123"
    assert settings.debug is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_config.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: Write the implementation**

Create `backend/config.py`:

```python
import os


class Settings:
    def __init__(self):
        self.app_password = os.environ.get("APP_PASSWORD", "admin")
        self.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
        self.db_path = os.environ.get("DB_PATH", "data/wohub.db")
        self.chartshot_url = os.environ.get("CHARTSHOT_URL", "http://chartshot:5000")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8080"))
        self.debug = os.environ.get("DEBUG", "false").lower() == "true"


settings = Settings()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_config.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_config.py
git commit -m "feat: add configuration module with env var support"
```

---

### Task 3: Database Schema

**Files:**
- Create: `backend/database.py`
- Create: `backend/tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_database.py`:

```python
import sqlite3
import tempfile
import os

from database import init_db


def test_creates_all_tables():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "channels" in tables
        assert "tasks" in tables
        assert "signals" in tables
        assert "snapshots" in tables
        assert "outcomes" in tables
        assert "push_logs" in tables
        assert "screenshots" in tables


def test_channels_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(channels)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert "type" in columns
        assert "name" in columns
        assert "config_json" in columns
        assert "enabled" in columns


def test_tasks_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert "name" in columns
        assert "type" in columns
        assert "config_json" in columns
        assert "actions_json" in columns
        assert "channel_id" in columns
        assert "schedule" in columns
        assert "enabled" in columns


def test_signals_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(signals)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "id" in columns
        assert "task_id" in columns
        assert "symbol" in columns
        assert "exchange" in columns
        assert "indicator" in columns
        assert "timeframe" in columns
        assert "signal_type" in columns
        assert "triggered_at" in columns


def test_outcomes_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("PRAGMA table_info(outcomes)")
        columns = {row[1] for row in cursor.fetchall()}
        conn.close()

        assert "price_1h" in columns
        assert "price_4h" in columns
        assert "price_24h" in columns
        assert "change_1h" in columns
        assert "change_4h" in columns
        assert "change_24h" in columns


def test_init_db_is_idempotent():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        init_db(db_path)
        init_db(db_path)  # should not raise
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert len(tables) == 7
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_database.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'database'`

- [ ] **Step 3: Write the implementation**

Create `backend/database.py`:

```python
import sqlite3
import os

_connection = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    actions_json TEXT NOT NULL DEFAULT '[]',
    channel_id INTEGER REFERENCES channels(id),
    schedule TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id),
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    indicator TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal_type TEXT,
    triggered_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    price REAL,
    volume_24h REAL,
    change_24h REAL,
    funding_rate REAL,
    captured_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    price_1h REAL,
    price_4h REAL,
    price_24h REAL,
    change_1h REAL,
    change_4h REAL,
    change_24h REAL,
    tracked_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS push_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id),
    channel_id INTEGER REFERENCES channels(id),
    content_text TEXT,
    image_paths TEXT,
    ai_analysis TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    error_message TEXT,
    pushed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS screenshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    file_path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.close()


def get_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_database.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/database.py backend/tests/test_database.py
git commit -m "feat: add SQLite database with full schema (7 tables)"
```

---

### Task 4: FastAPI App + Health Check

**Files:**
- Create: `backend/api/health.py`
- Modify: `backend/api/__init__.py`
- Create: `backend/main.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/conftest.py`:

```python
import tempfile
import os
import pytest
from httpx import ASGITransport, AsyncClient

# Patch settings before importing app
_tmpdir = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmpdir, "test.db")
os.environ["APP_PASSWORD"] = "testpass"
os.environ["SECRET_KEY"] = "test-secret-key"

from main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def reset_db():
    """Reset database for each test."""
    from database import init_db
    db_path = os.environ["DB_PATH"]
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db(db_path)
    yield
```

Create `backend/tests/test_api.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_health_check_includes_db_status(client):
    response = await client.get("/api/health")
    data = response.json()
    assert data["database"] == "connected"
```

- [ ] **Step 2: Update `backend/pyproject.toml` to add pytest-asyncio**

Add `"pytest-asyncio>=0.24.0"` to the `dev` dependencies list in `backend/pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "httpx>=0.27.0",
    "pytest-asyncio>=0.24.0",
]
```

Then install:

```bash
cd backend && pip install -e ".[dev]"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && python -m pytest tests/test_api.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 4: Write the implementation**

Create `backend/api/health.py`:

```python
from fastapi import APIRouter
from database import get_db
from config import settings

router = APIRouter()


@router.get("/health")
def health_check():
    db_status = "disconnected"
    try:
        conn = get_db(settings.db_path)
        conn.execute("SELECT 1")
        conn.close()
        db_status = "connected"
    except Exception:
        pass

    return {
        "status": "ok",
        "version": "0.1.0",
        "database": db_status,
    }
```

Update `backend/api/__init__.py`:

```python
from fastapi import APIRouter
from api.health import router as health_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
```

Create `backend/main.py`:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from config import settings
from database import init_db
from api import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(settings.db_path)
    yield


app = FastAPI(title="WoHub", lifespan=lifespan)
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_api.py -v
```

Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/api/health.py backend/api/__init__.py backend/tests/conftest.py backend/tests/test_api.py backend/pyproject.toml
git commit -m "feat: add FastAPI app with health check endpoint"
```

---

### Task 5: Authentication

**Files:**
- Create: `backend/auth.py`
- Modify: `backend/api/__init__.py`
- Create: `backend/tests/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_auth.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_login_success(client):
    response = await client.post(
        "/api/auth/login",
        data={"password": "testpass"},
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert "session" in response.cookies


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    response = await client.post(
        "/api/auth/login",
        data={"password": "wrong"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_auth_status_not_logged_in(client):
    response = await client.get("/api/auth/status")
    assert response.status_code == 200
    assert response.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_auth_status_logged_in(client):
    login = await client.post(
        "/api/auth/login",
        data={"password": "testpass"},
    )
    session_cookie = login.cookies.get("session")

    response = await client.get(
        "/api/auth/status",
        cookies={"session": session_cookie},
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True


@pytest.mark.asyncio
async def test_logout(client):
    login = await client.post(
        "/api/auth/login",
        data={"password": "testpass"},
    )
    session_cookie = login.cookies.get("session")

    response = await client.post(
        "/api/auth/logout",
        cookies={"session": session_cookie},
    )
    assert response.status_code == 200

    status = await client.get("/api/auth/status")
    assert status.json()["authenticated"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_auth.py -v
```

Expected: FAIL — no `/api/auth/login` route

- [ ] **Step 3: Write the implementation**

Create `backend/auth.py`:

```python
import hmac
from fastapi import APIRouter, Form, Request, Response, HTTPException, Cookie
from itsdangerous import URLSafeTimedSerializer, BadSignature
from config import settings
from typing import Optional

router = APIRouter(prefix="/auth")

_serializer = URLSafeTimedSerializer(settings.secret_key)
SESSION_MAX_AGE = 86400 * 7  # 7 days


def _create_session_token() -> str:
    return _serializer.dumps({"authenticated": True})


def _verify_session_token(token: str) -> bool:
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("authenticated", False)
    except BadSignature:
        return False


def is_authenticated(session: Optional[str] = Cookie(None)) -> bool:
    if not session:
        return False
    return _verify_session_token(session)


@router.post("/login")
def login(response: Response, password: str = Form(...)):
    if not hmac.compare_digest(password, settings.app_password):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = _create_session_token()
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return {"authenticated": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key="session")
    return {"authenticated": False}


@router.get("/status")
def auth_status(session: Optional[str] = Cookie(None)):
    return {"authenticated": is_authenticated(session)}
```

Update `backend/api/__init__.py`:

```python
from fastapi import APIRouter
from api.health import router as health_router
from auth import router as auth_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(auth_router)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python -m pytest tests/test_auth.py -v
```

Expected: 5 passed

- [ ] **Step 5: Run all tests to verify nothing is broken**

```bash
cd backend && python -m pytest -v
```

Expected: all tests pass (config: 2, database: 6, api: 2, auth: 5 = 15 total)

- [ ] **Step 6: Commit**

```bash
git add backend/auth.py backend/api/__init__.py backend/tests/test_auth.py
git commit -m "feat: add password authentication with signed session cookies"
```

---

### Task 6: Frontend Project Setup (Vue 3 + Vite)

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.js`

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "wohub-frontend",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "vue": "^3.5.0",
    "vue-router": "^4.4.0"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.1.0",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 2: Create `frontend/vite.config.js`**

```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
})
```

- [ ] **Step 3: Create `frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>WoHub</title>
</head>
<body>
  <div id="app"></div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
```

- [ ] **Step 4: Create `frontend/src/main.js`**

```js
import { createApp } from 'vue'
import App from './App.vue'
import router from './router/index.js'
import './assets/style.css'

const app = createApp(App)
app.use(router)
app.mount('#app')
```

- [ ] **Step 5: Create a minimal `frontend/src/App.vue`** (temporary, replaced in Task 7)

```vue
<template>
  <div>
    <h1>WoHub</h1>
    <router-view />
  </div>
</template>
```

- [ ] **Step 6: Create minimal router and placeholder view**

Create `frontend/src/router/index.js`:

```js
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/tasks' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
```

Create `frontend/src/assets/style.css` (empty for now):

```css
/* Global styles — populated in Task 7 */
```

- [ ] **Step 7: Install dependencies and verify**

```bash
cd frontend && npm install
```

Expected: `node_modules/` created, no errors

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/vite.config.js frontend/index.html frontend/src/main.js frontend/src/App.vue frontend/src/router/index.js frontend/src/assets/style.css
git commit -m "feat: initialize Vue 3 + Vite frontend project"
```

---

### Task 7: Frontend Layout + Routing + Dark Theme

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/router/index.js`
- Modify: `frontend/src/assets/style.css`
- Create: `frontend/src/views/Tasks.vue`
- Create: `frontend/src/views/Market.vue`
- Create: `frontend/src/views/Channels.vue`
- Create: `frontend/src/views/Settings.vue`
- Create: `frontend/src/views/Login.vue`

- [ ] **Step 1: Create the dark theme CSS**

Replace `frontend/src/assets/style.css`:

```css
:root {
  --bg-primary: #0d1117;
  --bg-secondary: #161b22;
  --bg-tertiary: #21262d;
  --border: #30363d;
  --text-primary: #e6edf3;
  --text-secondary: #8b949e;
  --accent: #58a6ff;
  --accent-hover: #79c0ff;
  --success: #3fb950;
  --danger: #f85149;
  --warning: #d29922;
  --sidebar-width: 220px;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  line-height: 1.6;
}

a {
  color: var(--accent);
  text-decoration: none;
}

a:hover {
  color: var(--accent-hover);
}

/* Layout */
.app-layout {
  display: flex;
  min-height: 100vh;
}

.sidebar {
  width: var(--sidebar-width);
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
}

.sidebar-header {
  padding: 20px;
  border-bottom: 1px solid var(--border);
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.sidebar-nav {
  flex: 1;
  padding: 12px 0;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  color: var(--text-secondary);
  transition: all 0.15s;
  cursor: pointer;
  border-left: 3px solid transparent;
}

.nav-item:hover {
  color: var(--text-primary);
  background: var(--bg-tertiary);
}

.nav-item.active {
  color: var(--text-primary);
  background: var(--bg-tertiary);
  border-left-color: var(--accent);
}

.main-content {
  margin-left: var(--sidebar-width);
  flex: 1;
  padding: 24px 32px;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h1 {
  font-size: 24px;
  font-weight: 600;
}

.page-header p {
  color: var(--text-secondary);
  margin-top: 4px;
}

/* Cards */
.card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 16px;
}

/* Buttons */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--bg-tertiary);
  color: var(--text-primary);
  cursor: pointer;
  font-size: 14px;
  transition: all 0.15s;
}

.btn:hover {
  background: var(--border);
}

.btn-primary {
  background: #238636;
  border-color: #2ea043;
}

.btn-primary:hover {
  background: #2ea043;
}

/* Forms */
input, select, textarea {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-primary);
  padding: 8px 12px;
  font-size: 14px;
  width: 100%;
}

input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--accent);
}

/* Status badge */
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.badge-success {
  background: rgba(63, 185, 80, 0.15);
  color: var(--success);
}

.badge-danger {
  background: rgba(248, 81, 73, 0.15);
  color: var(--danger);
}

/* Login page */
.login-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
}

.login-box {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 32px;
  width: 360px;
}

.login-box h1 {
  text-align: center;
  margin-bottom: 24px;
  font-size: 24px;
}

.login-box .form-group {
  margin-bottom: 16px;
}

.login-box label {
  display: block;
  margin-bottom: 6px;
  color: var(--text-secondary);
  font-size: 14px;
}

.login-box .btn {
  width: 100%;
  justify-content: center;
  margin-top: 8px;
}

.login-error {
  color: var(--danger);
  font-size: 14px;
  text-align: center;
  margin-top: 12px;
}

/* Empty state */
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--text-secondary);
}

.empty-state h3 {
  color: var(--text-primary);
  margin-bottom: 8px;
}
```

- [ ] **Step 2: Create view components**

Create `frontend/src/views/Login.vue`:

```vue
<template>
  <div class="login-container">
    <div class="login-box">
      <h1>WoHub</h1>
      <form @submit.prevent="handleLogin">
        <div class="form-group">
          <label>Password</label>
          <input type="password" v-model="password" placeholder="Enter password" autofocus />
        </div>
        <button class="btn btn-primary" type="submit">Login</button>
        <p v-if="error" class="login-error">{{ error }}</p>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client.js'

const router = useRouter()
const password = ref('')
const error = ref('')

async function handleLogin() {
  error.value = ''
  try {
    await api.login(password.value)
    router.push('/tasks')
  } catch (e) {
    error.value = 'Wrong password'
  }
}
</script>
```

Create `frontend/src/views/Tasks.vue`:

```vue
<template>
  <div>
    <div class="page-header">
      <h1>Tasks</h1>
      <p>Monitor and manage signal detection tasks</p>
    </div>
    <div class="empty-state card">
      <h3>No tasks yet</h3>
      <p>Create your first monitoring task to get started.</p>
      <button class="btn btn-primary" style="margin-top: 16px">Create Task</button>
    </div>
  </div>
</template>
```

Create `frontend/src/views/Market.vue`:

```vue
<template>
  <div>
    <div class="page-header">
      <h1>Market</h1>
      <p>Real-time funding rates and price changes across exchanges</p>
    </div>
    <div class="empty-state card">
      <h3>Coming in Phase 1b</h3>
      <p>Market data dashboard will be available after migration from CryptoFuturesHub.</p>
    </div>
  </div>
</template>
```

Create `frontend/src/views/Channels.vue`:

```vue
<template>
  <div>
    <div class="page-header">
      <h1>Channels</h1>
      <p>Configure push notification channels</p>
    </div>
    <div class="empty-state card">
      <h3>Coming in Phase 1d</h3>
      <p>Telegram, Discord, and Webhook channel management.</p>
    </div>
  </div>
</template>
```

Create `frontend/src/views/Settings.vue`:

```vue
<template>
  <div>
    <div class="page-header">
      <h1>Settings</h1>
      <p>System configuration and service status</p>
    </div>
    <div class="empty-state card">
      <h3>Coming soon</h3>
      <p>TradingView cookies, ChartShot service status, global parameters.</p>
    </div>
  </div>
</template>
```

- [ ] **Step 3: Update router**

Replace `frontend/src/router/index.js`:

```js
import { createRouter, createWebHistory } from 'vue-router'
import Login from '../views/Login.vue'
import Tasks from '../views/Tasks.vue'
import Market from '../views/Market.vue'
import Channels from '../views/Channels.vue'
import Settings from '../views/Settings.vue'

const routes = [
  { path: '/login', component: Login, meta: { public: true } },
  { path: '/', redirect: '/tasks' },
  { path: '/tasks', component: Tasks },
  { path: '/market', component: Market },
  { path: '/channels', component: Channels },
  { path: '/settings', component: Settings },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  if (to.meta.public) return true

  try {
    const res = await fetch('/api/auth/status')
    const data = await res.json()
    if (!data.authenticated) return '/login'
  } catch {
    return '/login'
  }
})

export default router
```

- [ ] **Step 4: Update App.vue with sidebar layout**

Replace `frontend/src/App.vue`:

```vue
<template>
  <div v-if="route.meta.public">
    <router-view />
  </div>
  <div v-else class="app-layout">
    <aside class="sidebar">
      <div class="sidebar-header">WoHub</div>
      <nav class="sidebar-nav">
        <router-link
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="nav-item"
          :class="{ active: route.path === item.path }"
        >
          <span>{{ item.icon }}</span>
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="nav-item" @click="handleLogout" style="border-top: 1px solid var(--border)">
        <span>Exit</span>
      </div>
    </aside>
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { useRoute, useRouter } from 'vue-router'
import { api } from './api/client.js'

const route = useRoute()
const router = useRouter()

const navItems = [
  { path: '/tasks', icon: '\u{1F4CB}', label: 'Tasks' },
  { path: '/market', icon: '\u{1F4CA}', label: 'Market' },
  { path: '/channels', icon: '\u{1F4E1}', label: 'Channels' },
  { path: '/settings', icon: '\u{2699}\u{FE0F}', label: 'Settings' },
]

async function handleLogout() {
  await api.logout()
  router.push('/login')
}
</script>
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/
git commit -m "feat: add frontend layout with sidebar navigation and dark theme"
```

---

### Task 8: Frontend API Client

**Files:**
- Create: `frontend/src/api/client.js`

- [ ] **Step 1: Create the API client**

Create `frontend/src/api/client.js`:

```js
const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })

  if (res.status === 401 && !path.startsWith('/auth/')) {
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    throw new Error(`API error: ${res.status}`)
  }

  return res.json()
}

export const api = {
  async login(password) {
    const form = new URLSearchParams()
    form.append('password', password)
    const res = await fetch(`${BASE}/auth/login`, {
      method: 'POST',
      body: form,
    })
    if (!res.ok) throw new Error('Login failed')
    return res.json()
  },

  async logout() {
    return request('/auth/logout', { method: 'POST' })
  },

  async authStatus() {
    return request('/auth/status')
  },

  async health() {
    return request('/health')
  },
}
```

- [ ] **Step 2: Verify frontend builds without errors**

```bash
cd frontend && npm run build
```

Expected: `dist/` directory created, no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.js
git commit -m "feat: add frontend API client with auth integration"
```

---

### Task 9: Docker Setup

**Files:**
- Create: `backend/Dockerfile`
- Create: `chartshot/main.py`
- Create: `chartshot/requirements.txt`
- Create: `chartshot/Dockerfile`
- Create: `docker-compose.yml`
- Modify: `backend/main.py` (add static file serving)

- [ ] **Step 1: Update `backend/main.py` to serve frontend static files**

Add static file mounting after the router include. Replace `backend/main.py`:

```python
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from config import settings
from database import init_db
from api import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(settings.db_path)
    yield


app = FastAPI(title="WoHub", lifespan=lifespan)
app.include_router(api_router)

# Serve Vue frontend in production
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(_static_dir, "assets")), name="assets")

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        file_path = os.path.join(_static_dir, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(_static_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
```

- [ ] **Step 2: Create `backend/Dockerfile`**

```dockerfile
# Stage 1: Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Backend
FROM python:3.13-slim
WORKDIR /app

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .

COPY backend/ ./

# Copy built frontend into backend static dir
COPY --from=frontend-build /app/frontend/dist ./static

RUN mkdir -p data

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 3: Create ChartShot stub**

Create `chartshot/requirements.txt`:

```
flask>=3.0
```

Create `chartshot/main.py`:

```python
from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "chartshot"})


@app.route("/api/screenshot", methods=["POST"])
def screenshot():
    # Stub — full implementation in Phase 1c
    return jsonify({"error": "not implemented"}), 501


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

Create `chartshot/Dockerfile`:

```dockerfile
FROM python:3.13-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

EXPOSE 5000
CMD ["python", "main.py"]
```

- [ ] **Step 4: Create `docker-compose.yml`**

```yaml
services:
  wohub:
    build:
      context: .
      dockerfile: backend/Dockerfile
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    environment:
      - TZ=UTC
      - APP_PASSWORD=${APP_PASSWORD:-admin}
      - SECRET_KEY=${SECRET_KEY:-change-me-in-production}
      - CHARTSHOT_URL=http://chartshot:5000
    depends_on:
      chartshot:
        condition: service_started
    restart: unless-stopped

  chartshot:
    build:
      context: ./chartshot
    volumes:
      - ./data/screenshots:/app/output
    environment:
      - TZ=UTC
    restart: unless-stopped
```

- [ ] **Step 5: Verify backend tests still pass** (static dir doesn't exist in test env, should be fine)

```bash
cd backend && python -m pytest -v
```

Expected: all 15 tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/Dockerfile backend/main.py chartshot/ docker-compose.yml
git commit -m "feat: add Docker setup with multi-stage build and ChartShot stub"
```

---

### Task 10: Integration Smoke Test

- [ ] **Step 1: Build and start containers**

```bash
docker compose build
docker compose up -d
```

Expected: both `wohub` and `chartshot` containers start without errors

- [ ] **Step 2: Verify health endpoints**

```bash
curl http://localhost:8080/api/health
```

Expected: `{"status":"ok","version":"0.1.0","database":"connected"}`

```bash
docker compose exec chartshot curl http://localhost:5000/health
```

Expected: `{"status":"ok","service":"chartshot"}`

- [ ] **Step 3: Verify frontend is served**

```bash
curl -s http://localhost:8080/ | head -5
```

Expected: HTML containing `<title>WoHub</title>`

- [ ] **Step 4: Verify auth flow**

```bash
# Login
curl -c cookies.txt -X POST http://localhost:8080/api/auth/login -d "password=admin"

# Check status with cookie
curl -b cookies.txt http://localhost:8080/api/auth/status

# Cleanup
rm cookies.txt
```

Expected: login returns `{"authenticated":true}`, status returns `{"authenticated":true}`

- [ ] **Step 5: Stop containers**

```bash
docker compose down
```

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: Phase 1a complete — project scaffolding verified"
```

---

## Phase 1a Deliverables

After completing all tasks, you should have:

1. **FastAPI backend** with health check and password auth (15 passing tests)
2. **SQLite database** with all 7 tables created on startup
3. **Vue 3 frontend** with dark theme, sidebar navigation, 4 placeholder views + login
4. **Docker Compose** running 2 containers (main + ChartShot stub)
5. **Single port** (8080) serving both API and frontend

## Next Plans

- **Phase 1b**: Market dashboard — migrate CryptoFuturesHub exchange adapters and build Market.vue
- **Phase 1c**: ChartShot service — migrate Playwright screenshot logic into the chartshot container
- **Phase 1d**: Push channels — Telegram push module + Channels.vue management UI
- **Phase 1e**: Task engine — scheduler, executor, Pine screener migration
- **Phase 1f**: Four task types — config forms + execution logic + trigger rules
- **Phase 1g**: Data loop — signal recording, snapshots, outcome tracking
