# WoHub Phase 2: AI Analysis Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate OpenAI-compatible LLM into WoHub — AI config management, streaming chat for signal analysis, automatic AI commentary on push notifications, and strategy prompt versioning.

**Architecture:** `backend/ai/` package handles LLM communication (streaming httpx), context assembly (signals + snapshots + history + screenshots), and strategy CRUD. Frontend adds a new AI analysis page with SSE-powered typewriter effect. The executor's `ai_analysis` action calls LLM then edits the already-sent push message to append the AI commentary.

**Tech Stack:** Python 3.13 / FastAPI / httpx (streaming) / SSE / SQLite / Vue 3

---

## File Map

### Backend — New Files

| File | Responsibility |
|------|---------------|
| `backend/ai/__init__.py` | Empty package init |
| `backend/ai/llm_client.py` | OpenAI-compatible streaming HTTP client |
| `backend/ai/context_builder.py` | Assemble signal data + history + screenshot into LLM messages |
| `backend/ai/strategy.py` | Default strategy prompt + DB helpers |
| `backend/api/ai.py` | AI config, signal analysis (SSE), strategy CRUD routes |
| `backend/actions/ai_analysis.py` | Executor action: LLM call → edit push message |
| `backend/tests/test_llm_client.py` | LLM client tests (mocked) |
| `backend/tests/test_ai_api.py` | AI API endpoint tests |

### Backend — Modified Files

| File | Change |
|------|--------|
| `backend/database.py` | Add 3 new tables (ai_config, strategies, ai_analyses) |
| `backend/api/__init__.py` | Register ai router |
| `backend/tasks/executor.py` | Wire ai_analysis action |
| `backend/api/settings.py` | Add AI config section |

### Frontend — New/Modified Files

| File | Change |
|------|--------|
| `frontend/src/views/AI.vue` | New: signal analysis page with streaming |
| `frontend/src/views/Settings.vue` | Add AI config + strategy management sections |
| `frontend/src/api/client.js` | Add AI API methods |
| `frontend/src/router/index.js` | Add /ai route |
| `frontend/src/App.vue` | Add AI nav item to sidebar |

---

### Task 1: Database Schema + AI Config API

**Files:**
- Modify: `backend/database.py`
- Create: `backend/ai/__init__.py`
- Create: `backend/ai/strategy.py`
- Create: `backend/api/ai.py`
- Modify: `backend/api/__init__.py`
- Create: `backend/tests/test_ai_api.py`

- [ ] **Step 1: Add 3 new tables to database.py**

Append these CREATE TABLE statements to the SCHEMA string in `backend/database.py`, before the closing `"""`:

```sql
CREATE TABLE IF NOT EXISTS ai_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    strategy_id INTEGER,
    analysis_text TEXT NOT NULL,
    sentiment TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 2: Create AI package + strategy helper**

Create `backend/ai/__init__.py` (empty).

Create `backend/ai/strategy.py`:

```python
from database import get_db
from config import settings

DEFAULT_STRATEGY_NAME = "默认技术分析师"
DEFAULT_STRATEGY_PROMPT = """你是一个专业的加密货币技术分析师。你只基于价格走势、成交量、技术指标进行分析，不考虑任何消息面因素。

你的分析应该包括：
1. 当前信号的含义解读
2. 结合历史数据的可靠性评估
3. 关键支撑位和阻力位（如果截图可见）
4. 短期方向判断（看涨/看跌/中性）
5. 风险提示

保持简洁，每次分析控制在 200 字以内。使用中文回复。"""


def ensure_default_strategy():
    db = get_db(settings.db_path)
    row = db.execute("SELECT id FROM strategies WHERE is_default = 1").fetchone()
    if not row:
        db.execute(
            "INSERT INTO strategies (name, system_prompt, is_default) VALUES (?, ?, 1)",
            (DEFAULT_STRATEGY_NAME, DEFAULT_STRATEGY_PROMPT),
        )
        db.commit()
    db.close()


def get_default_strategy():
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM strategies WHERE is_default = 1").fetchone()
    db.close()
    if row:
        return {"id": row["id"], "name": row["name"], "system_prompt": row["system_prompt"]}
    return {"id": 0, "name": DEFAULT_STRATEGY_NAME, "system_prompt": DEFAULT_STRATEGY_PROMPT}


def get_ai_config():
    db = get_db(settings.db_path)
    rows = db.execute("SELECT key, value FROM ai_config").fetchall()
    db.close()
    config = {r["key"]: r["value"] for r in rows}
    return {
        "api_key": config.get("api_key", ""),
        "base_url": config.get("base_url", "https://api.openai.com/v1"),
        "model": config.get("model", "gpt-4o"),
        "max_tokens": int(config.get("max_tokens", "1000")),
    }


def set_ai_config(data: dict):
    db = get_db(settings.db_path)
    for key, value in data.items():
        if key in ("api_key", "base_url", "model", "max_tokens"):
            db.execute(
                "INSERT INTO ai_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')",
                (key, str(value), str(value)),
            )
    db.commit()
    db.close()
