# WoHub Phase 1d: Push Channels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a unified push channel system — Telegram module with text/photo sending, channel CRUD API, and a management UI where users can add, edit, test, and remove push channels.

**Architecture:** Push channels are stored in the `channels` SQLite table (created in Phase 1a). Each channel has a `type` (telegram/discord/webhook) and `config_json` with type-specific settings. The Telegram module uses `httpx` for API calls. The Channels.vue page provides full CRUD with test-push functionality.

**Tech Stack:** Python 3.13 / FastAPI / httpx / SQLite / Vue 3

---

## File Map

### Backend — New Files

| File | Responsibility |
|------|---------------|
| `backend/channels/__init__.py` | Empty package init |
| `backend/channels/telegram.py` | Telegram Bot API: send text, send photo, edit message |
| `backend/channels/sender.py` | Unified send interface dispatching to channel type |
| `backend/api/channels.py` | Channel CRUD + test-push API routes |
| `backend/tests/test_telegram.py` | Telegram module tests (mocked) |
| `backend/tests/test_channels_api.py` | Channel API endpoint tests |

### Backend — Modified Files

| File | Change |
|------|--------|
| `backend/api/__init__.py` | Register channels router |
| `backend/pyproject.toml` | Add `httpx` to dependencies |

### Frontend — Modified Files

| File | Change |
|------|--------|
| `frontend/src/views/Channels.vue` | Full channel management UI |
| `frontend/src/api/client.js` | Add channel API methods |

---

### Task 1: Telegram Push Module

**Files:**
- Modify: `backend/pyproject.toml` (add httpx)
- Create: `backend/channels/__init__.py`
- Create: `backend/channels/telegram.py`
- Create: `backend/tests/test_telegram.py`

- [ ] **Step 1: Add httpx to dependencies**

Add `"httpx>=0.27.0"` to dependencies in `backend/pyproject.toml`:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.30.0",
    "python-multipart>=0.0.9",
    "itsdangerous>=2.2.0",
    "requests>=2.31.0",
    "httpx>=0.27.0",
]
```

Install: `cd backend && source .venv/Scripts/activate && pip install -e ".[dev]"`

- [ ] **Step 2: Write tests**

Create `backend/tests/test_telegram.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from channels.telegram import TelegramChannel


@pytest.fixture
def tg():
    return TelegramChannel(bot_token="fake-token", chat_id="-100123")


def _mock_response(ok=True, message_id=42):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "ok": ok,
        "result": {"message_id": message_id},
    }
    resp.raise_for_status = MagicMock()
    return resp


def test_send_text(tg):
    with patch("channels.telegram.httpx.post", return_value=_mock_response()) as mock:
        msg_id = tg.send_text("Hello")
        assert msg_id == 42
        call_args = mock.call_args
        assert "/sendMessage" in call_args[0][0]
        body = call_args[1]["json"]
        assert body["text"] == "Hello"
        assert body["chat_id"] == "-100123"
        assert body["parse_mode"] == "HTML"


def test_send_text_custom_chat(tg):
    with patch("channels.telegram.httpx.post", return_value=_mock_response()):
        msg_id = tg.send_text("Test", chat_id="-999")
        assert msg_id == 42


def test_send_text_failure(tg):
    resp = _mock_response(ok=False)
    resp.json.return_value = {"ok": False, "description": "Bad Request"}
    with patch("channels.telegram.httpx.post", return_value=resp):
        with pytest.raises(RuntimeError, match="Telegram"):
            tg.send_text("fail")


def test_send_photo(tg):
    mock_resp = _mock_response()
    with patch("channels.telegram.httpx.post", return_value=mock_resp) as mock:
        with patch("builtins.open", MagicMock()):
            msg_id = tg.send_photo("/fake/path.png", caption="Test")
            assert msg_id == 42
            assert "/sendPhoto" in mock.call_args[0][0]


def test_test_connection(tg):
    with patch("channels.telegram.httpx.get") as mock:
        mock.return_value = MagicMock(
            json=MagicMock(return_value={
                "ok": True,
                "result": {"username": "test_bot", "first_name": "Test"},
            })
        )
        result = tg.test_connection()
        assert result["ok"] is True
        assert result["bot_name"] == "Test"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/test_telegram.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'channels'`

- [ ] **Step 4: Implement Telegram module**

Create `backend/channels/__init__.py` (empty file).

Create `backend/channels/telegram.py`:

```python
import httpx
from pathlib import Path

