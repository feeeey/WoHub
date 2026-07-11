# LLM 多渠道接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 渠道(provider+base_url+api_key)成为可管理实体,主模型槽与视觉槽各自选「渠道×模型名」,现有单渠道配置无感迁移。

**Architecture:** 新表 `llm_channels`;`agent_config` 加 `channel_id`/`vision_channel_id`(视觉 NULL=跟随主渠道);`build_model(channel, model_name)` 按渠道构建;探测端点按渠道工作;Settings 页加渠道管理卡片。Spec: `docs/superpowers/specs/2026-07-11-llm-multi-channel-design.md`。

**Tech Stack:** FastAPI + SQLite(WAL) + pydantic-ai;Vue 3 + Vite。测试 pytest(后端目录 `backend/`,运行 `cd backend && pytest`)。

## Global Constraints

- `backend/database.py` 的 SCHEMA 是 append-only:改已存在的 CREATE TABLE 体是静默 no-op;已部署库的列变更只能走 `_migrate()` 的幂等 ALTER。
- `backend/agent/` import 链禁止出现任何下单函数(红线)。
- API Key 用 `trading.credentials.encrypt_secret/decrypt_secret`(Fernet)加密,任何 API 响应不得回传明文,只暴露 `has_api_key`。
- key 写入语义全项目统一:`None` = 不改动,`""` = 显式清除,其他字符串 = 更新。
- 旧列 `agent_config.provider/base_url/api_key_enc` 保留为休眠列,代码不再读写。
- 所有后端命令在 `backend/` 目录下执行;pytest 需跳过网络用例时用 `pytest -m "not network"`。

---

### Task 1: DB schema + 启动迁移

**Files:**
- Modify: `backend/database.py`(SCHEMA 末尾 + `_migrate()`)
- Test: `backend/tests/test_llm_channels_schema.py`(新建)

**Interfaces:**
- Produces: 表 `llm_channels(id, name UNIQUE, provider, base_url, api_key_enc, created_at)`;`agent_config` 新列 `channel_id INTEGER`、`vision_channel_id INTEGER`;迁移行为「llm_channels 为空且 agent_config.api_key_enc 非空 → 建『默认渠道』并令 channel_id 指向它,vision_channel_id 留 NULL」。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_llm_channels_schema.py
import os
import sqlite3


def _conn():
    return sqlite3.connect(os.environ["DB_PATH"])


def _cols(table):
    c = _conn()
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    finally:
        c.close()


def test_llm_channels_table_and_agent_config_columns():
    assert {"id", "name", "provider", "base_url", "api_key_enc", "created_at"} <= _cols("llm_channels")
    assert {"channel_id", "vision_channel_id"} <= _cols("agent_config")


def test_migrate_backfills_default_channel_idempotently():
    from database import init_db
    db_path = os.environ["DB_PATH"]
    c = _conn()
    c.execute("INSERT OR IGNORE INTO agent_config (id) VALUES (1)")
    c.execute("UPDATE agent_config SET provider='openai', "
              "base_url='https://openrouter.ai/api/v1', api_key_enc='enc-blob' WHERE id=1")
    c.commit(); c.close()

    init_db(db_path)                                   # 重跑迁移 → 回填
    c = _conn()
    ch = c.execute("SELECT id, name, provider, base_url, api_key_enc FROM llm_channels").fetchall()
    assert len(ch) == 1
    assert ch[0][1] == "默认渠道" and ch[0][3] == "https://openrouter.ai/api/v1" and ch[0][4] == "enc-blob"
    row = c.execute("SELECT channel_id, vision_channel_id FROM agent_config WHERE id=1").fetchone()
    assert row[0] == ch[0][0] and row[1] is None
    c.close()

    init_db(db_path)                                   # 再跑一次 → 幂等,不重复建
    c = _conn()
    assert c.execute("SELECT COUNT(*) FROM llm_channels").fetchone()[0] == 1
    c.close()