```

- [ ] **Step 3: Write tests**

Create `backend/tests/test_ai_api.py`:

```python
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_get_ai_config(client):
    resp = await client.get("/api/ai/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "api_key" in data
    assert "base_url" in data
    assert "model" in data


@pytest.mark.asyncio
async def test_update_ai_config(client):
    resp = await client.put("/api/ai/config", json={
        "api_key": "sk-test123",
        "base_url": "https://api.example.com/v1",
        "model": "gpt-4o-mini",
        "max_tokens": 500,
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    get_resp = await client.get("/api/ai/config")
    data = get_resp.json()
    assert data["base_url"] == "https://api.example.com/v1"
    assert data["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_list_strategies(client):
    resp = await client.get("/api/ai/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["is_default"] is True


@pytest.mark.asyncio
async def test_create_strategy(client):
    resp = await client.post("/api/ai/strategies", json={
        "name": "Test Strategy",
        "system_prompt": "You are a test analyst.",
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Strategy"
    assert resp.json()["is_default"] is False


@pytest.mark.asyncio
async def test_set_default_strategy(client):
    create = await client.post("/api/ai/strategies", json={
        "name": "New Default",
        "system_prompt": "New prompt.",
    })
    sid = create.json()["id"]
    resp = await client.post(f"/api/ai/strategies/{sid}/default")
    assert resp.status_code == 200
    assert resp.json()["is_default"] is True


@pytest.mark.asyncio
async def test_get_signals_list(client):
    resp = await client.get("/api/ai/signals")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

- [ ] **Step 4: Implement AI API routes**

Create `backend/api/ai.py`:

```python
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from database import get_db
from config import settings
from ai.strategy import get_ai_config, set_ai_config, ensure_default_strategy, get_default_strategy

router = APIRouter(prefix="/ai")


class AIConfigUpdate(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None


class StrategyCreate(BaseModel):
    name: str
    system_prompt: str


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None


# --- AI Config ---

@router.get("/config")
def ai_config():
    conf = get_ai_config()
    # Mask API key for display
    if conf["api_key"]:
        conf["api_key_display"] = conf["api_key"][:8] + "..." + conf["api_key"][-4:] if len(conf["api_key"]) > 16 else "****"
        conf["has_key"] = True
    else:
        conf["api_key_display"] = ""
        conf["has_key"] = False
    del conf["api_key"]
    return conf


@router.put("/config")
def update_ai_config(body: AIConfigUpdate):
    data = {k: v for k, v in body.dict().items() if v is not None}
    set_ai_config(data)
    return {"ok": True}


@router.post("/test")
def test_ai_connection():
    conf = get_ai_config()
    if not conf["api_key"]:
        return {"ok": False, "error": "API Key not configured"}
    try:
        import httpx
        resp = httpx.get(
            f"{conf['base_url']}/models",
            headers={"Authorization": f"Bearer {conf['api_key']}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return {"ok": True, "models_count": len(resp.json().get("data", []))}
        return {"ok": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- Strategies ---

@router.get("/strategies")
def list_strategies():
    ensure_default_strategy()
    db = get_db(settings.db_path)
    rows = db.execute("SELECT * FROM strategies ORDER BY is_default DESC, created_at DESC").fetchall()
    db.close()
    return [
        {"id": r["id"], "name": r["name"], "system_prompt": r["system_prompt"],
         "is_default": bool(r["is_default"]), "created_at": r["created_at"]}
        for r in rows
    ]


@router.post("/strategies")
def create_strategy(body: StrategyCreate):
    db = get_db(settings.db_path)
    cursor = db.execute(
        "INSERT INTO strategies (name, system_prompt) VALUES (?, ?)",
        (body.name, body.system_prompt),
    )
    db.commit()
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (cursor.lastrowid,)).fetchone()
    db.close()
    return {"id": row["id"], "name": row["name"], "system_prompt": row["system_prompt"],
            "is_default": bool(row["is_default"]), "created_at": row["created_at"]}


@router.put("/strategies/{strategy_id}")
def update_strategy(strategy_id: int, body: StrategyUpdate):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Strategy not found")
    updates, params = [], []
    if body.name is not None:
        updates.append("name = ?"); params.append(body.name)
    if body.system_prompt is not None:
        updates.append("system_prompt = ?"); params.append(body.system_prompt)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(strategy_id)
        db.execute(f"UPDATE strategies SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    db.close()
    return {"id": row["id"], "name": row["name"], "system_prompt": row["system_prompt"],
            "is_default": bool(row["is_default"]), "created_at": row["created_at"]}


@router.delete("/strategies/{strategy_id}")
def delete_strategy(strategy_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Strategy not found")
    if row["is_default"]:
        db.close()
        raise HTTPException(400, "Cannot delete default strategy")
    db.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
    db.commit()
    db.close()
    return {"ok": True}


@router.post("/strategies/{strategy_id}/default")
def set_default_strategy(strategy_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Strategy not found")
    db.execute("UPDATE strategies SET is_default = 0")
    db.execute("UPDATE strategies SET is_default = 1 WHERE id = ?", (strategy_id,))
    db.commit()
    row = db.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
    db.close()
    return {"id": row["id"], "name": row["name"], "system_prompt": row["system_prompt"],
            "is_default": bool(row["is_default"]), "created_at": row["created_at"]}


# --- Signals for AI analysis ---

@router.get("/signals")
def list_signals():
    db = get_db(settings.db_path)
    rows = db.execute("""
        SELECT s.*, 
               snap.price, snap.volume_24h, snap.change_24h, snap.funding_rate,
               a.id as analysis_id, a.analysis_text, a.sentiment
        FROM signals s
        LEFT JOIN snapshots snap ON snap.signal_id = s.id
        LEFT JOIN ai_analyses a ON a.signal_id = s.id
        ORDER BY s.triggered_at DESC
        LIMIT 50
    """).fetchall()
    db.close()
    return [
        {
            "id": r["id"], "task_id": r["task_id"], "symbol": r["symbol"],
            "exchange": r["exchange"], "indicator": r["indicator"],
            "timeframe": r["timeframe"], "signal_type": r["signal_type"],
            "triggered_at": r["triggered_at"],
            "price": r["price"], "volume_24h": r["volume_24h"],
            "change_24h": r["change_24h"], "funding_rate": r["funding_rate"],
            "has_analysis": r["analysis_id"] is not None,
            "analysis_text": r["analysis_text"],
            "sentiment": r["sentiment"],
        }
        for r in rows
    ]


@router.get("/signals/{signal_id}")
def get_signal_detail(signal_id: int):
    db = get_db(settings.db_path)
    sig = db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if not sig:
        db.close()
        raise HTTPException(404, "Signal not found")

    snap = db.execute("SELECT * FROM snapshots WHERE signal_id = ?", (signal_id,)).fetchone()
    outcome = db.execute("SELECT * FROM outcomes WHERE signal_id = ?", (signal_id,)).fetchone()
    analysis = db.execute("SELECT * FROM ai_analyses WHERE signal_id = ? ORDER BY created_at DESC LIMIT 1", (signal_id,)).fetchone()

    # History: same symbol + indicator, last 10
    history = db.execute("""
        SELECT s.id, s.triggered_at, s.timeframe, o.change_1h, o.change_4h, o.change_24h
        FROM signals s
        LEFT JOIN outcomes o ON o.signal_id = s.id
        WHERE s.symbol = ? AND s.indicator = ? AND s.id != ?
        ORDER BY s.triggered_at DESC LIMIT 10
    """, (sig["symbol"], sig["indicator"], signal_id)).fetchall()

    db.close()

    return {
        "signal": dict(sig),
        "snapshot": dict(snap) if snap else None,
        "outcome": dict(outcome) if outcome else None,
        "analysis": {"text": analysis["analysis_text"], "sentiment": analysis["sentiment"],
                      "created_at": analysis["created_at"]} if analysis else None,
        "history": [dict(h) for h in history],
    }
```

- [ ] **Step 5: Register AI router**

In `backend/api/__init__.py`, add:

```python
from api.ai import router as ai_router
api_router.include_router(ai_router)
```

- [ ] **Step 6: Update main.py lifespan to ensure default strategy**

Add to the lifespan in `backend/main.py`, after `init_db`:

```python
    from ai.strategy import ensure_default_strategy
    ensure_default_strategy()
```

- [ ] **Step 7: Run tests**

```bash
cd backend && source .venv/Scripts/activate && python -m pytest tests/test_ai_api.py -v
```

Expected: 6 passed

- [ ] **Step 8: Run all tests**

```bash
cd backend && python -m pytest -v -m "not network"
```

Expected: all pass (56 + 6 = 62)

- [ ] **Step 9: Commit**

```bash
git add backend/database.py backend/ai/ backend/api/ai.py backend/api/__init__.py backend/main.py backend/tests/test_ai_api.py
git commit -m "feat: add AI config, strategy management, and signal analysis API"
```

---

### Task 2: LLM Client (Streaming)

**Files:**
- Create: `backend/ai/llm_client.py`
- Create: `backend/tests/test_llm_client.py`

- [ ] **Step 1: Write tests**

Create `backend/tests/test_llm_client.py`:

```python
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from ai.llm_client import LLMClient


def test_build_headers():
    client = LLMClient(api_key="sk-test", base_url="https://api.example.com/v1")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer sk-test"
    assert "application/json" in headers["Content-Type"]


def test_build_request_body():
    client = LLMClient(api_key="sk-test", base_url="https://api.example.com/v1", model="gpt-4o")
    body = client._build_body(
        [{"role": "user", "content": "hello"}],
        stream=True,
    )
    assert body["model"] == "gpt-4o"
    assert body["stream"] is True
    assert body["messages"][0]["content"] == "hello"


def test_build_body_with_max_tokens():
    client = LLMClient(api_key="k", base_url="u", model="m", max_tokens=500)
    body = client._build_body([{"role": "user", "content": "hi"}], stream=False)
    assert body["max_tokens"] == 500


def test_from_config():
    with patch("ai.llm_client.get_ai_config", return_value={
        "api_key": "sk-abc", "base_url": "https://x.com/v1",
        "model": "gpt-4o-mini", "max_tokens": 800,
    }):
        client = LLMClient.from_config()
        assert client.api_key == "sk-abc"
        assert client.model == "gpt-4o-mini"
```

- [ ] **Step 2: Implement LLM client**

Create `backend/ai/llm_client.py`:

```python
import json
import httpx
from ai.strategy import get_ai_config


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str = "gpt-4o", max_tokens: int = 1000):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens

    @classmethod
    def from_config(cls):
        conf = get_ai_config()
        return cls(
            api_key=conf["api_key"],
            base_url=conf["base_url"],
            model=conf["model"],
            max_tokens=conf["max_tokens"],
        )

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_body(self, messages, stream=False):
        return {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }

    def chat(self, messages):
        """Non-streaming chat. Returns full response text."""
        body = self._build_body(messages, stream=False)
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def stream_chat(self, messages):
        """Streaming chat. Yields text chunks."""
        body = self._build_body(messages, stream=True)
        with httpx.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
```

- [ ] **Step 3: Run tests**

```bash
cd backend && python -m pytest tests/test_llm_client.py -v
```

Expected: 4 passed

- [ ] **Step 4: Commit**

```bash
git add backend/ai/llm_client.py backend/tests/test_llm_client.py
git commit -m "feat: add OpenAI-compatible streaming LLM client"
```

---

### Task 3: Context Builder + AI Analysis Action

**Files:**
- Create: `backend/ai/context_builder.py`
- Create: `backend/actions/__init__.py`
- Create: `backend/actions/ai_analysis.py`
- Modify: `backend/tasks/executor.py`

- [ ] **Step 1: Create context builder**

Create `backend/ai/context_builder.py`:

```python
import base64
import os
from database import get_db
from config import settings


def build_context(signal_id: int) -> list:
    """Build LLM messages array from signal data."""
    db = get_db(settings.db_path)

    sig = db.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if not sig:
        db.close()
        return []

    snap = db.execute("SELECT * FROM snapshots WHERE signal_id = ?", (signal_id,)).fetchone()
    outcome = db.execute("SELECT * FROM outcomes WHERE signal_id = ?", (signal_id,)).fetchone()

    # History: same symbol + indicator
    history = db.execute("""
        SELECT s.triggered_at, s.timeframe, o.change_1h, o.change_4h, o.change_24h
        FROM signals s
        LEFT JOIN outcomes o ON o.signal_id = s.id
        WHERE s.symbol = ? AND s.indicator = ? AND s.id != ?
        ORDER BY s.triggered_at DESC LIMIT 10
    """, (sig["symbol"], sig["indicator"], signal_id)).fetchall()

    # Screenshot
    screenshot = db.execute(
        "SELECT file_path FROM screenshots WHERE signal_id = ?", (signal_id,)
    ).fetchone()

    db.close()

    # Build text context
    lines = [
        f"## 信号信息",
        f"- 币种: {sig['symbol']}",
        f"- 交易所: {sig['exchange']}",
        f"- 指标: {sig['indicator']}",
        f"- 时间周期: {sig['timeframe']}",
        f"- 触发时间: {sig['triggered_at']}",
    ]

    if snap:
        lines.extend([
            f"\n## 触发时市场快照",
            f"- 价格: {snap['price']}",
            f"- 24h成交额: {snap['volume_24h']}",
            f"- 24h涨跌: {snap['change_24h']}%",
            f"- 资金费率: {snap['funding_rate']}",
        ])

    if outcome:
        lines.extend([
            f"\n## 信号后续表现",
            f"- 1h后: {outcome['change_1h'] or '待追踪'}%",
            f"- 4h后: {outcome['change_4h'] or '待追踪'}%",
            f"- 24h后: {outcome['change_24h'] or '待追踪'}%",
        ])

    if history:
        lines.append(f"\n## 历史记录（{sig['indicator']} 在 {sig['symbol']} 的最近 {len(history)} 次触发）")
        wins_1h = sum(1 for h in history if h["change_1h"] and h["change_1h"] > 0)
        wins_24h = sum(1 for h in history if h["change_24h"] and h["change_24h"] > 0)
        total_with_outcome = sum(1 for h in history if h["change_1h"] is not None)
        if total_with_outcome > 0:
            lines.append(f"- 1h正收益率: {wins_1h}/{total_with_outcome} ({wins_1h/total_with_outcome*100:.0f}%)")
            lines.append(f"- 24h正收益率: {wins_24h}/{total_with_outcome} ({wins_24h/total_with_outcome*100:.0f}%)")

    text = "\n".join(lines)

    # Build message content
    content = [{"type": "text", "text": text}]

    # Add screenshot if available
    if screenshot:
        img_path = screenshot["file_path"]
        if os.path.isfile(img_path):
            try:
                with open(img_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })
            except Exception:
                pass

    return [{"role": "user", "content": content}]
```

- [ ] **Step 2: Create AI analysis action**

Create `backend/actions/__init__.py` (empty).

Create `backend/actions/ai_analysis.py`:

```python
from ai.llm_client import LLMClient
from ai.context_builder import build_context
from ai.strategy import get_default_strategy, get_ai_config
from database import get_db
from config import settings


def run_ai_analysis(signal_id: int) -> str:
    """Run AI analysis for a signal. Returns analysis text."""
    conf = get_ai_config()
    if not conf["api_key"]:
        return ""

    strategy = get_default_strategy()
    context = build_context(signal_id)
    if not context:
        return ""

    messages = [{"role": "system", "content": strategy["system_prompt"]}] + context

    try:
        client = LLMClient.from_config()
        analysis = client.chat(messages)
    except Exception as e:
        print(f"[ai] LLM call failed: {e}")
        return f"AI 分析失败: {e}"

    # Determine sentiment
    sentiment = "neutral"
    lower = analysis.lower()
    if any(w in lower for w in ["看涨", "偏多", "bullish", "上涨"]):
        sentiment = "bullish"
    elif any(w in lower for w in ["看跌", "偏空", "bearish", "下跌"]):
        sentiment = "bearish"

    # Save to DB
    db = get_db(settings.db_path)
    db.execute(
        "INSERT INTO ai_analyses (signal_id, strategy_id, analysis_text, sentiment) VALUES (?, ?, ?, ?)",
        (signal_id, strategy["id"], analysis, sentiment),
    )
    db.commit()
    db.close()

    return analysis
```

- [ ] **Step 3: Wire ai_analysis into executor**

In `backend/tasks/executor.py`, add the AI analysis action call. After the `_record_signals` call in `_exec_watchlist_signal` and `_exec_market_scan`, add AI analysis logic.

Add this helper function to executor.py:

```python
def _run_ai_and_edit(task_id, overlaps, channel, push_message):
    """Run AI analysis for top signals and edit the push message with AI commentary."""
    try:
        from actions.ai_analysis import run_ai_analysis

        # Analyze top 3 signals
        db = get_db(settings.db_path)
        recent_signals = db.execute(
            "SELECT id, symbol FROM signals WHERE task_id = ? ORDER BY triggered_at DESC LIMIT 3",
            (task_id,),
        ).fetchall()
        db.close()

        ai_texts = []
        for sig in recent_signals:
            text = run_ai_analysis(sig["id"])
            if text:
                ai_texts.append(f"[{sig['symbol']}] {text}")

        if ai_texts and channel:
            ai_section = "\n\n🤖 AI 分析：\n" + "\n\n".join(ai_texts)
            full_message = push_message + ai_section
            try:
                from channels.telegram import TelegramChannel
                import json
                ch = TelegramChannel(
                    bot_token=channel["config"].get("bot_token", ""),
                    chat_id=channel["config"].get("chat_id", ""),
                )
                # Get the last push log to find message_id
                db = get_db(settings.db_path)
                log = db.execute(
                    "SELECT content_text FROM push_logs WHERE task_id = ? ORDER BY pushed_at DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                db.close()
                # Send as new message (editing requires message_id which we'd need to store)
                ch.send_text(ai_section)
            except Exception as e:
                print(f"[executor] AI push edit failed: {e}")
    except Exception as e:
        print(f"[executor] AI analysis failed: {e}")
```

Then in both `_exec_watchlist_signal` and `_exec_market_scan`, after `_record_signals(...)`, add:

```python
    if "ai_analysis" in actions:
        import threading
        t = threading.Thread(target=_run_ai_and_edit, args=(task_id, overlaps, channel, message), daemon=True)
        t.start()
```

- [ ] **Step 4: Commit**

```bash
git add backend/ai/context_builder.py backend/actions/__init__.py backend/actions/ai_analysis.py backend/tasks/executor.py
git commit -m "feat: add AI context builder, analysis action, and executor integration"
```

---

### Task 4: SSE Streaming Endpoint

**Files:**
- Modify: `backend/api/ai.py`

- [ ] **Step 1: Add streaming analysis endpoint**

Add this route to `backend/api/ai.py`:

```python
@router.post("/analyze/{signal_id}")
def analyze_signal(signal_id: int):
    """Stream AI analysis for a signal via SSE."""
    from ai.llm_client import LLMClient
    from ai.context_builder import build_context

    conf = get_ai_config()
    if not conf["api_key"]:
        raise HTTPException(400, "API Key not configured")

    strategy = get_default_strategy()
    context = build_context(signal_id)
    if not context:
        raise HTTPException(404, "Signal not found or no context available")

    messages = [{"role": "system", "content": strategy["system_prompt"]}] + context

    def generate():
        full_text = []
        try:
            client = LLMClient.from_config()
            for chunk in client.stream_chat(messages):
                full_text.append(chunk)
                yield f"data: {json.dumps({'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

        # Save completed analysis
        analysis = "".join(full_text)
        if analysis:
            sentiment = "neutral"
            lower = analysis.lower()
            if any(w in lower for w in ["看涨", "偏多", "bullish", "上涨"]):
                sentiment = "bullish"
            elif any(w in lower for w in ["看跌", "偏空", "bearish", "下跌"]):
                sentiment = "bearish"

            db = get_db(settings.db_path)
            db.execute(
                "INSERT INTO ai_analyses (signal_id, strategy_id, analysis_text, sentiment) VALUES (?, ?, ?, ?)",
                (signal_id, strategy["id"], analysis, sentiment),
            )
            db.commit()
            db.close()

        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

- [ ] **Step 2: Commit**

```bash
git add backend/api/ai.py
git commit -m "feat: add SSE streaming AI analysis endpoint"
```

---

### Task 5: Frontend — AI Analysis Page

**Files:**
- Modify: `frontend/src/api/client.js`
- Create: `frontend/src/views/AI.vue`
- Modify: `frontend/src/router/index.js`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Add AI methods to API client**

Add to `api` object in `frontend/src/api/client.js`:

```js
  async getAIConfig() {
    return request('/ai/config')
  },

  async updateAIConfig(data) {
    return request('/ai/config', { method: 'PUT', body: JSON.stringify(data) })
  },

  async testAIConnection() {
    return request('/ai/test', { method: 'POST' })
  },

  async getAISignals() {
    return request('/ai/signals')
  },

  async getSignalDetail(id) {
    return request(`/ai/signals/${id}`)
  },

  async listStrategies() {
    return request('/ai/strategies')
  },

  async createStrategy(data) {
    return request('/ai/strategies', { method: 'POST', body: JSON.stringify(data) })
  },

  async updateStrategy(id, data) {
    return request(`/ai/strategies/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  },

  async deleteStrategy(id) {
    return request(`/ai/strategies/${id}`, { method: 'DELETE' })
  },

  async setDefaultStrategy(id) {
    return request(`/ai/strategies/${id}/default`, { method: 'POST' })
  },
```

- [ ] **Step 2: Create AI.vue**

Create `frontend/src/views/AI.vue`:

```vue
<template>
  <div>
    <div class="page-header">
      <h1>信号分析</h1>
      <p>AI 驱动的技术信号解读</p>
    </div>

    <div class="ai-layout">
      <!-- Signal List -->
      <div class="signal-list card">
        <h3 class="list-title">最近信号</h3>
        <div v-if="!signals.length" class="list-empty">暂无信号记录</div>
        <div
          v-for="s in signals"
          :key="s.id"
          class="signal-item"
          :class="{ active: selected?.id === s.id }"
          @click="selectSignal(s)"
        >
          <div class="signal-symbol">{{ s.symbol }}</div>
          <div class="signal-meta">
            <span>{{ s.indicator }}</span>
            <span>{{ s.timeframe }}</span>
            <span class="signal-time">{{ formatTime(s.triggered_at) }}</span>
          </div>
          <span v-if="s.has_analysis" class="badge badge-success" style="font-size: 11px">已分析</span>
        </div>
      </div>

      <!-- Analysis Panel -->
      <div class="analysis-panel card">
        <div v-if="!selected" class="empty-state" style="padding: 40px">
          <h3>选择一个信号</h3>
          <p>从左侧列表选择信号进行 AI 分析</p>
        </div>

        <div v-else>
          <!-- Signal Info -->
          <div class="signal-info-card">
            <div class="info-row">
              <span class="info-label">币种</span>
              <span class="info-value">{{ detail?.signal?.symbol }} ({{ detail?.signal?.exchange }})</span>
            </div>
            <div class="info-row">
              <span class="info-label">指标</span>
              <span class="info-value">{{ detail?.signal?.indicator }} / {{ detail?.signal?.timeframe }}</span>
            </div>
            <div v-if="detail?.snapshot" class="info-row">
              <span class="info-label">触发价格</span>
              <span class="info-value">{{ detail.snapshot.price }}</span>
            </div>
            <div v-if="detail?.snapshot" class="info-row">
              <span class="info-label">24h涨跌</span>
              <span class="info-value" :class="detail.snapshot.change_24h > 0 ? 'clr-positive' : 'clr-negative'">
                {{ detail.snapshot.change_24h?.toFixed(2) }}%
              </span>
            </div>
            <div v-if="detail?.history?.length" class="info-row">
              <span class="info-label">历史触发</span>
              <span class="info-value">{{ detail.history.length }} 次记录</span>
            </div>
          </div>

          <!-- AI Analysis -->
          <div class="analysis-section">
            <div class="analysis-header">
              <h3>🤖 AI 分析</h3>
              <button class="btn btn-sm" @click="runAnalysis" :disabled="streaming">
                {{ streaming ? '分析中...' : (selected.has_analysis ? '重新分析' : '生成分析') }}
              </button>
            </div>

            <div v-if="analysisText" class="analysis-content">
              <div class="analysis-text" v-html="renderMarkdown(analysisText)"></div>
              <div v-if="sentiment" class="sentiment-badge">
                <span class="badge" :class="sentimentClass">{{ sentimentLabel }}</span>
              </div>
            </div>

            <div v-if="!analysisText && !streaming" class="analysis-empty">
              点击"生成分析"让 AI 解读此信号
            </div>

            <div v-if="analysisError" class="analysis-error">{{ analysisError }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api/client.js'

const signals = ref([])
const selected = ref(null)
const detail = ref(null)
const analysisText = ref('')
const sentiment = ref('')
const streaming = ref(false)
const analysisError = ref('')

const sentimentClass = computed(() => ({
  'badge-success': sentiment.value === 'bullish',
  'badge-danger': sentiment.value === 'bearish',
}))

const sentimentLabel = computed(() => {
  if (sentiment.value === 'bullish') return '看涨'
  if (sentiment.value === 'bearish') return '看跌'
  return '中性'
})

function formatTime(t) {
  if (!t) return ''
  return t.replace('T', ' ').substring(5, 16)
}

function renderMarkdown(text) {
  return text
    .replace(/\n/g, '<br>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/⚠️/g, '<span style="color:var(--warning)">⚠️</span>')
}

async function loadSignals() {
  try {
    signals.value = await api.getAISignals()
  } catch {}
}

async function selectSignal(s) {
  selected.value = s
  analysisText.value = s.analysis_text || ''
  sentiment.value = s.sentiment || ''
  analysisError.value = ''

  try {
    detail.value = await api.getSignalDetail(s.id)
    if (detail.value.analysis) {
      analysisText.value = detail.value.analysis.text
      sentiment.value = detail.value.analysis.sentiment
    }
  } catch {}
}

async function runAnalysis() {
  if (!selected.value) return
  streaming.value = true
  analysisText.value = ''
  sentiment.value = ''
  analysisError.value = ''

  try {
    const resp = await fetch(`/api/ai/analyze/${selected.value.id}`, { method: 'POST' })
    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const payload = line.slice(6).trim()
        if (payload === '[DONE]') continue
        try {
          const data = JSON.parse(payload)
          if (data.error) {
            analysisError.value = data.error
          } else if (data.text) {
            analysisText.value += data.text
          }
        } catch {}
      }
    }

    // Detect sentiment from final text
    const lower = analysisText.value.toLowerCase()
    if (['看涨', '偏多', 'bullish'].some(w => lower.includes(w))) sentiment.value = 'bullish'
    else if (['看跌', '偏空', 'bearish'].some(w => lower.includes(w))) sentiment.value = 'bearish'
    else sentiment.value = 'neutral'

    // Mark as analyzed in list
    if (selected.value) selected.value.has_analysis = true
  } catch (e) {
    analysisError.value = '分析失败: ' + e.message
  } finally {
    streaming.value = false
  }
}

onMounted(loadSignals)
</script>

<style scoped>
.ai-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 20px;
  min-height: 500px;
}

.signal-list {
  overflow-y: auto;
  max-height: 70vh;
  padding: 16px;
}

.list-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 12px;
}

.list-empty {
  color: var(--text-tertiary);
  font-size: 13px;
  text-align: center;
  padding: 20px;
}

.signal-item {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--transition-fast);
  margin-bottom: 4px;
}

.signal-item:hover {
  background: var(--bg-tertiary);
}

.signal-item.active {
  background: var(--accent-subtle);
}

.signal-symbol {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 4px;
}

.signal-meta {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: var(--text-tertiary);
}

.signal-time {
  margin-left: auto;
}

.analysis-panel {
  padding: 24px;
}

.signal-info-card {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 16px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  margin-bottom: 20px;
}

.info-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.info-label {
  font-size: 11px;
  color: var(--text-tertiary);
  font-weight: 600;
  letter-spacing: 0.03em;
}

.info-value {
  font-size: 14px;
  font-weight: 500;
}

.analysis-section {
  margin-top: 8px;
}

.analysis-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.analysis-header h3 {
  font-size: 16px;
}

.analysis-content {
  padding: 16px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  line-height: 1.7;
  font-size: 14px;
}

.analysis-text {
  white-space: pre-wrap;
}

.sentiment-badge {
  margin-top: 12px;
}

.analysis-empty {
  color: var(--text-tertiary);
  font-size: 14px;
  text-align: center;
  padding: 32px;
}

.analysis-error {
  color: var(--danger);
  font-size: 13px;
  margin-top: 12px;
}

.clr-positive { color: var(--success); }
.clr-negative { color: var(--danger); }
</style>
```

- [ ] **Step 3: Add route and nav item**

In `frontend/src/router/index.js`, add:

```js
import AI from '../views/AI.vue'
```

And in the routes array, after the settings route:

```js
  { path: '/ai', component: AI },
```

In `frontend/src/App.vue`, add a new nav item to the `navItems` array (between Channels and Settings):

```js
  {
    path: '/ai',
    label: '信号分析',
    icon: '<path d="M12 2a4 4 0 0 1 4 4v1a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V6a4 4 0 0 1 4-4z" /><path d="M16 11a4 4 0 0 0-8 0v5a4 4 0 0 0 8 0v-5z" /><line x1="12" y1="20" x2="12" y2="22" />',
  },
```

- [ ] **Step 4: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.js frontend/src/views/AI.vue frontend/src/router/index.js frontend/src/App.vue
git commit -m "feat: add AI signal analysis page with streaming typewriter effect"
```

---

### Task 6: Settings Page — AI Config + Strategy Management

**Files:**
- Modify: `frontend/src/views/Settings.vue`

- [ ] **Step 1: Add AI config and strategy management sections to Settings.vue**

Read current `frontend/src/views/Settings.vue` and append two new sections after the ChartShot section:

**AI Configuration section:**
```html
    <!-- AI Configuration -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">AI 配置</h3>
        <span class="badge" :class="aiHasKey ? 'badge-success' : 'badge-danger'">
          {{ aiHasKey ? '已配置' : '未配置' }}
        </span>
      </div>
      <p class="section-desc">接入 OpenAI 兼容 API，用于信号分析和技术解读。</p>
      <div class="form-row">
        <div class="form-group">
          <label>API Key</label>
          <input type="password" v-model="aiKeyInput" placeholder="sk-..." />
        </div>
        <div class="form-group">
          <label>Base URL</label>
          <input v-model="aiBaseUrl" placeholder="https://api.openai.com/v1" />
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>模型</label>
          <input v-model="aiModel" placeholder="gpt-4o" />
        </div>
        <div class="form-group">
          <label>Max Tokens</label>
          <input type="number" v-model.number="aiMaxTokens" />
        </div>
      </div>
      <div class="btn-row">
        <button class="btn btn-primary btn-sm" @click="saveAIConfig">保存</button>
        <button class="btn btn-sm" @click="testAI">测试连接</button>
      </div>
      <div v-if="aiMsg" class="action-msg" :class="aiOk ? 'msg-ok' : 'msg-fail'">{{ aiMsg }}</div>
    </div>

    <!-- Strategy Management -->
    <div class="card section">
      <div class="section-header">
        <h3 class="section-title">分析策略</h3>
        <button class="btn btn-sm" @click="showNewStrategy = !showNewStrategy">新建</button>
      </div>
      <p class="section-desc">管理 AI 分析的 System Prompt，不同策略影响分析角度和风格。</p>

      <div v-if="showNewStrategy" class="strategy-form">
        <div class="form-group">
          <label>策略名称</label>
          <input v-model="newStrategyName" placeholder="例如：激进短线分析" />
        </div>
        <div class="form-group">
          <label>System Prompt</label>
          <textarea v-model="newStrategyPrompt" rows="6" placeholder="输入系统提示词..."></textarea>
        </div>
        <div class="btn-row">
          <button class="btn btn-primary btn-sm" @click="createStrategy">创建</button>
          <button class="btn btn-sm" @click="showNewStrategy = false">取消</button>
        </div>
      </div>

      <div v-for="s in strategies" :key="s.id" class="strategy-item">
        <div class="strategy-header">
          <span class="strategy-name">{{ s.name }}</span>
          <span v-if="s.is_default" class="badge badge-success">默认</span>
          <div class="strategy-actions">
            <button v-if="!s.is_default" class="btn btn-sm" @click="setDefault(s)">设为默认</button>
            <button v-if="!s.is_default" class="btn btn-sm" style="color:var(--danger)" @click="deleteStrat(s)">删除</button>
          </div>
        </div>
        <div v-if="editingStrategy === s.id">
          <textarea v-model="editPrompt" rows="6" style="margin-top: 8px"></textarea>
          <div class="btn-row" style="margin-top: 8px">
            <button class="btn btn-primary btn-sm" @click="saveStrategyEdit(s)">保存</button>
            <button class="btn btn-sm" @click="editingStrategy = null">取消</button>
          </div>
        </div>
        <div v-else class="strategy-preview" @click="startEditStrategy(s)">
          {{ s.system_prompt.substring(0, 120) }}{{ s.system_prompt.length > 120 ? '...' : '' }}
          <span class="edit-hint">点击编辑</span>
        </div>
      </div>
    </div>
```

**Add corresponding script variables and functions** to the `<script setup>`:

```js
// AI Config
const aiHasKey = ref(false)
const aiKeyInput = ref('')
const aiBaseUrl = ref('https://api.openai.com/v1')
const aiModel = ref('gpt-4o')
const aiMaxTokens = ref(1000)
const aiMsg = ref('')
const aiOk = ref(false)

// Strategies
const strategies = ref([])
const showNewStrategy = ref(false)
const newStrategyName = ref('')
const newStrategyPrompt = ref('')
const editingStrategy = ref(null)
const editPrompt = ref('')

async function loadAIConfig() {
  try {
    const conf = await api.getAIConfig()
    aiHasKey.value = conf.has_key || false
    aiBaseUrl.value = conf.base_url || 'https://api.openai.com/v1'
    aiModel.value = conf.model || 'gpt-4o'
    aiMaxTokens.value = conf.max_tokens || 1000
  } catch {}
}

async function saveAIConfig() {
  aiMsg.value = ''
  const data = { base_url: aiBaseUrl.value, model: aiModel.value, max_tokens: aiMaxTokens.value }
  if (aiKeyInput.value) data.api_key = aiKeyInput.value
  try {
    await api.updateAIConfig(data)
    aiMsg.value = '保存成功'
    aiOk.value = true
    aiKeyInput.value = ''
    await loadAIConfig()
  } catch (e) {
    aiMsg.value = '保存失败: ' + e.message
    aiOk.value = false
  }
}

async function testAI() {
  aiMsg.value = '测试中...'
  try {
    const res = await api.testAIConnection()
    aiMsg.value = res.ok ? `连接成功，${res.models_count} 个模型可用` : ('连接失败: ' + res.error)
    aiOk.value = res.ok
  } catch (e) {
    aiMsg.value = '测试失败: ' + e.message
    aiOk.value = false
  }
}

async function loadStrategies() {
  try { strategies.value = await api.listStrategies() } catch {}
}

async function createStrategy() {
  await api.createStrategy({ name: newStrategyName.value, system_prompt: newStrategyPrompt.value })
  showNewStrategy.value = false
  newStrategyName.value = ''
  newStrategyPrompt.value = ''
  await loadStrategies()
}

function startEditStrategy(s) {
  editingStrategy.value = s.id
  editPrompt.value = s.system_prompt
}

async function saveStrategyEdit(s) {
  await api.updateStrategy(s.id, { system_prompt: editPrompt.value })
  editingStrategy.value = null
  await loadStrategies()
}

async function setDefault(s) {
  await api.setDefaultStrategy(s.id)
  await loadStrategies()
}

async function deleteStrat(s) {
  if (!confirm(`确认删除策略 "${s.name}"？`)) return
  await api.deleteStrategy(s.id)
  await loadStrategies()
}
```

Add calls to `onMounted`:

```js
onMounted(() => {
  loadInfo()
  loadPineCookies()
  loadChartshotStatus()
  loadAIConfig()
  loadStrategies()
})
```

**Add scoped styles:**

```css
.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.form-group label {
  display: block;
  margin-bottom: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.strategy-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}

.strategy-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.strategy-name {
  font-weight: 600;
  font-size: 14px;
}

.strategy-actions {
  margin-left: auto;
  display: flex;
  gap: 6px;
}

.strategy-preview {
  margin-top: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  line-height: 1.5;
}

.strategy-preview:hover {
  color: var(--text-primary);
}

.edit-hint {
  color: var(--accent);
  font-size: 12px;
  margin-left: 8px;
}

.strategy-form {
  padding: 16px;
  background: var(--bg-primary);
  border-radius: var(--radius-md);
  margin-bottom: 16px;
}
```

- [ ] **Step 2: Verify build**

```bash
cd frontend && npm run build
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Settings.vue
git commit -m "feat: add AI config and strategy management to Settings page"
```

---

### Task 7: Integration Verification

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && source .venv/Scripts/activate && python -m pytest -v -m "not network"
```

Expected: 66+ passed

- [ ] **Step 2: Build and deploy**

```bash
cd /c/Users/real/Desktop/WoHub && docker compose up -d --build
```

- [ ] **Step 3: Verify AI API**

```bash
# Get AI config
curl -s http://localhost:8080/api/ai/config | python -m json.tool

# List strategies
curl -s http://localhost:8080/api/ai/strategies | python -m json.tool

# Get signals
curl -s http://localhost:8080/api/ai/signals | python -m json.tool
```

- [ ] **Step 4: Verify frontend**

Open http://localhost:8080:
- Settings → AI 配置区域可见，可配置 API Key / Base URL / Model
- Settings → 策略管理区域，默认策略已创建，可编辑 Prompt
- 侧边栏新增"信号分析"入口
- AI 页面显示信号列表（如有）
- 选择信号后可点击"生成分析"触发 SSE 流式输出

- [ ] **Step 5: Stop containers**

```bash
docker compose down
```

---

## Phase 2 Deliverables

1. **OpenAI 兼容 LLM Client** with streaming (httpx)
2. **AI Config API** (key/base_url/model/max_tokens) with Settings UI
3. **Context Builder** assembling signal + snapshot + history + screenshot for LLM
4. **SSE Streaming Endpoint** `/api/ai/analyze/{signal_id}` with typewriter effect
5. **AI Analysis Page** (signal list + detail + streaming analysis)
6. **Strategy Prompt Management** (CRUD + default + inline editor)
7. **Executor Integration** (`ai_analysis` action → LLM → edit push message)
8. **3 new DB tables** (ai_config, strategies, ai_analyses)