TIMEOUT = 30


class TelegramChannel:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._base = f"https://api.telegram.org/bot{bot_token}"

    def _resolve_chat(self, chat_id=None):
        return chat_id or self.chat_id

    def _check_response(self, resp, method):
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description', data)}")
        return data

    def send_text(self, text: str, chat_id: str = None, parse_mode: str = "HTML") -> int:
        resp = httpx.post(
            f"{self._base}/sendMessage",
            json={
                "chat_id": self._resolve_chat(chat_id),
                "text": text,
                "parse_mode": parse_mode,
            },
            timeout=TIMEOUT,
        )
        data = self._check_response(resp, "sendMessage")
        return data["result"]["message_id"]

    def edit_text(self, message_id: int, text: str, chat_id: str = None) -> None:
        resp = httpx.post(
            f"{self._base}/editMessageText",
            json={
                "chat_id": self._resolve_chat(chat_id),
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=TIMEOUT,
        )
        self._check_response(resp, "editMessageText")

    def send_photo(self, photo_path: str, caption: str = "", chat_id: str = None) -> int:
        with open(photo_path, "rb") as f:
            resp = httpx.post(
                f"{self._base}/sendPhoto",
                data={
                    "chat_id": self._resolve_chat(chat_id),
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={"photo": ("chart.png", f, "image/png")},
                timeout=60,
            )
        data = self._check_response(resp, "sendPhoto")
        return data["result"]["message_id"]

    def edit_photo(self, message_id: int, photo_path: str, caption: str = "", chat_id: str = None) -> None:
        import json
        media = json.dumps({
            "type": "photo",
            "media": "attach://photo",
            "caption": caption,
            "parse_mode": "HTML",
        })
        with open(photo_path, "rb") as f:
            resp = httpx.post(
                f"{self._base}/editMessageMedia",
                data={
                    "chat_id": self._resolve_chat(chat_id),
                    "message_id": message_id,
                    "media": media,
                },
                files={"photo": ("chart.png", f, "image/png")},
                timeout=60,
            )
        self._check_response(resp, "editMessageMedia")

    def delete_message(self, message_id: int, chat_id: str = None) -> None:
        resp = httpx.post(
            f"{self._base}/deleteMessage",
            json={
                "chat_id": self._resolve_chat(chat_id),
                "message_id": message_id,
            },
            timeout=TIMEOUT,
        )
        self._check_response(resp, "deleteMessage")

    def test_connection(self) -> dict:
        try:
            resp = httpx.get(f"{self._base}/getMe", timeout=10)
            data = resp.json()
            if data.get("ok"):
                bot = data["result"]
                return {"ok": True, "bot_name": bot.get("first_name", ""), "username": bot.get("username", "")}
            return {"ok": False, "error": data.get("description", "Unknown error")}
        except Exception as e:
            return {"ok": False, "error": str(e)}
```

- [ ] **Step 5: Run tests**

```bash
cd backend && python -m pytest tests/test_telegram.py -v
```

Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/channels/__init__.py backend/channels/telegram.py backend/tests/test_telegram.py
git commit -m "feat: add Telegram push channel module"
```

---

### Task 2: Channel Sender Dispatcher

**Files:**
- Create: `backend/channels/sender.py`

- [ ] **Step 1: Create `backend/channels/sender.py`**

```python
import json
from channels.telegram import TelegramChannel


def create_channel(channel_type: str, config: dict):
    if channel_type == "telegram":
        return TelegramChannel(
            bot_token=config.get("bot_token", ""),
            chat_id=config.get("chat_id", ""),
        )
    raise ValueError(f"Unsupported channel type: {channel_type}")


def send_text(channel_type: str, config: dict, text: str) -> int:
    ch = create_channel(channel_type, config)
    return ch.send_text(text)


def send_photo(channel_type: str, config: dict, photo_path: str, caption: str = "") -> int:
    ch = create_channel(channel_type, config)
    return ch.send_photo(photo_path, caption)


def test_channel(channel_type: str, config: dict) -> dict:
    ch = create_channel(channel_type, config)
    return ch.test_connection()
```

- [ ] **Step 2: Commit**

```bash
git add backend/channels/sender.py
git commit -m "feat: add channel sender dispatcher"
```

---

### Task 3: Channel CRUD API

**Files:**
- Create: `backend/api/channels.py`
- Modify: `backend/api/__init__.py`
- Create: `backend/tests/test_channels_api.py`

- [ ] **Step 1: Write tests**

Create `backend/tests/test_channels_api.py`:

```python
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_create_channel(client):
    resp = await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Test Group",
        "config": {"bot_token": "123:ABC", "chat_id": "-100999"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] > 0
    assert data["name"] == "Test Group"
    assert data["type"] == "telegram"


@pytest.mark.asyncio
async def test_list_channels(client):
    await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Group A",
        "config": {"bot_token": "t1", "chat_id": "c1"},
    })
    await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Group B",
        "config": {"bot_token": "t2", "chat_id": "c2"},
    })
    resp = await client.get("/api/channels")
    assert resp.status_code == 200
    channels = resp.json()
    assert len(channels) >= 2


@pytest.mark.asyncio
async def test_update_channel(client):
    create = await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Old Name",
        "config": {"bot_token": "t", "chat_id": "c"},
    })
    ch_id = create.json()["id"]

    resp = await client.put(f"/api/channels/{ch_id}", json={
        "name": "New Name",
        "enabled": False,
    })
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_delete_channel(client):
    create = await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Temp",
        "config": {"bot_token": "t", "chat_id": "c"},
    })
    ch_id = create.json()["id"]

    resp = await client.delete(f"/api/channels/{ch_id}")
    assert resp.status_code == 200

    listing = await client.get("/api/channels")
    ids = [c["id"] for c in listing.json()]
    assert ch_id not in ids


@pytest.mark.asyncio
async def test_test_channel(client):
    create = await client.post("/api/channels", json={
        "type": "telegram",
        "name": "Test",
        "config": {"bot_token": "fake", "chat_id": "-100"},
    })
    ch_id = create.json()["id"]

    with patch("api.channels.test_channel", return_value={"ok": True, "bot_name": "TestBot"}):
        resp = await client.post(f"/api/channels/{ch_id}/test")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
```

- [ ] **Step 2: Implement channel API**

Create `backend/api/channels.py`:

```python
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db
from config import settings
from channels.sender import test_channel

router = APIRouter(prefix="/channels")


class ChannelCreate(BaseModel):
    type: str
    name: str
    config: dict


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None


@router.get("")
def list_channels():
    db = get_db(settings.db_path)
    rows = db.execute("SELECT * FROM channels ORDER BY created_at DESC").fetchall()
    db.close()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "type": r["type"],
            "name": r["name"],
            "config": json.loads(r["config_json"]),
            "enabled": bool(r["enabled"]),
            "created_at": r["created_at"],
        })
    return result


@router.post("")
def create_channel(body: ChannelCreate):
    if body.type not in ("telegram", "discord", "webhook"):
        raise HTTPException(400, f"Unsupported type: {body.type}")

    db = get_db(settings.db_path)
    cursor = db.execute(
        "INSERT INTO channels (type, name, config_json) VALUES (?, ?, ?)",
        (body.type, body.name, json.dumps(body.config)),
    )
    db.commit()
    ch_id = cursor.lastrowid
    row = db.execute("SELECT * FROM channels WHERE id = ?", (ch_id,)).fetchone()
    db.close()
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "config": json.loads(row["config_json"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


@router.put("/{channel_id}")
def update_channel(channel_id: int, body: ChannelUpdate):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Channel not found")

    updates = []
    params = []
    if body.name is not None:
        updates.append("name = ?")
        params.append(body.name)
    if body.config is not None:
        updates.append("config_json = ?")
        params.append(json.dumps(body.config))
    if body.enabled is not None:
        updates.append("enabled = ?")
        params.append(int(body.enabled))

    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(channel_id)
        db.execute(f"UPDATE channels SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()

    row = db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    db.close()
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "config": json.loads(row["config_json"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


@router.delete("/{channel_id}")
def delete_channel(channel_id: int):
    db = get_db(settings.db_path)
    db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    db.commit()
    db.close()
    return {"ok": True}


@router.post("/{channel_id}/test")
def test_push(channel_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Channel not found")

    config = json.loads(row["config_json"])
    result = test_channel(row["type"], config)
    return result
```

- [ ] **Step 3: Register channels router in `backend/api/__init__.py`**

```python
from fastapi import APIRouter
from api.health import router as health_router
from auth import router as auth_router
from api.market import router as market_router
from api.channels import router as channels_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(market_router)
api_router.include_router(channels_router)
```

- [ ] **Step 4: Run tests**

```bash
cd backend && python -m pytest tests/test_channels_api.py -v
```

Expected: 5 passed

- [ ] **Step 5: Run all non-network tests**

```bash
cd backend && python -m pytest -v -m "not network"
```

Expected: all pass (29 + 5 telegram + 5 channels API = 39)

- [ ] **Step 6: Commit**

```bash
git add backend/api/channels.py backend/api/__init__.py backend/tests/test_channels_api.py
git commit -m "feat: add channel CRUD API with test-push endpoint"
```

---

### Task 4: Frontend API Client + Channels.vue

**Files:**
- Modify: `frontend/src/api/client.js`
- Replace: `frontend/src/views/Channels.vue`

- [ ] **Step 1: Add channel methods to API client**

Add these to the `api` object in `frontend/src/api/client.js`:

```js
  async listChannels() {
    return request('/channels')
  },

  async createChannel(data) {
    return request('/channels', { method: 'POST', body: JSON.stringify(data) })
  },

  async updateChannel(id, data) {
    return request(`/channels/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  },

  async deleteChannel(id) {
    return request(`/channels/${id}`, { method: 'DELETE' })
  },

  async testChannel(id) {
    return request(`/channels/${id}/test`, { method: 'POST' })
  },
```

- [ ] **Step 2: Replace `frontend/src/views/Channels.vue`**

```vue
<template>
  <div>
    <div class="page-header">
      <h1>推送通道</h1>
      <p>管理 Telegram、Discord 等消息推送渠道</p>
    </div>

    <button class="btn btn-primary" @click="showForm = true" style="margin-bottom: 24px">
      添加通道
    </button>

    <!-- Add/Edit Form -->
    <div v-if="showForm" class="card" style="margin-bottom: 24px">
      <h3 style="margin-bottom: 16px">{{ editing ? '编辑通道' : '添加通道' }}</h3>
      <form @submit.prevent="saveChannel">
        <div class="form-row">
          <div class="form-group">
            <label>名称</label>
            <input v-model="form.name" placeholder="例如：交易信号群" required />
          </div>
          <div class="form-group">
            <label>类型</label>
            <select v-model="form.type" :disabled="!!editing">
              <option value="telegram">Telegram</option>
              <option value="discord" disabled>Discord (即将支持)</option>
              <option value="webhook" disabled>Webhook (即将支持)</option>
            </select>
          </div>
        </div>

        <div v-if="form.type === 'telegram'">
          <div class="form-group">
            <label>Bot Token</label>
            <input v-model="form.config.bot_token" placeholder="从 @BotFather 获取" required />
          </div>
          <div class="form-group">
            <label>Chat ID</label>
            <input v-model="form.config.chat_id" placeholder="群组或用户 ID" required />
          </div>
        </div>

        <div class="form-actions">
          <button type="submit" class="btn btn-primary">{{ editing ? '保存' : '添加' }}</button>
          <button type="button" class="btn" @click="cancelForm">取消</button>
        </div>
        <p v-if="formError" class="form-error">{{ formError }}</p>
      </form>
    </div>

    <!-- Channel List -->
    <div v-if="channels.length === 0 && !showForm" class="empty-state card">
      <svg class="empty-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
      </svg>
      <h3>暂无通道</h3>
      <p>添加一个推送通道，将信号发送到 Telegram 群组。</p>
    </div>

    <div v-for="ch in channels" :key="ch.id" class="channel-card card">
      <div class="channel-header">
        <div class="channel-info">
          <span class="channel-name">{{ ch.name }}</span>
          <span class="badge" :class="ch.enabled ? 'badge-success' : 'badge-danger'">
            {{ ch.enabled ? '已启用' : '已停用' }}
          </span>
          <span class="channel-type">{{ ch.type }}</span>
        </div>
        <div class="channel-actions">
          <button class="btn btn-sm" @click="testPush(ch)" :disabled="ch.testing">
            {{ ch.testing ? '测试中...' : '测试' }}
          </button>
          <button class="btn btn-sm" @click="editChannel(ch)">编辑</button>
          <button class="btn btn-sm" @click="toggleEnabled(ch)">
            {{ ch.enabled ? '停用' : '启用' }}
          </button>
          <button class="btn btn-sm" style="color: var(--danger)" @click="removeChannel(ch)">删除</button>
        </div>
      </div>
      <div v-if="ch.testResult" class="test-result" :class="ch.testResult.ok ? 'test-ok' : 'test-fail'">
        {{ ch.testResult.ok ? '连接成功: ' + (ch.testResult.bot_name || '') : '连接失败: ' + (ch.testResult.error || '') }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api/client.js'

const channels = ref([])
const showForm = ref(false)
const editing = ref(null)
const formError = ref('')
const form = ref({
  name: '',
  type: 'telegram',
  config: { bot_token: '', chat_id: '' },
})

async function loadChannels() {
  try {
    channels.value = (await api.listChannels()).map(c => ({
      ...c,
      testing: false,
      testResult: null,
    }))
  } catch (e) {
    console.error('Failed to load channels:', e)
  }
}

function resetForm() {
  form.value = { name: '', type: 'telegram', config: { bot_token: '', chat_id: '' } }
  editing.value = null
  formError.value = ''
}

function cancelForm() {
  showForm.value = false
  resetForm()
}

function editChannel(ch) {
  editing.value = ch.id
  form.value = {
    name: ch.name,
    type: ch.type,
    config: { ...ch.config },
  }
  showForm.value = true
}

async function saveChannel() {
  formError.value = ''
  try {
    if (editing.value) {
      await api.updateChannel(editing.value, {
        name: form.value.name,
        config: form.value.config,
      })
    } else {
      await api.createChannel(form.value)
    }
    showForm.value = false
    resetForm()
    await loadChannels()
  } catch (e) {
    formError.value = '保存失败: ' + e.message
  }
}

async function removeChannel(ch) {
  if (!confirm(`确认删除通道 "${ch.name}"？`)) return
  await api.deleteChannel(ch.id)
  await loadChannels()
}

async function toggleEnabled(ch) {
  await api.updateChannel(ch.id, { enabled: !ch.enabled })
  await loadChannels()
}

async function testPush(ch) {
  ch.testing = true
  ch.testResult = null
  try {
    ch.testResult = await api.testChannel(ch.id)
  } catch (e) {
    ch.testResult = { ok: false, error: e.message }
  } finally {
    ch.testing = false
  }
}

onMounted(loadChannels)
</script>

<style scoped>
.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.form-group {
  margin-bottom: 16px;
}

.form-group label {
  display: block;
  margin-bottom: 6px;
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
}

.form-actions {
  display: flex;
  gap: 12px;
  margin-top: 8px;
}

.form-error {
  color: var(--danger);
  font-size: 13px;
  margin-top: 12px;
}

.channel-card {
  margin-bottom: 12px;
}

.channel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.channel-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.channel-name {
  font-weight: 600;
  font-size: 15px;
}

.channel-type {
  color: var(--text-tertiary);
  font-size: 13px;
}

.channel-actions {
  display: flex;
  gap: 8px;
}

.test-result {
  margin-top: 12px;
  padding: 8px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.test-ok {
  background: var(--success-subtle);
  color: var(--success);
}

.test-fail {
  background: var(--danger-subtle);
  color: var(--danger);
}
</style>
```

- [ ] **Step 3: Verify build**

```bash
cd frontend && npm run build
```

Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.js frontend/src/views/Channels.vue
git commit -m "feat: add channel management UI with CRUD and test-push"
```

---

### Task 5: Integration Verification

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && source .venv/Scripts/activate && python -m pytest -v -m "not network"
```

Expected: 39 passed

- [ ] **Step 2: Build and deploy**

```bash
cd /c/Users/real/Desktop/WoHub && docker compose up -d --build
```

- [ ] **Step 3: Verify channels API**

```bash
# Create a channel
curl -s -X POST http://localhost:8080/api/channels \
  -H "Content-Type: application/json" \
  -d '{"type":"telegram","name":"Test","config":{"bot_token":"fake","chat_id":"-100"}}' | python -m json.tool

# List channels
curl -s http://localhost:8080/api/channels | python -m json.tool
```

- [ ] **Step 4: Verify frontend**

Open http://localhost:8080, login, go to Channels page. Verify:
- "添加通道" button opens form
- Can fill in Telegram bot token and chat ID
- Can save, edit, delete channels
- Test button calls /test endpoint

- [ ] **Step 5: Stop containers**

```bash
docker compose down
```

---

## Phase 1d Deliverables

1. **Telegram push module** with send_text, send_photo, edit, delete
2. **Channel sender dispatcher** for multi-type support (extensible to Discord/Webhook)
3. **Channel CRUD API** with test-push endpoint
4. **Channels.vue** management UI with add/edit/delete/enable/test
5. **39 total backend tests** (non-network)