def test_no_key_no_backfill():
    from database import init_db
    init_db(os.environ["DB_PATH"])                     # 干净库(conftest 已建),无 key
    c = _conn()
    assert c.execute("SELECT COUNT(*) FROM llm_channels").fetchone()[0] == 0
    c.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && pytest tests/test_llm_channels_schema.py -v`
Expected: FAIL(`llm_channels` 表不存在 / 列缺失)

- [ ] **Step 3: 实现**

`backend/database.py` SCHEMA 字符串末尾(`screener_semantics` 表之后、闭合 `"""` 之前)追加:

```sql
CREATE TABLE IF NOT EXISTS llm_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL DEFAULT 'openai' CHECK (provider IN ('openai', 'anthropic')),
    base_url TEXT NOT NULL DEFAULT '',
    api_key_enc TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

`_migrate()` 整体替换为:

```python
def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for tables that already exist in deployed DBs.
    (SCHEMA 是 append-only：改已存在的 CREATE TABLE 体是静默 no-op。)"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_config)")}
    if "vision_model" not in cols:
        conn.execute("ALTER TABLE agent_config ADD COLUMN vision_model TEXT NOT NULL DEFAULT ''")
    if "channel_id" not in cols:
        conn.execute("ALTER TABLE agent_config ADD COLUMN channel_id INTEGER")
    if "vision_channel_id" not in cols:
        conn.execute("ALTER TABLE agent_config ADD COLUMN vision_channel_id INTEGER")
    # 单渠道旧配置 → 「默认渠道」。仅在 llm_channels 为空时执行（幂等）。
    if conn.execute("SELECT COUNT(*) FROM llm_channels").fetchone()[0] == 0:
        row = conn.execute(
            "SELECT provider, base_url, api_key_enc FROM agent_config WHERE id = 1").fetchone()
        if row and row[2]:
            cur = conn.execute(
                "INSERT INTO llm_channels (name, provider, base_url, api_key_enc) "
                "VALUES ('默认渠道', ?, ?, ?)", (row[0], row[1], row[2]))
            conn.execute("UPDATE agent_config SET channel_id = ? WHERE id = 1",
                         (cur.lastrowid,))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && pytest tests/test_llm_channels_schema.py tests/test_chat_schema.py tests/test_database.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/database.py backend/tests/test_llm_channels_schema.py
git commit -m "feat(db): llm_channels table + agent_config channel columns with default-channel backfill"
```

---

### Task 2: agent/config.py — Channel 实体 + CRUD + 双渠道解析

**Files:**
- Modify: `backend/agent/config.py`
- Test: `backend/tests/test_llm_channels.py`(新建)
- Modify: `backend/tests/helpers.py`(新建,共享测试 helper)

**Interfaces:**
- Consumes: Task 1 的表与列。
- Produces(后续所有任务依赖,签名逐字使用):
  - `@dataclass Channel: id:int, name:str, provider:str, base_url:str, api_key:str|None`(api_key 为解密后明文)
  - `AgentConfig` 字段:`channel_id, vision_channel_id, model, vision_model, max_tokens, max_tool_calls, deep_dive_limit, cooldown_minutes, credential_id, push_verdict, enabled, main_channel: Channel|None, vision_channel: Channel|None`。不再有 `provider/base_url/api_key` 字段。
  - `list_channels() -> list[dict]`(公开形态含 `has_api_key`,无明文)
  - `get_channel(channel_id: int) -> Channel | None`
  - `save_channel(data: dict) -> int`(含 `id` = 更新;key 语义 None/""/值)
  - `channel_in_use(channel_id: int) -> bool`、`delete_channel(channel_id: int) -> None`
  - 解析规则:`vision_channel_id` 为 NULL → `vision_channel = main_channel`;引用了不存在的渠道 → 对应槽为 `None`。
  - `tests/helpers.py` 提供 `save_config_with_channel(**overrides) -> int`(建渠道+指向它,返回 channel id)。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_llm_channels.py
from agent.config import (save_channel, get_channel, list_channels, delete_channel,
                          channel_in_use, save_config, load_config)


def _mk(name="OpenRouter", key="sk-1"):
    return save_channel({"name": name, "provider": "openai",
                         "base_url": "https://openrouter.ai/api/v1", "api_key": key})


def test_channel_crud_key_semantics():
    cid = _mk()
    assert get_channel(cid).api_key == "sk-1"
    save_channel({"id": cid, "name": "OpenRouter", "provider": "openai",
                  "base_url": "", "api_key": None})
    assert get_channel(cid).api_key == "sk-1"       # None = 不改
    assert get_channel(cid).base_url == ""          # 其他字段照常更新
    save_channel({"id": cid, "name": "OpenRouter", "provider": "openai",
                  "base_url": "", "api_key": ""})
    assert get_channel(cid).api_key is None          # "" = 清除
    pub = list_channels()[0]
    assert "api_key" not in pub and "api_key_enc" not in pub
    assert pub["has_api_key"] is False and pub["name"] == "OpenRouter"


def test_get_channel_missing_returns_none():
    assert get_channel(999) is None


def test_load_config_resolves_channels_and_vision_fallback():
    cid = _mk()
    save_config({"channel_id": cid, "model": "m1", "vision_model": "vm", "enabled": True})
    cfg = load_config()
    assert cfg.main_channel.id == cid and cfg.main_channel.api_key == "sk-1"
    assert cfg.vision_channel.id == cid              # vision_channel_id NULL → 跟随主渠道

    cid2 = _mk(name="DashScope", key="sk-2")
    save_config({"vision_channel_id": cid2})
    cfg = load_config()
    assert cfg.vision_channel.id == cid2 and cfg.main_channel.id == cid


def test_load_config_broken_ref_gives_none():
    save_config({"channel_id": 999, "vision_channel_id": 998, "model": "m"})
    cfg = load_config()
    assert cfg.main_channel is None and cfg.vision_channel is None


def test_channel_in_use_and_delete():
    cid = _mk()
    assert channel_in_use(cid) is False
    save_config({"channel_id": cid})
    assert channel_in_use(cid) is True
    save_config({"channel_id": None, "vision_channel_id": cid})
    assert channel_in_use(cid) is True
    save_config({"vision_channel_id": None})
    assert channel_in_use(cid) is False
    delete_channel(cid)
    assert get_channel(cid) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && pytest tests/test_llm_channels.py -v`
Expected: FAIL(ImportError: cannot import name 'save_channel')

- [ ] **Step 3: 实现 —— `backend/agent/config.py` 整文件替换**

```python
# backend/agent/config.py
"""Single-row agent config + llm_channels CRUD. API keys are Fernet-encrypted
with the same SECRET_KEY-derived key as trading credentials — rotating
SECRET_KEY invalidates stored keys (documented operator behavior).
旧列 agent_config.provider/base_url/api_key_enc 已休眠：代码不再读写。"""
from dataclasses import dataclass
from database import get_db
from config import settings
from trading.credentials import encrypt_secret, decrypt_secret

FIELDS = ("channel_id", "vision_channel_id", "model", "vision_model", "max_tokens",
          "max_tool_calls", "deep_dive_limit", "cooldown_minutes", "credential_id",
          "push_verdict", "enabled")
_NULLABLE = {"credential_id", "channel_id", "vision_channel_id"}   # 允许显式置 NULL


@dataclass
class Channel:
    id: int
    name: str
    provider: str
    base_url: str
    api_key: str | None          # 解密后明文；None = 未配置


@dataclass
class AgentConfig:
    channel_id: int | None
    vision_channel_id: int | None
    model: str
    vision_model: str
    max_tokens: int
    max_tool_calls: int
    deep_dive_limit: int
    cooldown_minutes: int
    credential_id: int | None
    push_verdict: bool
    enabled: bool
    main_channel: Channel | None = None
    vision_channel: Channel | None = None    # vision_channel_id NULL 时 = main_channel


def _ensure_row(db):
    db.execute("INSERT OR IGNORE INTO agent_config (id) VALUES (1)")


def _row_to_channel(row) -> Channel:
    return Channel(id=row["id"], name=row["name"], provider=row["provider"],
                   base_url=row["base_url"],
                   api_key=decrypt_secret(row["api_key_enc"]) if row["api_key_enc"] else None)


def _fetch_channel(db, channel_id) -> Channel | None:
    row = db.execute("SELECT * FROM llm_channels WHERE id = ?", (channel_id,)).fetchone()
    return _row_to_channel(row) if row else None


def load_config() -> AgentConfig:
    db = get_db(settings.db_path)
    try:
        _ensure_row(db)
        db.commit()
        row = dict(db.execute("SELECT * FROM agent_config WHERE id = 1").fetchone())
        main = _fetch_channel(db, row["channel_id"]) if row["channel_id"] else None
        # 引用失效 → None；未设置 → 跟随主渠道
        vision = (_fetch_channel(db, row["vision_channel_id"])
                  if row["vision_channel_id"] else main)
    finally:
        db.close()
    return AgentConfig(
        channel_id=row["channel_id"], vision_channel_id=row["vision_channel_id"],
        model=row["model"], vision_model=row["vision_model"],
        max_tokens=row["max_tokens"], max_tool_calls=row["max_tool_calls"],
        deep_dive_limit=row["deep_dive_limit"], cooldown_minutes=row["cooldown_minutes"],
        credential_id=row["credential_id"], push_verdict=bool(row["push_verdict"]),
        enabled=bool(row["enabled"]), main_channel=main, vision_channel=vision)


def save_config(data: dict) -> None:
    """data: FIELDS 子集。_NULLABLE 中的字段允许显式置 None（清除）。"""
    db = get_db(settings.db_path)
    try:
        _ensure_row(db)
        sets, params = [], []
        for f in FIELDS:
            if f not in data:
                continue
            if data[f] is None and f not in _NULLABLE:
                continue
            sets.append(f"{f} = ?")
            v = data[f]
            params.append(int(v) if isinstance(v, bool) else v)
        if sets:
            sets.append("updated_at = datetime('now')")
            db.execute(f"UPDATE agent_config SET {', '.join(sets)} WHERE id = 1", params)
        db.commit()
    finally:
        db.close()


# ---- llm_channels CRUD ----

def list_channels() -> list[dict]:
    """公开形态：含 has_api_key，绝不含明文/密文。"""
    db = get_db(settings.db_path)
    try:
        rows = db.execute("SELECT id, name, provider, base_url, api_key_enc, created_at "
                          "FROM llm_channels ORDER BY id").fetchall()
    finally:
        db.close()
    return [{"id": r["id"], "name": r["name"], "provider": r["provider"],
             "base_url": r["base_url"], "has_api_key": bool(r["api_key_enc"]),
             "created_at": r["created_at"]} for r in rows]


def get_channel(channel_id: int) -> Channel | None:
    db = get_db(settings.db_path)
    try:
        return _fetch_channel(db, channel_id)
    finally:
        db.close()


def save_channel(data: dict) -> int:
    """data: name/provider/base_url + api_key（None=不改, ""=清除, 值=更新）。
    含 id = 更新，缺 id = 新建。返回渠道 id。name 冲突时抛 sqlite3.IntegrityError。"""
    key = data.get("api_key")
    db = get_db(settings.db_path)
    try:
        if data.get("id"):
            sets = ["name = ?", "provider = ?", "base_url = ?"]
            params = [data["name"], data["provider"], data.get("base_url", "")]
            if key == "":
                sets.append("api_key_enc = ?"); params.append(None)
            elif key:
                sets.append("api_key_enc = ?"); params.append(encrypt_secret(key))
            params.append(data["id"])
            db.execute(f"UPDATE llm_channels SET {', '.join(sets)} WHERE id = ?", params)
            cid = data["id"]
        else:
            cur = db.execute(
                "INSERT INTO llm_channels (name, provider, base_url, api_key_enc) "
                "VALUES (?, ?, ?, ?)",
                (data["name"], data["provider"], data.get("base_url", ""),
                 encrypt_secret(key) if key else None))
            cid = cur.lastrowid
        db.commit()
        return cid
    finally:
        db.close()


def channel_in_use(channel_id: int) -> bool:
    db = get_db(settings.db_path)
    try:
        row = db.execute("SELECT 1 FROM agent_config WHERE id = 1 AND "
                         "(channel_id = ? OR vision_channel_id = ?)",
                         (channel_id, channel_id)).fetchone()
        return row is not None
    finally:
        db.close()


def delete_channel(channel_id: int) -> None:
    db = get_db(settings.db_path)
    try:
        db.execute("DELETE FROM llm_channels WHERE id = ?", (channel_id,))
        db.commit()
    finally:
        db.close()
```

- [ ] **Step 4: 建共享测试 helper**

```python
# backend/tests/helpers.py
"""跨测试文件共享的搭建函数。"""


def save_config_with_channel(**overrides) -> int:
    """建一条渠道并把 agent_config 指向它。overrides 直接透传 save_config
    （如 vision_model="v", enabled=True）。返回 channel id。"""
    from agent.config import save_channel, save_config
    cid = save_channel({"name": overrides.pop("channel_name", "test-ch"),
                        "provider": overrides.pop("channel_provider", "openai"),
                        "base_url": overrides.pop("channel_base_url", ""),
                        "api_key": overrides.pop("channel_api_key", "k")})
    save_config({"channel_id": cid, "model": "m", "enabled": True, **overrides})
    return cid
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd backend && pytest tests/test_llm_channels.py -v`
Expected: 全部 PASS。
注意:`tests/test_agent_config.py`、`test_chat_*` 等旧测试此时会开始失败(旧字段没了)——这是预期,Task 3/5 逐步修复,本任务不处理。

- [ ] **Step 6: Commit**

```bash
git add backend/agent/config.py backend/tests/test_llm_channels.py backend/tests/helpers.py
git commit -m "feat(agent): Channel entity + llm_channels CRUD; AgentConfig resolves main/vision channels"
```

---

### Task 3: build_model(channel, model_name) + 三处调用点 + 受影响 chat 测试

**Files:**
- Modify: `backend/agent/llm.py`
- Modify: `backend/agent/chat/vision.py:30`
- Modify: `backend/agent/chat/runtime.py:163,320-324`
- Modify: `backend/tests/test_agent_llm.py`(重写)
- Modify: `backend/tests/test_chat_vision.py`、`backend/tests/test_chat_capture.py`(改用 helper)

**Interfaces:**
- Consumes: `agent.config.Channel`、`tests.helpers.save_config_with_channel`。
- Produces: `build_model(channel: Channel|None, model_name: str)` —— channel 为 None 或无 key、或 model_name 为空时抛 `ValueError`。runtime 的 capture_chart 注册条件变为 `cfg.vision_model and cfg.vision_channel`。

- [ ] **Step 1: 重写 `tests/test_agent_llm.py` 为失败测试**

```python
# backend/tests/test_agent_llm.py
import pytest
from agent.config import Channel


def _ch(**kw):
    base = dict(id=1, name="test", provider="openai",
                base_url="https://gw.example.com/v1", api_key="k")
    base.update(kw)
    return Channel(**base)


def test_openai_model_uses_base_url():
    from agent.llm import build_model
    m = build_model(_ch(), "gpt-5")
    assert m.model_name == "gpt-5"


def test_anthropic_model():
    from agent.llm import build_model
    m = build_model(_ch(provider="anthropic", base_url=""), "claude-sonnet-4-6")
    assert "claude" in m.model_name


def test_missing_key_raises():
    from agent.llm import build_model
    with pytest.raises(ValueError):
        build_model(_ch(api_key=None), "gpt-5")


def test_missing_channel_raises():
    from agent.llm import build_model
    with pytest.raises(ValueError):
        build_model(None, "gpt-5")


def test_empty_model_name_raises():
    from agent.llm import build_model
    with pytest.raises(ValueError):
        build_model(_ch(), "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && pytest tests/test_agent_llm.py -v`
Expected: FAIL(签名不匹配 / TypeError)

- [ ] **Step 3: 实现 `backend/agent/llm.py`(整文件替换)**

```python
"""Provider factory. 基于 pydantic-ai v1.x（版本漂移处理见实施计划 Task 7）。"""
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider


def build_model(channel, model_name: str):
    """从「渠道 + 模型名」构建 LLM 实例。channel: agent.config.Channel。"""
    if channel is None or not channel.api_key:
        raise ValueError("LLM 渠道未配置或缺少 API Key")
    if not model_name:
        raise ValueError("模型名为空")
    if channel.provider == "anthropic":
        return AnthropicModel(model_name,
                              provider=AnthropicProvider(api_key=channel.api_key))
    kwargs = {"api_key": channel.api_key}
    if channel.base_url:
        kwargs["base_url"] = channel.base_url
    return OpenAIChatModel(model_name, provider=OpenAIProvider(**kwargs))
```

- [ ] **Step 4: 改两处运行时调用点**

`backend/agent/chat/vision.py` 第 30 行:

```python
    model = build_model(cfg.vision_channel, cfg.vision_model)
```

`backend/agent/chat/runtime.py`:

① 第 163 行 `if cfg.vision_model:` 改为(视觉渠道失效则不注册 capture_chart;图片中继路径**不改**,失败会以错误文本呈现给模型):

```python
    if cfg.vision_model and cfg.vision_channel:
```

② `run_turn` 中(原 320-324 行):

```python
        has_llm = cfg.main_channel is not None and bool(cfg.main_channel.api_key)
        if not cfg.enabled or (not has_llm and model_override is None):
            raise RuntimeError("Agent 未启用或未配置 LLM 渠道（请到系统设置页配置）")
        prompt, current = _build_prompt(session_id, turn_row["user_message_id"], cfg, deps)
        model = model_override or build_model(cfg.main_channel, cfg.model)
```

- [ ] **Step 5: 迁移 chat 测试到 helper**

`backend/tests/test_chat_vision.py`:文件顶部加 `from tests.helpers import save_config_with_channel`(若 import 失败改 `from helpers import ...`,以先跑通者为准),并做如下替换:

- 13-16 行 `test_config_roundtrips_vision_model`:

```python
def test_config_roundtrips_vision_model():
    save_config_with_channel(vision_model="gemini-vision")
    assert load_config().vision_model == "gemini-vision"
```

- 19-27 行 `test_describe_image_uses_vision_model_slot`:

```python
def test_describe_image_uses_vision_model_slot():
    save_config_with_channel(vision_model="v")
    cfg = load_config()
    with patch("agent.chat.vision.build_model",
               return_value=TestModel(call_tools=[], custom_output_text="上升趋势，MACD 金叉")) as bm:
        out = vision.describe_image(cfg, PNG_1PX, "image/png")
    assert "上升趋势" in out
    assert bm.call_args.args[1] == "v"               # build_model(channel, model_name)
    assert bm.call_args.args[0].id == cfg.vision_channel.id
```

- 52-53 行与 65-66 行的 `save_config({...})` 分别替换为 `save_config_with_channel(vision_model="v")` 和 `save_config_with_channel(vision_model="")`。

`backend/tests/test_chat_capture.py` 的 `_prep`(18-20 行):

```python
def _prep(vision="v"):
    from tests.helpers import save_config_with_channel
    save_config_with_channel(vision_model=vision)
```

- [ ] **Step 6: 跑相关测试**

Run: `cd backend && pytest tests/test_agent_llm.py tests/test_chat_vision.py tests/test_chat_capture.py tests/test_chat_runtime.py tests/test_chat_worker.py -v`
Expected: test_agent_llm / test_chat_vision / test_chat_capture PASS。若 test_chat_runtime / test_chat_worker 仍有旧式 `save_config({"provider"...})` 失败,把这些调用同样替换为 `save_config_with_channel(...)`(参数对照:`vision_model`/`enabled` 直接透传;`api_key`→`channel_api_key`),直至 PASS。

- [ ] **Step 7: Commit**

```bash
git add backend/agent/llm.py backend/agent/chat/vision.py backend/agent/chat/runtime.py backend/tests/
git commit -m "feat(agent): build_model takes (channel, model_name); runtime/vision use per-slot channels"
```

---

### Task 4: 渠道 CRUD API 端点

**Files:**
- Modify: `backend/api/agent.py`
- Test: `backend/tests/test_llm_channels_api.py`(新建)

**Interfaces:**
- Consumes: Task 2 的 CRUD 函数。
- Produces: `GET/POST /api/agent/channels`、`PUT/DELETE /api/agent/channels/{id}`;删除被引用渠道 → 409;重名 → 409;不存在 → 404。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_llm_channels_api.py
import pytest
from agent.config import save_config


@pytest.mark.asyncio
async def test_channel_crud_roundtrip(client):
    async with client as c:
        r = await c.post("/api/agent/channels", json={
            "name": "OpenRouter", "provider": "openai",
            "base_url": "https://openrouter.ai/api/v1", "api_key": "sk-x"})
        assert r.status_code == 200
        cid = r.json()["id"]

        rows = (await c.get("/api/agent/channels")).json()["channels"]
        assert rows[0]["name"] == "OpenRouter" and rows[0]["has_api_key"] is True
        assert "api_key" not in rows[0] and "api_key_enc" not in rows[0]

        r = await c.put(f"/api/agent/channels/{cid}", json={
            "name": "OpenRouter", "provider": "openai", "base_url": "", "api_key": None})
        assert r.status_code == 200
        rows = (await c.get("/api/agent/channels")).json()["channels"]
        assert rows[0]["has_api_key"] is True        # None = 不改 key

        assert (await c.delete(f"/api/agent/channels/{cid}")).status_code == 200
        assert (await c.get("/api/agent/channels")).json()["channels"] == []


@pytest.mark.asyncio
async def test_delete_referenced_channel_409(client):
    async with client as c:
        cid = (await c.post("/api/agent/channels", json={
            "name": "A", "provider": "openai", "base_url": "", "api_key": "k"})).json()["id"]
        save_config({"vision_channel_id": cid})
        assert (await c.delete(f"/api/agent/channels/{cid}")).status_code == 409
        save_config({"vision_channel_id": None})
        assert (await c.delete(f"/api/agent/channels/{cid}")).status_code == 200


@pytest.mark.asyncio
async def test_duplicate_name_409_and_missing_404(client):
    async with client as c:
        body = {"name": "dup", "provider": "openai", "base_url": "", "api_key": "k"}
        await c.post("/api/agent/channels", json=body)
        assert (await c.post("/api/agent/channels", json=body)).status_code == 409
        assert (await c.put("/api/agent/channels/999", json=body)).status_code == 404
        assert (await c.delete("/api/agent/channels/999")).status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && pytest tests/test_llm_channels_api.py -v`
Expected: FAIL(404 Not Found —— 路由不存在)

- [ ] **Step 3: 实现**

`backend/api/agent.py`:顶部 import 区加 `import sqlite3`,并把 `from agent.config import load_config, save_config` 扩为:

```python
from agent.config import (load_config, save_config, Channel, list_channels,
                          get_channel, save_channel, channel_in_use, delete_channel)
```

在 `put_config` 之后追加:

```python
# ---- LLM 渠道 CRUD ----

class ChannelBody(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    provider: Literal["openai", "anthropic"] = "openai"
    base_url: str = ""
    api_key: Optional[str] = None      # None = 不改, "" = 清除


@router.get("/channels")
def get_channels():
    return {"channels": list_channels()}


@router.post("/channels")
def create_channel(body: ChannelBody):
    try:
        return {"id": save_channel(body.model_dump())}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "渠道名已存在")


@router.put("/channels/{channel_id}")
def update_channel(channel_id: int, body: ChannelBody):
    if get_channel(channel_id) is None:
        raise HTTPException(404, "渠道不存在")
    try:
        save_channel({**body.model_dump(), "id": channel_id})
    except sqlite3.IntegrityError:
        raise HTTPException(409, "渠道名已存在")
    return {"id": channel_id}


@router.delete("/channels/{channel_id}")
def remove_channel(channel_id: int):
    if get_channel(channel_id) is None:
        raise HTTPException(404, "渠道不存在")
    if channel_in_use(channel_id):
        raise HTTPException(409, "渠道正被主模型或视觉槽位引用，请先切换槽位")
    delete_channel(channel_id)
    return {"ok": True}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && pytest tests/test_llm_channels_api.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/agent.py backend/tests/test_llm_channels_api.py
git commit -m "feat(api): llm channel CRUD endpoints with in-use/duplicate guards"
```

---

### Task 5: 探测端点按渠道重构 + AgentConfigBody

**Files:**
- Modify: `backend/api/agent.py`(`AgentConfigBody`、`_public`、`ProbeBody`/`_merged_cfg`/`list_models`/`_probe_text`/`_probe_vision`/`test_llm`)
- Modify: `backend/tests/test_agent_config.py`(重写)
- Modify: `backend/tests/test_agent_probe_api.py`(重写)

**Interfaces:**
- Consumes: Task 2 CRUD、Task 3 `build_model(channel, model_name)`。
- Produces:
  - `PUT /api/agent/config` body:`channel_id/vision_channel_id/model/vision_model/max_tokens/max_tool_calls/deep_dive_limit/credential_id/enabled`(不再收 provider/base_url/api_key)。
  - `POST /api/agent/models` body:`{channel_id?, provider?, base_url?, api_key?}` —— channel_id 指向已存渠道为底,inline 字段覆盖(支持未保存先测)。
  - `POST /api/agent/test` body:`{channel_id?, model?, vision_channel_id?, vision_model?}`,缺省回落已存配置;返回 `{"main": {ok, channel, error?}, "vision": null | {ok, channel, supports_image?, error?}}`。
  - `GET /api/agent/config` 响应含 `channel_id/vision_channel_id/has_api_key`(= 主渠道有 key),不含渠道对象。

- [ ] **Step 1: 重写两个测试文件为失败测试**

```python
# backend/tests/test_agent_config.py
import pytest
from tests.helpers import save_config_with_channel


@pytest.mark.asyncio
async def test_get_config_defaults(client):
    async with client as c:
        r = await c.get("/api/agent/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["channel_id"] is None and body["vision_channel_id"] is None
    assert "api_key" not in body and "main_channel" not in body
    assert body["has_api_key"] is False


@pytest.mark.asyncio
async def test_update_config_roundtrip(client):
    cid = save_config_with_channel()
    async with client as c:
        r = await c.put("/api/agent/config", json={
            "channel_id": cid, "vision_channel_id": None,
            "model": "gpt-5", "vision_model": "gpt-4-vision", "enabled": True,
            "max_tool_calls": 10, "deep_dive_limit": 3,
            "credential_id": None, "max_tokens": 4096})
        assert r.status_code == 200
        body = (await c.get("/api/agent/config")).json()
    assert body["channel_id"] == cid and body["has_api_key"] is True
    assert body["model"] == "gpt-5" and body["vision_model"] == "gpt-4-vision"


def test_load_config_decrypts_key_via_channel():
    from agent.config import load_config
    save_config_with_channel(channel_api_key="k123")
    assert load_config().main_channel.api_key == "k123"
```

```python
# backend/tests/test_agent_probe_api.py
import pytest
from unittest.mock import patch
from tests.helpers import save_config_with_channel


@pytest.mark.asyncio
async def test_models_via_stored_channel(client):
    cid = save_config_with_channel(channel_base_url="https://openrouter.ai/api/v1",
                                   channel_api_key="sk-x")

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "deepseek/deepseek-v4-pro"},
                                          {"id": "google/gemini-3-pro"}]}

    with patch("api.agent.requests.get", return_value=FakeResp()) as g:
        async with client as c:
            r = (await c.post("/api/agent/models", json={"channel_id": cid})).json()
    assert r["models"] == ["deepseek/deepseek-v4-pro", "google/gemini-3-pro"]
    assert g.call_args.args[0] == "https://openrouter.ai/api/v1/models"


@pytest.mark.asyncio
async def test_models_inline_overrides_without_saved_channel(client):
    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "m1"}]}

    with patch("api.agent.requests.get", return_value=FakeResp()) as g:
        async with client as c:
            r = await c.post("/api/agent/models", json={
                "provider": "openai", "base_url": "https://x.example/v1", "api_key": "sk-t"})
    assert r.json()["models"] == ["m1"]
    assert g.call_args.args[0] == "https://x.example/v1/models"


@pytest.mark.asyncio
async def test_models_requires_key(client):
    async with client as c:
        assert (await c.post("/api/agent/models", json={})).status_code == 400


@pytest.mark.asyncio
async def test_llm_test_per_slot_channels(client):
    from agent.config import save_channel, save_config
    cid = save_config_with_channel(vision_model="")
    vid = save_channel({"name": "视觉专用", "provider": "openai",
                        "base_url": "", "api_key": "sk-v"})
    seen = {}

    def fake_text(channel, model_name):
        seen["text"] = (channel.id, model_name)
        return {"ok": True, "channel": channel.name}

    def fake_vision(channel, model_name):
        seen["vision"] = (channel.id, model_name)
        return {"ok": True, "channel": channel.name, "supports_image": True}

    with patch("api.agent._probe_text", side_effect=fake_text), \
         patch("api.agent._probe_vision", side_effect=fake_vision):
        async with client as c:
            r = (await c.post("/api/agent/test", json={
                "model": "override-m", "vision_channel_id": vid,
                "vision_model": "vm"})).json()
            assert r["main"]["ok"] is True and seen["text"] == (cid, "override-m")
            assert seen["vision"] == (vid, "vm") and r["vision"]["channel"] == "视觉专用"
            # vision_model 显式空 且 配置为空 → vision 为 null
            r2 = (await c.post("/api/agent/test", json={"vision_model": ""})).json()
            assert r2["vision"] is None


@pytest.mark.asyncio
async def test_semantics_get_seeds_and_put_updates(client):
    async with client as c:
        rows = (await c.get("/api/agent/semantics")).json()
        assert len(rows) == 8
        r = await c.put("/api/agent/semantics/oscillator/oversold_zone",
                        json={"bias": "long（改）"})
        assert r.status_code == 200
        rows = (await c.get("/api/agent/semantics")).json()
        target = next(x for x in rows if x["key"] == "oscillator/oversold_zone")
        assert target["bias"] == "long（改）"
        assert (await c.put("/api/agent/semantics/not/exists",
                            json={"bias": "x"})).status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && pytest tests/test_agent_config.py tests/test_agent_probe_api.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 —— `backend/api/agent.py` 改四块**

① `AgentConfigBody` 替换:

```python
class AgentConfigBody(BaseModel):
    channel_id: Optional[int] = None
    vision_channel_id: Optional[int] = None
    model: str
    vision_model: str = ""
    max_tokens: int = Field(4096, ge=256, le=64000)
    max_tool_calls: int = Field(15, ge=1, le=50)
    deep_dive_limit: int = Field(5, ge=0, le=20)
    credential_id: Optional[int] = None
    enabled: bool = False
```

② `_public` 替换(渠道对象不出 API,只出 id + has_api_key):

```python
def _public(cfg) -> dict:
    d = cfg.__dict__.copy()
    main = d.pop("main_channel")
    d.pop("vision_channel")
    d["has_api_key"] = bool(main and main.api_key)
    d["insecure_defaults"] = settings.insecure_defaults()   # 前端据此显示警告
    return d
```

③ 删除 `_merged_cfg`,`ProbeBody` 与解析函数替换:

```python
class ProbeBody(BaseModel):
    channel_id: Optional[int] = None
    provider: Optional[Literal["openai", "anthropic"]] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None


def _resolve_channel(body: ProbeBody) -> Channel:
    """已存渠道为底 + inline 覆盖（支持渠道未保存先测）。"""
    base = None
    if body.channel_id:
        base = get_channel(body.channel_id)
        if base is None:
            raise HTTPException(404, "渠道不存在")
    return Channel(
        id=base.id if base else 0,
        name=base.name if base else "(未保存)",
        provider=body.provider or (base.provider if base else "openai"),
        base_url=(base.base_url if base else "") if body.base_url is None else body.base_url,
        api_key=body.api_key or (base.api_key if base else None))
```

`list_models` 里 `cfg = _merged_cfg(body)` 起的前几行替换为:

```python
@router.post("/models")
def list_models(body: ProbeBody):
    ch = _resolve_channel(body)
    if not ch.api_key:
        raise HTTPException(400, "未配置 API Key")
    try:
        if ch.provider == "anthropic":
            r = requests.get("https://api.anthropic.com/v1/models",
                             headers={"x-api-key": ch.api_key,
                                      "anthropic-version": "2023-06-01"}, timeout=15)
        else:
            base = (ch.base_url or "https://api.openai.com/v1").rstrip("/")
            r = requests.get(f"{base}/models",
                             headers={"Authorization": f"Bearer {ch.api_key}"}, timeout=15)
        r.raise_for_status()
        ids = sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
        return {"models": ids}
    except requests.RequestException as e:
        raise HTTPException(502, f"模型列表获取失败: {e}")
```

④ 探测函数与 `/test` 替换:

```python
def _probe_text(channel, model_name) -> dict:
    """最小文本调用验证渠道×模型可用。真网调用，仅由 /test 端点触发。"""
    try:
        from pydantic_ai import Agent
        agent = Agent(build_model(channel, model_name), output_type=str)
        # 思考型模型（如 deepseek-v4-pro）先消耗推理 token 再输出——
        # 预算必须容纳整段思考，太小会在产出任何文字前被截断
        agent.run_sync("回复一个字：好", model_settings={"max_tokens": 2048})
        return {"ok": True, "channel": channel.name}
    except Exception as e:
        return {"ok": False, "channel": channel.name, "error": str(e)[:300]}


def _probe_vision(channel, model_name) -> dict:
    try:
        from pydantic_ai import Agent, BinaryContent
        agent = Agent(build_model(channel, model_name), output_type=str)
        agent.run_sync(["图中是什么颜色？一词回答。",
                        BinaryContent(data=_PROBE_PNG, media_type="image/png")],
                       model_settings={"max_tokens": 2048})
        return {"ok": True, "channel": channel.name, "supports_image": True}
    except Exception as e:
        return {"ok": False, "channel": channel.name, "error": str(e)[:300]}


class TestBody(BaseModel):
    channel_id: Optional[int] = None
    model: Optional[str] = None
    vision_channel_id: Optional[int] = None
    vision_model: Optional[str] = None


@router.post("/test")
def test_llm(body: TestBody):
    cfg = load_config()
    main_ch = get_channel(body.channel_id) if body.channel_id else cfg.main_channel
    model = body.model or cfg.model
    if main_ch is None or not main_ch.api_key:
        raise HTTPException(400, "主渠道未配置或缺少 API Key")
    out = {"main": _probe_text(main_ch, model), "vision": None}
    vision_model = cfg.vision_model if body.vision_model is None else body.vision_model
    if vision_model:
        vch = get_channel(body.vision_channel_id) if body.vision_channel_id else main_ch
        if vch is None or not vch.api_key:
            out["vision"] = {"ok": False, "channel": "-",
                             "error": "视觉渠道未配置或缺少 API Key"}
        else:
            out["vision"] = _probe_vision(vch, vision_model)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && pytest tests/test_agent_config.py tests/test_agent_probe_api.py tests/test_llm_channels_api.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 全量回归,清扫残余旧式调用**

Run: `cd backend && pytest -m "not network" -q`
若仍有失败:`grep -rn "save_config({" tests/` 找出残余旧式调用(带 `provider`/`api_key` 键的),替换为 `save_config_with_channel(...)`;`grep -rn "AgentConfig(" tests/` 找出直接构造处,按 Task 2 的新字段表修正。修到全绿。

- [ ] **Step 6: Commit**

```bash
git add backend/api/agent.py backend/tests/
git commit -m "feat(api): channel-aware probe/models/test endpoints; config body takes channel ids"
```

---

### Task 6: 前端 —— 渠道管理卡片 + 槽位渠道下拉

**Files:**
- Modify: `frontend/src/api/client.js`(agent 区块)
- Modify: `frontend/src/views/Settings.vue`(Agent 配置区模板 + script + 少量 CSS)

**Interfaces:**
- Consumes: Task 4/5 的端点。
- Produces: 渠道管理 UI;`agentForm` 用 `channel_id`/`vision_channel_id` 取代 `provider`/`base_url`/api_key 输入。

- [ ] **Step 1: client.js 加渠道方法**

在 `updateAgentConfig` 之后(约 282 行)插入:

```js
  async listLlmChannels() {
    return request('/agent/channels')
  },

  async createLlmChannel(data) {
    return request('/agent/channels', { method: 'POST', body: JSON.stringify(data) })
  },

  async updateLlmChannel(id, data) {
    return request(`/agent/channels/${id}`, { method: 'PUT', body: JSON.stringify(data) })
  },

  async deleteLlmChannel(id) {
    return request(`/agent/channels/${id}`, { method: 'DELETE' })
  },
```

- [ ] **Step 2: Settings.vue 模板改造**

删除 Agent 区里的三块:Provider 下拉的 form-group(224-229 行)、Base URL form-group(232-238 行)、含「模型/视觉模型/API Key」的 form-row(240-271 行),在原位置(安全警告条之后、「启用 Agent」form-row 之前)插入渠道卡片,并在「启用 Agent」form-row 之后插入两行槽位:

```html
      <!-- LLM 渠道管理 -->
      <div class="channel-card">
        <div class="channel-head">
          <strong>LLM 渠道</strong>
          <button type="button" class="btn-inline" @click="startChannelEdit(null)">新增渠道</button>
        </div>
        <div v-if="!channels.length" class="picker-empty">尚无渠道，先新增一个（如 OpenRouter）</div>
        <table v-else class="channel-table">
          <thead><tr><th>名称</th><th>Provider</th><th>Base URL</th><th>Key</th><th></th></tr></thead>
          <tbody>
            <tr v-for="ch in channels" :key="ch.id">
              <td>{{ ch.name }}</td>
              <td>{{ ch.provider }}</td>
              <td class="channel-url">{{ ch.base_url || '官方端点' }}</td>
              <td>{{ ch.has_api_key ? '已配置' : '未配置' }}</td>
              <td class="channel-ops">
                <button type="button" class="btn-inline" @click="startChannelEdit(ch)">编辑</button>
                <button type="button" class="btn-inline" @click="removeChannel(ch)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="channelEdit" class="channel-editor">
          <div class="form-row">
            <div class="form-group">
              <label>名称</label>
              <input v-model="channelEdit.name" placeholder="例：OpenRouter" />
            </div>
            <div class="form-group">
              <label>Provider</label>
              <select v-model="channelEdit.provider">
                <option value="openai">OpenAI 兼容端点</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </div>
          </div>
          <div v-if="channelEdit.provider === 'openai'" class="form-group">
            <label>Base URL</label>
            <input v-model="channelEdit.base_url" placeholder="https://.../v1，留空使用官方端点" />
          </div>
          <div class="form-group">
            <label>API Key</label>
            <input v-model="channelEdit.api_key" type="password" autocomplete="new-password"
                   :placeholder="channelEdit.has_api_key ? '已保存（留空不修改）' : '请输入 API Key'" />
          </div>
          <div class="btn-row">
            <button class="btn btn-primary btn-sm" @click="saveChannel">保存渠道</button>
            <button type="button" class="btn btn-sm" :disabled="channelTesting" @click="testChannel">
              {{ channelTesting ? '测试中…' : '测试连通' }}</button>
            <button type="button" class="btn btn-sm" @click="channelEdit = null">取消</button>
            <span v-if="channelMsg" class="test-result">{{ channelMsg }}</span>
          </div>
        </div>
      </div>
```

```html
      <div class="form-row">
        <div class="form-group">
          <label>主模型渠道</label>
          <select v-model="agentForm.channel_id">
            <option :value="null">未选择</option>
            <option v-for="ch in channels" :key="ch.id" :value="ch.id">{{ ch.name }}</option>
          </select>
        </div>
        <div class="form-group">
          <label>模型 <button type="button" class="btn-inline" @click="openModelPicker('model')">选择</button></label>
          <input v-model="agentForm.model"
                 placeholder="例：deepseek/deepseek-v4-pro（可手输或点「选择」浏览）" />
        </div>
      </div>

      <div class="form-row">
        <div class="form-group">
          <label>视觉渠道</label>
          <select v-model="agentForm.vision_channel_id">
            <option :value="null">跟随主渠道</option>
            <option v-for="ch in channels" :key="ch.id" :value="ch.id">{{ ch.name }}</option>
          </select>
        </div>
        <div class="form-group">
          <label>视觉模型（可选，识图/截图分析用）
            <button type="button" class="btn-inline" @click="openModelPicker('vision_model')">选择</button></label>
          <input v-model="agentForm.vision_model"
                 placeholder="留空 = 图片直传主模型（主模型须多模态）" />
        </div>
      </div>
```

测试结果展示(325-329 行)改为带渠道名:

```html
        <span v-if="testResult" class="test-result">
          主模型 {{ testResult.main.channel ? '[' + testResult.main.channel + '] ' : '' }}{{ testResult.main.ok ? '✅' : '❌ ' + testResult.main.error }}
          <template v-if="testResult.vision">
            ｜视觉 {{ testResult.vision.channel ? '[' + testResult.vision.channel + '] ' : '' }}{{ testResult.vision.ok ? '✅ 支持图像' : '❌ ' + testResult.vision.error }}
          </template>
```

- [ ] **Step 3: Settings.vue script 改造**

① `agentForm` 初始值与 `loadAgentConfig`:`provider`/`base_url` 两键替换为 `channel_id: null, vision_channel_id: null`,加载时取 `r.channel_id ?? null` / `r.vision_channel_id ?? null`。
② 删除 `agentHasApiKey`/`agentApiKeyInput`/`agentApiKeyClearedFlag`/`clearAgentApiKey` 及 `saveAgentConfig` 里的 api_key 组装(`saveAgentConfig` 直接 `api.updateAgentConfig({ ...agentForm.value })`;同步删除模板里已不存在的引用)。
③ `agentOverrides()` 替换为:

```js
function testOverrides() {
  return { channel_id: agentForm.value.channel_id,
           model: agentForm.value.model,
           vision_channel_id: agentForm.value.vision_channel_id,
           vision_model: agentForm.value.vision_model }
}
```

`testLlm` 内改为 `api.testAgentLlm(testOverrides())`。
④ `openModelPicker` 改为每次打开都刷新(渠道相关):`modelFilter.value = ''; await loadModels()`;`loadModels` 改为:

```js
async function loadModels() {
  const cid = modelPickerFor.value === 'vision_model'
    ? (agentForm.value.vision_channel_id || agentForm.value.channel_id)
    : agentForm.value.channel_id
  pickerLoading.value = true
  pickerError.value = ''
  try {
    modelList.value = (await api.fetchAgentModels({ channel_id: cid })).models
  } catch (e) {
    pickerError.value = '模型列表获取失败：' + e.message
  } finally {
    pickerLoading.value = false
  }
}
```

⑤ 渠道管理状态与函数(加在 agent 配置区代码附近):

```js
// ---- LLM 渠道管理 ----
const channels = ref([])
const channelEdit = ref(null)   // null | {id?, name, provider, base_url, api_key, has_api_key}
const channelMsg = ref('')
const channelTesting = ref(false)

async function loadChannels() {
  try { channels.value = (await api.listLlmChannels()).channels } catch {}
}

function startChannelEdit(ch) {
  channelMsg.value = ''
  channelEdit.value = ch
    ? { id: ch.id, name: ch.name, provider: ch.provider, base_url: ch.base_url,
        api_key: '', has_api_key: ch.has_api_key }
    : { name: '', provider: 'openai', base_url: '', api_key: '', has_api_key: false }
}

async function saveChannel() {
  const e = channelEdit.value
  const payload = { name: e.name.trim(), provider: e.provider, base_url: e.base_url,
                    api_key: e.api_key.trim() || null }   // 空输入 = 不改已存 key
  try {
    if (e.id) await api.updateLlmChannel(e.id, payload)
    else await api.createLlmChannel(payload)
    channelEdit.value = null
    await loadChannels()
  } catch (err) { channelMsg.value = '保存失败：' + err.message }
}

async function removeChannel(ch) {
  if (!confirm(`删除渠道「${ch.name}」？`)) return
  try { await api.deleteLlmChannel(ch.id); await loadChannels() }
  catch (err) { alert('删除失败：' + err.message) }
}

async function testChannel() {
  const e = channelEdit.value
  channelTesting.value = true
  channelMsg.value = ''
  try {
    const body = { channel_id: e.id || null, provider: e.provider, base_url: e.base_url }
    if (e.api_key.trim()) body.api_key = e.api_key.trim()
    const r = await api.fetchAgentModels(body)
    channelMsg.value = `✅ 连通（${r.models.length} 个模型）`
  } catch (err) {
    channelMsg.value = '❌ ' + err.message
  } finally {
    channelTesting.value = false
  }
}
```

⑥ 在现有 `onMounted`(或初始化调用处,`loadAgentConfig()` 旁)追加 `loadChannels()`。

- [ ] **Step 4: 加 CSS(Settings.vue style 区末尾)**

```css
.channel-card { border: 1px solid var(--border, #ddd); border-radius: 8px;
  padding: 12px; margin-bottom: 16px; }
.channel-head { display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 8px; }
.channel-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.channel-table th, .channel-table td { text-align: left; padding: 4px 8px;
  border-bottom: 1px solid var(--border, #eee); }
.channel-url { font-family: monospace; font-size: 12px; word-break: break-all; }
.channel-ops { white-space: nowrap; }
.channel-editor { margin-top: 12px; padding-top: 12px;
  border-top: 1px dashed var(--border, #ddd); }
```

- [ ] **Step 5: 构建验证**

Run: `cd frontend && npm run build`
Expected: 构建成功,无未定义引用告警(重点确认模板里不再引用 `agentApiKeyInput`/`agentHasApiKey`/`agentApiKeyClearedFlag`/`clearAgentApiKey`/`agentOverrides`)。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.js frontend/src/views/Settings.vue
git commit -m "feat(settings-ui): LLM channel management card + per-slot channel selectors"
```

---

### Task 7: 收尾 —— 全量回归 + 文档

**Files:**
- Modify: `CLAUDE.md`(key tables 列表 + agent 目录描述)
- Test: 全量 pytest + 前端构建

- [ ] **Step 1: 全量回归**

Run: `cd backend && pytest -m "not network" -q`
Expected: 全绿。任何失败按 Task 5 Step 5 的 grep 清扫法处理。

- [ ] **Step 2: 更新 CLAUDE.md**

- Database 段 key tables 列表在 `screener_semantics` 后追加 `llm_channels`。
- `backend/agent/` 描述里 `config.py (Fernet key + vision_model)` 改为 `config.py (llm_channels 渠道 CRUD + 双槽位渠道解析 + Fernet key)`。

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record llm_channels table and channel-aware agent config"
```
