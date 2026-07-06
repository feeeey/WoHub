# 对话式 Agent（Chat Agent）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 WoHub 的 agent 层重造为 Manus 式多会话对话 agent：后台执行 + 事件溯源，SSE 全流式（文字逐字 + 工具卡片实时），工具覆盖筛选器扫描/K线/指标/识图/交易只读，并移除旧批量裁决层。

**Architecture:** 用户消息落库后创建 `chat_turns` 队列行，后台 daemon 线程驱动 PydanticAI `agent.iter` 工具循环，事件（text_delta/tool_start/tool_end/…）边产生边写 `chat_events`；SSE 端点只是从事件表 tail 的观察窗口，断线/刷新按全局事件 id 续播。规格：`docs/superpowers/specs/2026-07-04-chat-agent-design.md`。

**Tech Stack:** FastAPI + SQLite(WAL) + PydanticAI（`pydantic-ai-slim[openai,anthropic]>=1.0,<2`，已有）；Vue 3 + EventSource；新增前端依赖仅 `marked` + `dompurify`。

## Global Constraints

- 红线：`backend/agent/` 及其 import 链**禁止**出现任何下单/改单/撤单函数（`place_order*`、`close_position`、`close_all`、`cancel_*`）。工具全只读。
- 纯技术分析：不注册任何新闻/情绪/链上工具。
- `database.py` 的 SCHEMA 常量是 append-only：改已存在的 CREATE TABLE 体是静默 no-op；已建表加列必须走 `_migrate()` 幂等 ALTER。
- 节流沿用：TradingView 全局 1 次/2 秒（pine_screener 内置）；Binance fapi ≥250ms（tools.py `_throttle()`）。
- 单条工具结果字符串超 2000 字符截断（trace 与事件 payload 同规则）。
- 每轮（turn）预算：`max_tool_calls`（UsageLimits）+ `deep_dive_limit`（kline_summary/kline 结构 + capture_chart 合计）。
- UI 文案全部中文；后端错误信息面向 LLM 的用中文撰写。
- 测试：`cd backend && pytest -m "not network"` 必须全绿后才 commit；真网调用标 `pytest.mark.network`。前端无测试框架，前端任务以 `npm run build` 通过 + 手测清单代替。
- 新后端依赖仅允许：`python-multipart`（若 pyproject 尚无）。禁止引入其他新依赖。
- 提交信息格式沿用仓库惯例：`feat(chat): …` / `fix(chat): …` / `refactor(agent): …`。

**执行前置：** 各任务的 Files 段路径均相对仓库根 `WoHub/`。运行测试的工作目录是 `backend/`。

---

## Phase 1 — 数据层 + 事件基建

### Task 1: 新表 schema + agent_config 迁移

**Files:**
- Modify: `backend/database.py`（SCHEMA 尾部追加 + 新增 `_migrate()`）
- Test: `backend/tests/test_chat_schema.py`

**Interfaces:**
- Consumes: 无（纯 schema）
- Produces: 表 `chat_sessions/chat_messages/chat_turns/chat_events/screener_semantics`；`agent_config.vision_model TEXT NOT NULL DEFAULT ''` 列；后续所有任务依赖这些表存在。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_schema.py
import os
import sqlite3


def _cols(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_chat_tables_exist():
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"chat_sessions", "chat_messages", "chat_turns",
            "chat_events", "screener_semantics"} <= names


def test_chat_events_columns():
    assert {"id", "turn_id", "seq", "type", "payload_json",
            "created_at"} <= _cols(os.environ["DB_PATH"], "chat_events")


def test_agent_config_vision_model_migrated():
    """init_db 对已存在的 agent_config 表也要补上 vision_model 列（幂等 ALTER）。"""
    assert "vision_model" in _cols(os.environ["DB_PATH"], "agent_config")


def test_migrate_idempotent():
    """重复 init_db 不报错（ALTER 已存在列时跳过）。"""
    from database import init_db
    init_db(os.environ["DB_PATH"])
    init_db(os.environ["DB_PATH"])
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_schema.py -v`
Expected: FAIL（`chat_sessions` 不存在 / `vision_model` 缺失）

- [ ] **Step 3: 实现**

在 `backend/database.py` 的 SCHEMA 字符串**末尾**（`CREATE INDEX IF NOT EXISTS idx_outcomes_signal …` 之后、结束三引号之前）追加：

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '新会话',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL DEFAULT '',
    images_json TEXT,
    trace_json TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    model TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);

CREATE TABLE IF NOT EXISTS chat_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id),
    user_message_id INTEGER REFERENCES chat_messages(id),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_status ON chat_turns(status);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session ON chat_turns(session_id, id);

CREATE TABLE IF NOT EXISTS chat_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL REFERENCES chat_turns(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_events_turn ON chat_events(turn_id, seq);

CREATE TABLE IF NOT EXISTS screener_semantics (
    key TEXT PRIMARY KEY,
    meaning TEXT NOT NULL DEFAULT '',
    bias TEXT NOT NULL DEFAULT '',
    usage TEXT NOT NULL DEFAULT '',
    caveats TEXT NOT NULL DEFAULT '',
    combos TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

并把 `init_db` 改为：

```python
def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column additions for tables that already exist in deployed DBs.
    (SCHEMA 是 append-only：改已存在的 CREATE TABLE 体是静默 no-op。)"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_config)")}
    if "vision_model" not in cols:
        conn.execute("ALTER TABLE agent_config ADD COLUMN vision_model TEXT NOT NULL DEFAULT ''")


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    conn.close()
```

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_schema.py tests/test_database.py -v`
Expected: 全 PASS（test_database.py 的既有表计数断言若写死数量需 +5，按其现有断言方式同步修改）

- [ ] **Step 5: Commit**

```bash
git add backend/database.py backend/tests/test_chat_schema.py backend/tests/test_database.py
git commit -m "feat(chat): schema for sessions/messages/turns/events + screener_semantics; agent_config.vision_model migration"
```

### Task 2: 会话/消息/轮次存储层 `store.py`

**Files:**
- Create: `backend/agent/chat/__init__.py`（空文件）
- Create: `backend/agent/chat/store.py`
- Test: `backend/tests/test_chat_store.py`

**Interfaces:**
- Consumes: Task 1 的表
- Produces（后续 runtime/worker/API 全部依赖，签名必须一致）:
  - `create_session(title: str | None = None) -> int`
  - `list_sessions() -> list[dict]`（含 id/title/created_at/updated_at/message_count）
  - `rename_session(session_id: int, title: str) -> bool`
  - `delete_session(session_id: int) -> None`（级联删 messages/turns/events）
  - `touch_session(session_id: int) -> None`
  - `add_message(session_id, role, content, images=None, trace=None, model=None, input_tokens=None, output_tokens=None, error=None) -> int`
  - `list_messages(session_id: int) -> list[dict]`（images/trace 已 json 解析）
  - `create_turn(session_id: int, user_message_id: int) -> int`
  - `claim_next_turn() -> sqlite3.Row | None`（原子 UPDATE…RETURNING）
  - `finish_turn(turn_id: int, status: str) -> None`（status ∈ done/failed/cancelled）
  - `request_cancel(turn_id: int) -> bool`
  - `cancel_requested(turn_id: int) -> bool`
  - `active_turn(session_id: int) -> dict | None`（queued/running 的最新 turn）
  - `recover_interrupted() -> list[int]`（启动时 running→failed，返回受影响 turn id）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_store.py
from agent.chat import store


def test_session_crud_and_title():
    sid = store.create_session()
    assert store.list_sessions()[0]["title"] == "新会话"
    store.rename_session(sid, "BTC 结构分析")
    assert store.list_sessions()[0]["title"] == "BTC 结构分析"
    store.delete_session(sid)
    assert store.list_sessions() == []


def test_message_roundtrip_parses_json_fields():
    sid = store.create_session()
    mid = store.add_message(sid, "user", "看下 BTC",
                            images=[{"kind": "upload", "filename": "a.png"}])
    store.add_message(sid, "assistant", "好的", trace={"steps": [1]},
                      model="m", input_tokens=10, output_tokens=5)
    msgs = store.list_messages(sid)
    assert msgs[0]["id"] == mid and msgs[0]["images"] == [{"kind": "upload", "filename": "a.png"}]
    assert msgs[1]["trace"] == {"steps": [1]} and msgs[1]["output_tokens"] == 5


def test_turn_queue_claim_and_finish():
    sid = store.create_session()
    mid = store.add_message(sid, "user", "hi")
    tid = store.create_turn(sid, mid)
    row = store.claim_next_turn()
    assert row["id"] == tid and row["status"] == "running"
    assert store.claim_next_turn() is None
    store.finish_turn(tid, "done")
    assert store.active_turn(sid) is None


def test_cancel_flag():
    sid = store.create_session()
    tid = store.create_turn(sid, store.add_message(sid, "user", "x"))
    assert store.cancel_requested(tid) is False
    assert store.request_cancel(tid) is True
    assert store.cancel_requested(tid) is True


def test_recover_interrupted_marks_running_failed():
    sid = store.create_session()
    tid = store.create_turn(sid, store.add_message(sid, "user", "x"))
    store.claim_next_turn()
    assert store.recover_interrupted() == [tid]
    assert store.active_turn(sid) is None      # failed 不算 active


def test_delete_session_cascades():
    sid = store.create_session()
    mid = store.add_message(sid, "user", "x")
    tid = store.create_turn(sid, mid)
    from agent.chat import events
    events.append_event(tid, "text_delta", {"text": "a"})
    store.delete_session(sid)
    assert store.list_messages(sid) == []
    assert events.events_after(sid, 0) == []
```

注：最后一个用例依赖 Task 3 的 events 模块，先用 `pytest.importorskip("agent.chat.events")` 保护或放到 Task 3 再补——直接把该用例写在 Task 3 的测试文件里也可（推荐后者，本任务先写前 5 个用例）。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_store.py -v`
Expected: FAIL with `ModuleNotFoundError: agent.chat`

- [ ] **Step 3: 实现**

```python
# backend/agent/chat/store.py
"""Chat sessions/messages/turns persistence. Short-lived connections,
short transactions (SQLite single-writer discipline)."""
import json
from database import get_db
from config import settings


def _db():
    return get_db(settings.db_path)


# ---- sessions ----

def create_session(title: str | None = None) -> int:
    db = _db()
    try:
        cur = db.execute("INSERT INTO chat_sessions (title) VALUES (?)",
                         (title or "新会话",))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def list_sessions() -> list[dict]:
    db = _db()
    try:
        rows = db.execute(
            """SELECT s.*, (SELECT COUNT(*) FROM chat_messages m
                            WHERE m.session_id = s.id) AS message_count
               FROM chat_sessions s ORDER BY s.updated_at DESC, s.id DESC""").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def rename_session(session_id: int, title: str) -> bool:
    db = _db()
    try:
        cur = db.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title[:80], session_id))
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def delete_session(session_id: int) -> None:
    db = _db()
    try:
        db.execute("DELETE FROM chat_events WHERE turn_id IN "
                   "(SELECT id FROM chat_turns WHERE session_id = ?)", (session_id,))
        db.execute("DELETE FROM chat_turns WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        db.commit()
    finally:
        db.close()


def touch_session(session_id: int) -> None:
    db = _db()
    try:
        db.execute("UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
                   (session_id,))
        db.commit()
    finally:
        db.close()


# ---- messages ----

def add_message(session_id: int, role: str, content: str, images=None, trace=None,
                model=None, input_tokens=None, output_tokens=None, error=None) -> int:
    db = _db()
    try:
        cur = db.execute(
            """INSERT INTO chat_messages (session_id, role, content, images_json,
               trace_json, model, input_tokens, output_tokens, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, role, content,
             json.dumps(images, ensure_ascii=False) if images else None,
             json.dumps(trace, ensure_ascii=False) if trace else None,
             model, input_tokens, output_tokens, error))
        db.execute("UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
                   (session_id,))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def _safe_json(s):
    try:
        return json.loads(s) if s else None
    except json.JSONDecodeError:
        return {"_parse_error": True}


def list_messages(session_id: int) -> list[dict]:
    db = _db()
    try:
        rows = db.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id",
            (session_id,)).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        d = dict(r)
        d["images"] = _safe_json(d.pop("images_json")) or []
        d["trace"] = _safe_json(d.pop("trace_json"))
        out.append(d)
    return out


# ---- turns（chat_turns 兼作工作队列，单 worker）----

def create_turn(session_id: int, user_message_id: int) -> int:
    db = _db()
    try:
        cur = db.execute(
            "INSERT INTO chat_turns (session_id, user_message_id) VALUES (?, ?)",
            (session_id, user_message_id))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def claim_next_turn():
    """Atomically claim oldest queued turn; None when empty (SQLite >= 3.35 RETURNING)."""
    db = _db()
    try:
        row = db.execute(
            """UPDATE chat_turns SET status = 'running'
               WHERE id = (SELECT id FROM chat_turns WHERE status = 'queued'
                           ORDER BY id LIMIT 1)
               RETURNING *""").fetchone()
        db.commit()
        return row
    finally:
        db.close()


def finish_turn(turn_id: int, status: str) -> None:
    assert status in ("done", "failed", "cancelled")
    db = _db()
    try:
        db.execute("UPDATE chat_turns SET status = ?, finished_at = datetime('now') "
                   "WHERE id = ?", (status, turn_id))
        db.commit()
    finally:
        db.close()


def request_cancel(turn_id: int) -> bool:
    db = _db()
    try:
        cur = db.execute(
            "UPDATE chat_turns SET cancel_requested = 1 WHERE id = ? "
            "AND status IN ('queued', 'running')", (turn_id,))
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def cancel_requested(turn_id: int) -> bool:
    db = _db()
    try:
        row = db.execute("SELECT cancel_requested FROM chat_turns WHERE id = ?",
                         (turn_id,)).fetchone()
        return bool(row and row["cancel_requested"])
    finally:
        db.close()


def active_turn(session_id: int) -> dict | None:
    db = _db()
    try:
        row = db.execute(
            """SELECT id, status, user_message_id FROM chat_turns
               WHERE session_id = ? AND status IN ('queued', 'running')
               ORDER BY id DESC LIMIT 1""", (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def recover_interrupted() -> list[int]:
    """启动恢复：queued 保留续跑；被重启打断的 running 判 failed。"""
    db = _db()
    try:
        rows = db.execute(
            """UPDATE chat_turns SET status = 'failed', finished_at = datetime('now')
               WHERE status = 'running' RETURNING id""").fetchall()
        db.commit()
        return [r["id"] for r in rows]
    finally:
        db.close()
```

`backend/agent/chat/__init__.py` 为空文件。

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/chat/ backend/tests/test_chat_store.py
git commit -m "feat(chat): session/message/turn store — DB-backed turn queue with cancel and restart recovery"
```

### Task 3: 事件层 `events.py`

**Files:**
- Create: `backend/agent/chat/events.py`
- Test: `backend/tests/test_chat_events.py`

**Interfaces:**
- Consumes: Task 1 表、Task 2 `store`
- Produces:
  - `append_event(turn_id: int, type: str, payload: dict) -> int`（返回全局事件 id；per-turn seq 自动递增）
  - `events_after(session_id: int, after_id: int, limit: int = 500) -> list[dict]`（键：id/turn_id/seq/type/payload）
  - `last_event_id() -> int`（全局最大 id，0 表示无事件）
  - `turn_events(turn_id: int) -> list[dict]`（恢复进行中轮次用）
  - 事件类型约定（runtime/前端共同遵守）：`text_delta {text}` / `tool_start {tool, args}` / `tool_progress {tool, done, total, note}` / `tool_end {tool, ok, summary, elapsed_ms}` / `image {kind, filename, caption}` / `turn_done {message_id, input_tokens, output_tokens}` / `turn_error {error}` / `cancelled {}`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_events.py
from agent.chat import store, events


def _turn():
    sid = store.create_session()
    return sid, store.create_turn(sid, store.add_message(sid, "user", "x"))


def test_append_and_seq_monotonic():
    sid, tid = _turn()
    e1 = events.append_event(tid, "text_delta", {"text": "a"})
    e2 = events.append_event(tid, "tool_start", {"tool": "get_klines", "args": {}})
    rows = events.turn_events(tid)
    assert [r["seq"] for r in rows] == [1, 2]
    assert rows[0]["payload"] == {"text": "a"}
    assert e2 > e1


def test_events_after_resume_no_dup_no_loss():
    sid, tid = _turn()
    ids = [events.append_event(tid, "text_delta", {"text": str(i)}) for i in range(5)]
    part1 = events.events_after(sid, 0, limit=2)
    part2 = events.events_after(sid, part1[-1]["id"])
    got = [r["id"] for r in part1 + part2]
    assert got == ids                      # 不重不漏、按 id 升序


def test_events_after_scoped_to_session():
    sid1, tid1 = _turn()
    sid2, tid2 = _turn()
    events.append_event(tid1, "text_delta", {"text": "s1"})
    events.append_event(tid2, "text_delta", {"text": "s2"})
    assert all(r["turn_id"] == tid2 for r in events.events_after(sid2, 0))


def test_last_event_id_empty_is_zero():
    assert events.last_event_id() == 0


def test_delete_session_cascades_events():
    sid, tid = _turn()
    events.append_event(tid, "text_delta", {"text": "a"})
    store.delete_session(sid)
    assert events.events_after(sid, 0) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_events.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现**

```python
# backend/agent/chat/events.py
"""Append-only event log per turn. Global autoincrement id doubles as the
SSE resume cursor; per-turn seq guarantees intra-turn ordering."""
import json
from database import get_db
from config import settings

MAX_PAYLOAD_CHARS = 4000   # 事件是流式明细，超长截断（完整结果在消息 trace_json 里）


def append_event(turn_id: int, type: str, payload: dict) -> int:
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw) > MAX_PAYLOAD_CHARS:
        raw = json.dumps({"_truncated": True, "preview": raw[:MAX_PAYLOAD_CHARS]},
                         ensure_ascii=False)
    db = get_db(settings.db_path)
    try:
        cur = db.execute(
            """INSERT INTO chat_events (turn_id, seq, type, payload_json)
               VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM chat_events
                           WHERE turn_id = ?), ?, ?)""",
            (turn_id, turn_id, type, raw))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def _rows_to_dicts(rows):
    out = []
    for r in rows:
        d = {"id": r["id"], "turn_id": r["turn_id"], "seq": r["seq"], "type": r["type"]}
        try:
            d["payload"] = json.loads(r["payload_json"])
        except json.JSONDecodeError:
            d["payload"] = {"_parse_error": True}
        out.append(d)
    return out


def events_after(session_id: int, after_id: int, limit: int = 500) -> list[dict]:
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            """SELECT e.id, e.turn_id, e.seq, e.type, e.payload_json
               FROM chat_events e JOIN chat_turns t ON t.id = e.turn_id
               WHERE t.session_id = ? AND e.id > ? ORDER BY e.id LIMIT ?""",
            (session_id, after_id, limit)).fetchall()
    finally:
        db.close()
    return _rows_to_dicts(rows)


def turn_events(turn_id: int) -> list[dict]:
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            "SELECT id, turn_id, seq, type, payload_json FROM chat_events "
            "WHERE turn_id = ? ORDER BY seq", (turn_id,)).fetchall()
    finally:
        db.close()
    return _rows_to_dicts(rows)


def last_event_id() -> int:
    db = get_db(settings.db_path)
    try:
        row = db.execute("SELECT COALESCE(MAX(id), 0) AS m FROM chat_events").fetchone()
        return row["m"]
    finally:
        db.close()
```

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_events.py tests/test_chat_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/chat/events.py backend/tests/test_chat_events.py
git commit -m "feat(chat): append-only event log with global resume cursor and per-turn seq"
```

---

## Phase 2 — 指标模块 + 工具层扩展

### Task 4: `klines/indicators.py` 核心六件套

**Files:**
- Create: `backend/klines/indicators.py`
- Test: `backend/tests/test_indicators.py`

**Interfaces:**
- Consumes: `klines.models.Candle`、`klines.structure.atr(candles, period)`（已有）
- Produces:
  - `sma(values: list[float], period: int) -> float | None`
  - `ema_series(values: list[float], period: int) -> list[float]`（不足 period 返回 `[]`；首值 = 前 period 个的 SMA）
  - `rsi(closes: list[float], period: int = 14) -> float | None`（Wilder 平滑；无涨无跌返回 50.0）
  - `macd(closes) -> dict | None`（键 dif/dea/hist/cross，cross ∈ golden/dead/none）
  - `bollinger(closes, period=20, k=2.0) -> dict | None`（键 upper/mid/lower/position）
  - `volume_ratio(volumes: list[float], period: int = 20) -> float | None`（量比 = 最新量 / 前 period 根均量）
  - `compute_indicators(candles: list[Candle]) -> dict`（聚合入口，Task 5 的工具消费）

- [ ] **Step 1: 写失败测试（金标准数值）**

```python
# backend/tests/test_indicators.py
import pytest
from klines.indicators import (sma, ema_series, rsi, macd, bollinger,
                               volume_ratio, compute_indicators)
from klines.models import Candle


def test_sma_basic_and_insufficient():
    assert sma([1, 2, 3, 4, 5], 5) == 3.0
    assert sma([1, 2], 5) is None


def test_ema_seeded_with_sma():
    # period=3，seed=SMA(1,2,3)=2.0，k=0.5
    # next: 2+ (4-2)*0.5 = 3.0; then 3 + (5-3)*0.5 = 4.0
    assert ema_series([1, 2, 3, 4, 5], 3) == [2.0, 3.0, 4.0]
    assert ema_series([1, 2], 3) == []


def test_rsi_all_gains_is_100_flat_is_50():
    closes = list(range(1, 20))            # 全涨
    assert rsi(closes, 14) == 100.0
    assert rsi([5.0] * 20, 14) == 50.0     # 无涨无跌


def test_rsi_known_mixed_sequence():
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    v = rsi(closes, 14)
    assert v == pytest.approx(70.46, abs=0.1)   # Wilder 教科书序列


def test_macd_constant_series_is_zero():
    out = macd([10.0] * 60)
    assert out["dif"] == pytest.approx(0.0, abs=1e-9)
    assert out["hist"] == pytest.approx(0.0, abs=1e-9)
    assert out["cross"] == "none"
    assert macd([1.0] * 10) is None        # 长度不足 slow+signal


def test_bollinger_constant_band_collapses():
    out = bollinger([10.0] * 25)
    assert out["upper"] == out["mid"] == out["lower"] == 10.0
    assert out["position"] == 0.5          # 带宽为 0 时定义居中


def test_volume_ratio():
    assert volume_ratio([1.0] * 20 + [2.0], 20) == pytest.approx(2.0)
    assert volume_ratio([1.0] * 5, 20) is None


def _mk(i, close, vol=100.0):
    return Candle(open_time=i, close_time=i + 1, open=close, high=close + 1,
                  low=close - 1, close=close, volume=vol, closed=True)


def test_compute_indicators_shape():
    candles = [_mk(i, 100 + i * 0.1) for i in range(80)]
    out = compute_indicators(candles)
    assert set(out) == {"ma", "ema", "macd", "rsi", "boll", "atr", "volume"}
    assert out["ma"]["ma20"] is not None
    assert out["rsi"]["state"] in ("overbought", "oversold", "neutral")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_indicators.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# backend/klines/indicators.py
"""Classic indicator math on closed candles. Pure local computation, no I/O.
所有函数对不足样本返回 None/[]（调用方负责向 LLM 解释数据不足）。"""
from statistics import mean, pstdev
from klines.models import Candle
from klines.structure import atr as _atr


def sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return mean(values[-period:])


def ema_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [mean(values[:period])]
    for v in values[period:]:
        out.append(out[-1] + (v - out[-1]) * k)
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for prev, cur in zip(closes[:-1], closes[1:]):
        chg = cur - prev
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])
    for g, l in zip(gains[period:], losses[period:]):     # Wilder smoothing
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    if len(closes) < slow + signal:
        return None
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    dif = [f - s for f, s in zip(ema_fast[-len(ema_slow):], ema_slow)]
    dea = ema_series(dif, signal)
    if not dea:
        return None
    hist = [d - e for d, e in zip(dif[-len(dea):], dea)]
    cross = "none"
    if len(hist) >= 2:
        if hist[-2] <= 0 < hist[-1]:
            cross = "golden"
        elif hist[-2] >= 0 > hist[-1]:
            cross = "dead"
    return {"dif": round(dif[-1], 6), "dea": round(dea[-1], 6),
            "hist": round(hist[-1], 6), "cross": cross}


def bollinger(closes: list[float], period: int = 20, k: float = 2.0) -> dict | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = mean(window)
    sd = pstdev(window)
    upper, lower = mid + k * sd, mid - k * sd
    pos = 0.5 if upper == lower else (closes[-1] - lower) / (upper - lower)
    return {"upper": round(upper, 8), "mid": round(mid, 8), "lower": round(lower, 8),
            "position": round(min(max(pos, 0.0), 1.0), 4)}


def volume_ratio(volumes: list[float], period: int = 20) -> float | None:
    if len(volumes) < period + 1:
        return None
    base = mean(volumes[-period - 1:-1])
    if base == 0:
        return None
    return round(volumes[-1] / base, 4)


def compute_indicators(candles: list[Candle]) -> dict:
    """聚合六件套。只用已收盘 K 线（未收盘棒会让均线/量比失真）。"""
    closed = [c for c in candles if c.closed]
    closes = [c.close for c in closed]
    vols = [c.volume for c in closed]
    r = rsi(closes)
    a = _atr(candles, period=14)
    last = closes[-1] if closes else None
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    ema55 = ema_series(closes, 55)
    return {
        "ma": {f"ma{p}": (round(v, 8) if (v := sma(closes, p)) is not None else None)
               for p in (5, 10, 20, 50)},
        "ema": {"ema12": round(ema12[-1], 8) if ema12 else None,
                "ema26": round(ema26[-1], 8) if ema26 else None,
                "ema55": round(ema55[-1], 8) if ema55 else None},
        "macd": macd(closes),
        "rsi": {"rsi14": r,
                "state": ("overbought" if r is not None and r >= 70 else
                          "oversold" if r is not None and r <= 30 else "neutral")},
        "boll": bollinger(closes),
        "atr": {"atr14": a,
                "atr_pct": round(a / last * 100, 3) if a is not None and last else None},
        "volume": {"vol_ratio20": volume_ratio(vols)},
    }
```

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_indicators.py -v`
Expected: PASS（若 `test_rsi_known_mixed_sequence` 数值有小偏差，核对实现是否 Wilder 平滑——教科书值 70.46±0.1）

- [ ] **Step 5: Commit**

```bash
git add backend/klines/indicators.py backend/tests/test_indicators.py
git commit -m "feat(klines): core indicator module — MA/EMA/MACD/RSI/BOLL/ATR/volume-ratio with golden tests"
```

### Task 5: 工具层扩展一 — K线/指标/watchlist/行情总览

**Files:**
- Modify: `backend/agent/tools.py`（追加函数，既有 4 个函数不动）
- Test: `backend/tests/test_chat_tools.py`

**Interfaces:**
- Consumes: `klines.fetcher.fetch_klines`、`klines.indicators.compute_indicators`、`sources.pine_screener.fetch_watchlists`、`sources.exchanges.fetch_all_tickers/fetch_all_funding_rates`、已有 `_throttle()`
- Produces（Task 8 runtime 注册这些函数为 agent 工具）:
  - `get_klines(symbol: str, interval: str, limit: int = 100) -> dict`（`{"symbol", "interval", "candles": [[open_time,o,h,l,c,v], …], "last_closed"}`，limit 钳制 ≤300）
  - `get_indicators(symbol: str, interval: str) -> dict`（compute_indicators 输出 + symbol/interval/last_close）
  - `list_watchlists() -> dict`（`{"watchlists": [{"name", "id"}, …]}`）
  - `market_overview(top_n: int = 10) -> dict`（涨幅/跌幅榜 + 资金费率极值，Binance）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_tools.py
from unittest.mock import patch
from agent import tools as T
from klines.models import Candle


def _mk(i, close, closed=True):
    return Candle(open_time=i * 1000, close_time=i * 1000 + 999, open=close,
                  high=close + 1, low=close - 1, close=close, volume=100.0, closed=closed)


def test_get_klines_compact_and_clamped():
    candles = [_mk(i, 100 + i) for i in range(50)] + [_mk(50, 150, closed=False)]
    with patch.object(T, "fetch_klines", return_value=candles) as fk:
        out = T.get_klines("BTCUSDT", "1h", limit=999)
        assert fk.call_args.kwargs["limit"] == 300          # 钳制
    assert out["candles"][0] == [0, 100.0, 101.0, 99.0, 100.0, 100.0]
    assert out["last_closed"] is False                      # 末棒未收盘如实告知
    assert len(out["candles"]) == 51


def test_get_klines_error_returned_not_raised():
    with patch.object(T, "fetch_klines", side_effect=RuntimeError("boom")):
        out = T.get_klines("BTCUSDT", "1h")
    assert "error" in out


def test_get_indicators_shape():
    candles = [_mk(i, 100 + i * 0.1) for i in range(80)]
    with patch.object(T, "fetch_klines", return_value=candles):
        out = T.get_indicators("BTCUSDT", "4h")
    assert out["symbol"] == "BTCUSDT" and "macd" in out["indicators"]


def test_list_watchlists_wraps_mapping():
    with patch("sources.pine_screener.fetch_watchlists",
               return_value={"主列表": 1, "山寨": 2}):
        out = T.list_watchlists()
    assert {"name": "主列表", "id": 1} in out["watchlists"]


def test_market_overview_sorts_and_trims():
    tickers = [{"symbol": f"S{i}", "exchange": "Binance", "lastPrice": 1,
                "priceChangePercent": float(i), "volume24h": 1000} for i in range(30)]
    funding = [{"symbol": "S1", "exchange": "Binance", "fundingRate": 0.001}]
    with patch.object(T, "fetch_all_tickers", return_value=(tickers, None)), \
         patch.object(T, "fetch_all_funding_rates", return_value=(funding, None)):
        out = T.market_overview(top_n=5)
    assert len(out["gainers"]) == 5 and out["gainers"][0]["priceChangePercent"] == 29.0
    assert len(out["losers"]) == 5 and out["losers"][0]["priceChangePercent"] == 0.0
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_tools.py -v`
Expected: FAIL with `AttributeError: get_klines`

- [ ] **Step 3: 实现（追加到 `backend/agent/tools.py` 末尾）**

```python
# ---- chat agent tools（Phase 2 追加；全只读）----

def get_klines(symbol: str, interval: str, limit: int = 100) -> dict:
    """紧凑 K 线数组 [[open_time, open, high, low, close, volume], ...]，
    时间升序；limit 钳制到 300（token 保护）。"""
    limit = max(10, min(int(limit), 300))
    _throttle()
    try:
        candles = fetch_klines(symbol, interval, limit=limit)
    except Exception as e:
        return {"error": f"K线获取失败: {e}"}
    if not candles:
        return {"error": "K线为空"}
    return {
        "symbol": symbol.upper(), "interval": interval,
        "candles": [[c.open_time, c.open, c.high, c.low, c.close, c.volume]
                    for c in candles],
        "last_closed": candles[-1].closed,
    }


def get_indicators(symbol: str, interval: str) -> dict:
    """核心六件套指标当前值（MA/EMA/MACD/RSI/BOLL/ATR/量比），基于已收盘K线。"""
    from klines.indicators import compute_indicators
    _throttle()
    try:
        candles = fetch_klines(symbol, interval, limit=120)
    except Exception as e:
        return {"error": f"K线获取失败: {e}"}
    closed = [c for c in candles if c.closed]
    if len(closed) < 30:
        return {"error": f"已收盘K线不足（{len(closed)} 根），无法计算指标"}
    return {"symbol": symbol.upper(), "interval": interval,
            "last_close": closed[-1].close,
            "indicators": compute_indicators(candles)}


def list_watchlists() -> dict:
    """TradingView 关注列表（name -> id），run_screener_scan 的前置。"""
    from sources.pine_screener import fetch_watchlists
    try:
        mapping = fetch_watchlists()
    except Exception as e:
        return {"error": f"watchlist 获取失败（可能 cookie 过期）: {e}"}
    return {"watchlists": [{"name": k, "id": v} for k, v in mapping.items()]}


def market_overview(top_n: int = 10) -> dict:
    """涨跌榜 + 资金费率极值（Binance USDT-M）。"""
    top_n = max(3, min(int(top_n), 20))
    tickers, _ = fetch_all_tickers()
    funding, _ = fetch_all_funding_rates()
    bn = [t for t in tickers if t["exchange"] == "Binance"]
    by_chg = sorted(bn, key=lambda t: t["priceChangePercent"], reverse=True)
    fr = sorted((f for f in funding if f["exchange"] == "Binance"),
                key=lambda f: f["fundingRate"])
    slim = lambda t: {"symbol": t["symbol"], "lastPrice": t["lastPrice"],
                      "priceChangePercent": t["priceChangePercent"],
                      "volume24h": t["volume24h"]}
    return {"gainers": [slim(t) for t in by_chg[:top_n]],
            "losers": [slim(t) for t in reversed(by_chg[-top_n:])],
            "funding_extremes": {"lowest": fr[:5], "highest": fr[-5:]} if fr else {}}
```

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_tools.py tests/test_agent_tools.py -v`
Expected: PASS（既有 test_agent_tools.py 不受影响）

- [ ] **Step 5: Commit**

```bash
git add backend/agent/tools.py backend/tests/test_chat_tools.py
git commit -m "feat(chat): read-only tools — compact klines, indicator six-pack, watchlists, market overview"
```

### Task 6: 工具层扩展二 — 筛选器扫描（进度回调）+ 账户只读

**Files:**
- Modify: `backend/agent/tools.py`（继续追加）
- Test: `backend/tests/test_chat_tools_scan.py`

**Interfaces:**
- Consumes: `sources.pine_screener.run_screener/build_cross_analysis/SCREENER_NAMES/list_screeners`、`trading.service.get_account/list_open_orders`（只读）
- Produces:
  - `run_screener_scan(screener_keys: list[str], timeframes: list[str], watchlist_id: int, progress_cb=None) -> dict` — progress_cb 签名 `(done: int, total: int, note: str)`；返回 `{"results": [{key,label,resolution,symbols,count}...], "cross": …, "errors": […]}`；combo 上限 12
  - `account_overview(credential_id: int) -> dict` — 余额/持仓/挂单只读

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_tools_scan.py
from unittest.mock import patch
from agent import tools as T


def test_scan_iterates_combos_with_progress_and_cross():
    calls, progress = [], []

    def fake_run(folder, name, res, wl):
        calls.append((folder, name, res, wl))
        return ["BINANCE:BTCUSDT.P"] if name == "oversold_zone" else []

    with patch("sources.pine_screener.run_screener", side_effect=fake_run):
        out = T.run_screener_scan(
            ["oscillator/oversold_zone", "oscillator/overbought_zone"],
            ["1h", "4h"], watchlist_id=7,
            progress_cb=lambda d, t, n: progress.append((d, t, n)))
    assert len(calls) == 4 and calls[0] == ("oscillator", "oversold_zone", "1h", 7)
    assert [p[:2] for p in progress] == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert len(out["results"]) == 4
    assert out["cross"]["screener_overlap"] == {}          # 单筛选器命中不构成 overlap
    assert out["errors"] == []


def test_scan_per_combo_error_isolated():
    def fake_run(folder, name, res, wl):
        raise RuntimeError("cookie expired")
    with patch("sources.pine_screener.run_screener", side_effect=fake_run):
        out = T.run_screener_scan(["oscillator/oversold_zone"], ["1h"], 0)
    assert out["results"] == [] and len(out["errors"]) == 1
    assert "cookie" in out["errors"][0]["error"]


def test_scan_rejects_bad_key_and_too_many_combos():
    out = T.run_screener_scan(["not/exists"], ["1h"], 0)
    assert "error" in out
    out = T.run_screener_scan(["oscillator/oversold_zone"] * 5, ["5m", "15m", "1h"], 0)
    assert "error" in out                                   # 15 combos > 12


def test_account_overview_readonly_wrap():
    with patch("trading.service.get_account",
               return_value={"env": "testnet", "total_wallet_balance": 100.0,
                             "available_balance": 90.0, "total_unrealized_pnl": 1.0,
                             "balances": [], "positions": [{"symbol": "BTCUSDT"}]}), \
         patch("trading.service.list_open_orders", return_value=[{"orderId": 1}]):
        out = T.account_overview(credential_id=3)
    assert out["env"] == "testnet" and out["open_orders"] == [{"orderId": 1}]


def test_account_overview_error_returned():
    with patch("trading.service.get_account", side_effect=RuntimeError("auth")):
        out = T.account_overview(credential_id=3)
    assert "error" in out
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_tools_scan.py -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: 实现（追加到 `backend/agent/tools.py`）**

```python
MAX_SCAN_COMBOS = 12    # TradingView 1req/2s：12 combo ≈ 24s，对话可接受上限


def run_screener_scan(screener_keys: list[str], timeframes: list[str],
                      watchlist_id: int, progress_cb=None) -> dict:
    """跑筛选器×周期组合。每个 combo 完成回调 progress_cb(done, total, note)。
    单 combo 失败不中断整个扫描（记入 errors）。受 TradingView 全局 2s 限流。"""
    from sources import pine_screener as ps
    valid = {f"{s['folder']}/{s['name']}": s["label"] for s in ps.list_screeners()}
    # 隐藏的合并背离筛选器也允许（向后兼容 key）
    valid.setdefault("oscillator/divergence", ps.SCREENER_NAMES["oscillator/divergence"])
    bad = [k for k in screener_keys if k not in valid]
    if bad:
        return {"error": f"未知筛选器 key: {bad}；可用: {sorted(valid)}"}
    bad_tf = [t for t in timeframes if t not in ps.VALID_RESOLUTIONS]
    if bad_tf:
        return {"error": f"非法周期: {bad_tf}；可用: {sorted(ps.VALID_RESOLUTIONS)}"}
    combos = [(k, tf) for k in screener_keys for tf in timeframes]
    if not combos:
        return {"error": "screener_keys 与 timeframes 都不能为空"}
    if len(combos) > MAX_SCAN_COMBOS:
        return {"error": f"组合数 {len(combos)} 超上限 {MAX_SCAN_COMBOS}"
                         "（限流 1 次/2 秒，请缩小范围分批扫）"}
    results, errors = [], []
    for i, (key, tf) in enumerate(combos, 1):
        folder, name = key.split("/", 1)
        label = valid[key]
        try:
            symbols = ps.run_screener(folder, name, tf, watchlist_id)
            results.append({"key": key, "label": label, "resolution": tf,
                            "symbols": symbols[:100], "count": len(symbols)})
            note = f"{label}@{tf} 命中 {len(symbols)}"
        except Exception as e:
            errors.append({"key": key, "resolution": tf, "error": str(e)[:300]})
            note = f"{label}@{tf} 失败"
        if progress_cb:
            progress_cb(i, len(combos), note)
    return {"results": results, "cross": ps.build_cross_analysis(results),
            "errors": errors,
            "hint": "空 symbols 可能是无信号，也可能是数据源失败——参考 errors 判断"}


def account_overview(credential_id: int) -> dict:
    """余额/持仓/挂单只读总览。本函数绝不 import 任何下单函数。"""
    _throttle()
    try:
        from trading.service import get_account, list_open_orders   # 均为只读
        acct = get_account(credential_id)
        acct["open_orders"] = list_open_orders(credential_id)
        return acct
    except Exception as e:
        return {"error": f"账户查询失败: {e}"}
```

注意 `list_screeners()` 返回元素的键名——实现前用
`python -c "from sources.pine_screener import list_screeners; print(list_screeners()[:2])"`
确认（folder/name/label 若名字不同，按实际键名适配 `valid` 的构造，测试同步调整）。

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_tools_scan.py -v`
Expected: PASS

- [ ] **Step 5: 红线自查 + Commit**

Run: `cd backend && grep -n "place_order\|close_position\|close_all\|cancel_order\|cancel_all" agent/tools.py`
Expected: 仅命中注释或无输出（`from trading.service import get_account, list_open_orders` 不含下单函数）

```bash
git add backend/agent/tools.py backend/tests/test_chat_tools_scan.py
git commit -m "feat(chat): screener scan tool with per-combo progress + read-only account overview"
```

---

## Phase 3 — 语义档案、runtime、worker、API/SSE

### Task 7: 筛选器语义档案 + 对话 prompt

**Files:**
- Create: `backend/agent/chat/semantics.py`
- Create: `backend/agent/chat/prompts.py`
- Test: `backend/tests/test_chat_semantics.py`

**Interfaces:**
- Consumes: Task 1 `screener_semantics` 表、`sources.pine_screener.SCREENER_NAMES`
- Produces:
  - `semantics.seed_defaults() -> int`（空表时写入 8 份初稿，返回写入数；幂等 INSERT OR IGNORE）
  - `semantics.get_all() -> list[dict]`（键 key/label/meaning/bias/usage/caveats/combos/updated_at）
  - `semantics.upsert(key: str, fields: dict) -> bool`（key 必须 ∈ SCREENER_NAMES）
  - `prompts.CHAT_PROMPT_VERSION = "chat-v1"`
  - `prompts.build_system_prompt() -> str`（注入语义档案 + 工具指引 + 当前 UTC 时间）
  - `prompts.render_history(messages: list[dict]) -> str`（user/assistant 正文，工具轨迹不回灌）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_semantics.py
from agent.chat import semantics
from agent.chat.prompts import build_system_prompt, render_history


def test_seed_defaults_idempotent_and_complete():
    n = semantics.seed_defaults()
    assert n == 8
    assert semantics.seed_defaults() == 0          # 二次幂等
    rows = semantics.get_all()
    assert len(rows) == 8
    keys = {r["key"] for r in rows}
    assert "oscillator/divergence_bottom" in keys and "trend/shadows" in keys
    assert all(r["meaning"] for r in rows)          # 初稿无空 meaning


def test_upsert_validates_key():
    semantics.seed_defaults()
    assert semantics.upsert("oscillator/oversold_zone", {"bias": "long（超跌反弹）"}) is True
    assert semantics.get_all_map()["oscillator/oversold_zone"]["bias"] == "long（超跌反弹）"
    assert semantics.upsert("not/exists", {"bias": "x"}) is False


def test_system_prompt_injects_semantics_and_rules():
    semantics.seed_defaults()
    sp = build_system_prompt()
    assert "底背离" in sp and "顶背离" in sp          # 语义档案已注入
    assert "不下单" in sp or "不能下单" in sp          # 红线句
    assert "纯技术分析" in sp
    assert "UTC" in sp                                # 时间戳


def test_render_history_skips_trace_and_orders_by_id():
    msgs = [
        {"id": 1, "role": "user", "content": "看下 BTC", "trace": None},
        {"id": 2, "role": "assistant", "content": "BTC 结构偏多",
         "trace": {"steps": [{"tool": "get_klines"}]}},
    ]
    text = render_history(msgs)
    assert "看下 BTC" in text and "BTC 结构偏多" in text
    assert "get_klines" not in text                   # 工具轨迹不回灌
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_semantics.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# backend/agent/chat/semantics.py
"""筛选器语义档案：agent 理解基石筛选器的机制。初稿由 Claude 起草，
用户在 Settings UI 审校修改（Task 18）。"""
from database import get_db
from config import settings
from sources.pine_screener import SCREENER_NAMES

_EDITABLE = ("meaning", "bias", "usage", "caveats", "combos")

DEFAULT_SEMANTICS = {
    "oscillator/divergence_top": {
        "meaning": "价格创新高而振荡指标未同步新高：上涨动能衰竭的见顶预警。",
        "bias": "short（做空倾向）",
        "usage": "趋势末端、超买区出现时参考价值最高；等确认K线（如长上影/吞没）再行动。",
        "caveats": "强趋势中背离会连续钝化多次，单独使用胜率一般；背离不是入场信号而是预警。",
        "combos": "叠加超买、长上影、跨周期共振（如 1h+4h 同时背离）可显著提高可信度。"},
    "oscillator/divergence_bottom": {
        "meaning": "价格创新低而振荡指标未同步新低：下跌动能衰竭的筑底预警。",
        "bias": "long（做多倾向）",
        "usage": "急跌后的低位出现时参考价值最高；等确认K线（如长下影/阳包阴）再行动。",
        "caveats": "阴跌趋势里底背离可连续钝化；低位判断需结合更高周期结构。",
        "combos": "叠加超卖、长下影、跨周期共振可显著提高可信度。"},
    "oscillator/overbought_zone": {
        "meaning": "振荡指标进入高位区间：短期涨幅过大，存在回调压力。",
        "bias": "short（回调倾向，弱信号）",
        "usage": "震荡市里逢高减仓/等回调的参考；本身不构成做空依据。",
        "caveats": "强趋势中超买可长期钝化——顺势行情里做空超买是常见亏损来源。",
        "combos": "与顶背离、长上影叠加才有交易价值；单独出现只做观察。"},
    "oscillator/oversold_zone": {
        "meaning": "振荡指标进入低位区间：短期跌幅过大，存在反弹动能。",
        "bias": "long（反弹倾向，弱信号）",
        "usage": "震荡市里逢低布局的参考；本身不构成做多依据。",
        "caveats": "瀑布行情中超卖会持续钝化；抄底需等待止跌结构。",
        "combos": "与底背离、长下影叠加才有交易价值；单独出现只做观察。"},
    "oscillator/volatility_alert": {
        "meaning": "波动率异常放大（如带宽/ATR 突增）：行情启动或恐慌释放。",
        "bias": "中性（无方向信息）",
        "usage": "提示『这里有事发生』，用于把注意力引向该标的，方向要靠其他信号判定。",
        "caveats": "不含方向；消息面驱动的脉冲也会触发（本系统不做消息面判断）。",
        "combos": "与趋势爆量同时出现提示新趋势启动；与背离叠加提示反转加速。"},
    "oscillator/divergence": {
        "meaning": "顶背离与底背离的合并基础筛选器（方向未拆分，前端默认隐藏）。",
        "bias": "双向（看具体命中方向）",
        "usage": "旧任务兼容用；分析时优先使用方向拆分后的顶背离/底背离。",
        "caveats": "命中结果不区分顶/底，直接使用会丢失方向信息。",
        "combos": "同顶背离/底背离。"},
    "trend/shadows": {
        "meaning": "单根K线出现极端长上影或长下影：该价格区被明确拒绝。",
        "bias": "双向（长上影偏空、长下影偏多）",
        "usage": "出现在关键结构位（前高前低、枢轴）时最有效，是入场确认信号之一。",
        "caveats": "趋势中继也常见影线（洗盘），孤立K线的影线意义有限。",
        "combos": "与超买/超卖、背离、结构枢轴位叠加构成完整的反转确认链。"},
    "trend/trend_volume_spike": {
        "meaning": "放量伴随趋势方向突破：资金参与的趋势加速/启动信号。",
        "bias": "顺势双向（突破方向）",
        "usage": "确认突破有效性、识别主升/主跌段启动；顺势跟随优于逆势。",
        "caveats": "高位放量可能是出货（量价背离）；低流动性币放量易被操纵。",
        "combos": "与波动警报共振提示新趋势；结合资金费率判断拥挤度。"},
}


def seed_defaults() -> int:
    db = get_db(settings.db_path)
    try:
        n = 0
        for key, f in DEFAULT_SEMANTICS.items():
            cur = db.execute(
                """INSERT OR IGNORE INTO screener_semantics
                   (key, meaning, bias, usage, caveats, combos)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key, f["meaning"], f["bias"], f["usage"], f["caveats"], f["combos"]))
            n += cur.rowcount
        db.commit()
        return n
    finally:
        db.close()


def get_all() -> list[dict]:
    db = get_db(settings.db_path)
    try:
        rows = db.execute("SELECT * FROM screener_semantics ORDER BY key").fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        d = dict(r)
        d["label"] = SCREENER_NAMES.get(d["key"], d["key"])
        out.append(d)
    return out


def get_all_map() -> dict:
    return {r["key"]: r for r in get_all()}


def upsert(key: str, fields: dict) -> bool:
    if key not in SCREENER_NAMES:
        return False
    sets, params = [], []
    for f in _EDITABLE:
        if f in fields and fields[f] is not None:
            sets.append(f"{f} = ?")
            params.append(str(fields[f]))
    if not sets:
        return True
    db = get_db(settings.db_path)
    try:
        db.execute("INSERT OR IGNORE INTO screener_semantics (key) VALUES (?)", (key,))
        db.execute(f"UPDATE screener_semantics SET {', '.join(sets)}, "
                   "updated_at = datetime('now') WHERE key = ?", (*params, key))
        db.commit()
        return True
    finally:
        db.close()
```

```python
# backend/agent/chat/prompts.py
"""Chat agent 的 system prompt 组装与历史渲染。"""
from datetime import datetime, timezone

CHAT_PROMPT_VERSION = "chat-v1"

_BASE = """你是 WoHub 的加密永续合约技术分析助手，在网页对话里帮用户看盘、跑筛选、分析结构。

硬约束（任何情况下不可违反）：
- 纯技术分析：只依据价格、成交量、衍生指标与市场结构；不引入任何消息面/情绪/链上判断。
- 你没有也永远不会有下单能力：不下单、不改单、不撤单。用户想执行时，引导其去交易终端页人工确认（可给出 /trade?symbol=XXX&direction=long|short 形式的预填链接）。
- 以损定仓：仓位大小永远由结构止损反推（get_position_plan 工具），你不做任何自创的风险/仓位计算。
- 不确定就明说。宁可说『证据不足』也不编造斩钉截铁的结论。

工具使用指引：
- run_screener_scan 是长任务（限流 1 次/2 秒），组合数超过上限会被拒绝；先用 list_watchlists 拿 watchlist_id。
- 筛选结果为空有双义性：可能『无信号』也可能『数据源失败』——看返回里的 errors 字段区分，不要过度解读空集。
- K线形态的方向标签是启发式，不是既定事实。
- get_kline_structure / capture_chart 有每轮配额（深评预算），省着用在最值得的标的上。
- 回答用中文，结论先行，给出可复核的数值证据。"""


def _semantics_block() -> str:
    from agent.chat.semantics import get_all
    rows = get_all()
    if not rows:
        return ""
    lines = ["\n【筛选器语义档案】（这些是本系统内置 Pine 筛选器的含义，跑扫描前先对照）"]
    for r in rows:
        lines.append(f"- {r['label']}（key={r['key']}）：{r['meaning']}"
                     f" 方向：{r['bias']}。用法：{r['usage']}"
                     f" 局限：{r['caveats']} 建议叠加：{r['combos']}")
    return "\n".join(lines)


def build_system_prompt() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"{_BASE}\n{_semantics_block()}\n\n当前时间：{now}"


def render_history(messages: list[dict]) -> str:
    """最近历史的纯文本渲染：只回灌正文，不回灌工具轨迹（token 纪律）。"""
    lines = []
    for m in messages:
        who = "用户" if m["role"] == "user" else "助手"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{who}：{content}")
    return "\n".join(lines)
```

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_semantics.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/chat/semantics.py backend/agent/chat/prompts.py backend/tests/test_chat_semantics.py
git commit -m "feat(chat): screener semantics profiles (8 seeded drafts) + chat system prompt assembly"
```

### Task 8: 轮次执行器 `runtime.py`

**Files:**
- Create: `backend/agent/chat/runtime.py`
- Modify: `backend/agent/llm.py`（`build_model` 加可选 `model_name` 参数）
- Test: `backend/tests/test_chat_runtime.py`

**Interfaces:**
- Consumes: Task 2/3/7 全部、`agent.tools`（Task 5/6）、`agent.llm.build_model`、`agent.config.load_config`
- Produces:
  - `run_turn(turn_row, model_override=None) -> None`（worker 调用；turn_row 为 `claim_next_turn()` 返回行）
  - `ChatDeps`（dataclass：turn_id/budget/credential_id/trace/emit/check_cancel）
  - `TurnCancelled` 异常
  - 事件序列契约：`text_delta* → (tool_start → tool_progress* → tool_end)* → turn_done|turn_error|cancelled`（顺序按实际交错）

- [ ] **Step 1: 修改 `backend/agent/llm.py`**

```python
def build_model(cfg, model_name: str | None = None):
    """构建 LLM 模型实例（Anthropic/OpenAI）。model_name 覆盖 cfg.model
    （视觉模型槽位用）。"""
    if not cfg.api_key:
        raise ValueError("agent LLM api_key 未配置")
    name = model_name or cfg.model
    if cfg.provider == "anthropic":
        return AnthropicModel(name, provider=AnthropicProvider(api_key=cfg.api_key))
    kwargs = {"api_key": cfg.api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return OpenAIChatModel(name, provider=OpenAIProvider(**kwargs))
```

（函数体其余不变；`docstring` 中删去过时的 Task 引用。）

- [ ] **Step 2: 写失败测试**

```python
# backend/tests/test_chat_runtime.py
import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.models.function import FunctionModel, AgentInfo, DeltaToolCall
from agent.chat import store, events, runtime
from agent.config import save_config


def _prep(content="看下大盘"):
    save_config({"provider": "openai", "model": "m", "api_key": "k", "enabled": True})
    sid = store.create_session()
    mid = store.add_message(sid, "user", content)
    tid = store.create_turn(sid, mid)
    return sid, mid, store.claim_next_turn()


def _types(tid):
    return [e["type"] for e in events.turn_events(tid)]


def test_run_turn_end_to_end_with_testmodel():
    sid, mid, row = _prep()
    runtime.run_turn(row, model_override=TestModel(call_tools=[], custom_output_text="大盘偏弱"))
    types = _types(row["id"])
    assert "text_delta" in types and types[-1] == "turn_done"
    msgs = store.list_messages(sid)
    assert msgs[-1]["role"] == "assistant" and "大盘偏弱" in msgs[-1]["content"]
    # 自动标题：新会话 → 首条消息截断
    assert store.list_sessions()[0]["title"] == "看下大盘"
    db_turn = store.active_turn(sid)
    assert db_turn is None                                    # done


def test_tool_events_emitted_via_functionmodel():
    sid, mid, row = _prep("BTC 现在多少钱")

    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:      # 首个请求 → 调工具
            yield {0: DeltaToolCall(name="get_market_snapshot",
                                    json_args='{"symbols": ["BTCUSDT"]}')}
        else:                       # 工具结果回来 → 出文本
            yield "BTC 报价已取"

    from unittest.mock import patch
    with patch("agent.tools.market_snapshot",
               return_value={"BTCUSDT": {"lastPrice": 100000}}):
        runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))
    types = _types(row["id"])
    i_start, i_end = types.index("tool_start"), types.index("tool_end")
    assert i_start < i_end and types[-1] == "turn_done"
    trace = store.list_messages(sid)[-1]["trace"]
    assert trace["steps"][0]["tool"] == "market_snapshot"


def test_precancelled_turn_short_circuits():
    sid, mid, row = _prep()
    store.request_cancel(row["id"])
    runtime.run_turn(row, model_override=TestModel(call_tools=[], custom_output_text="x"))
    assert _types(row["id"])[-1] == "cancelled"
    db = store.active_turn(sid)
    assert db is None                                         # cancelled 落库


def test_model_failure_marks_failed_with_error_message():
    sid, mid, row = _prep()

    async def boom(messages, info):
        raise RuntimeError("provider 500")
        yield  # pragma: no cover

    runtime.run_turn(row, model_override=FunctionModel(stream_function=boom))
    assert _types(row["id"])[-1] == "turn_error"
    last = store.list_messages(sid)[-1]
    assert last["role"] == "assistant" and last["error"]


def test_disabled_agent_fails_fast():
    save_config({"enabled": False})
    sid = store.create_session()
    tid = store.create_turn(sid, store.add_message(sid, "user", "x"))
    row = store.claim_next_turn()
    runtime.run_turn(row)
    assert _types(tid)[-1] == "turn_error"
```

- [ ] **Step 3: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_runtime.py -v`
Expected: FAIL with `ImportError: runtime`

- [ ] **Step 4: 实现**

```python
# backend/agent/chat/runtime.py
"""单轮执行器：组装 prompt → PydanticAI agent.iter 工具循环 → 事件落库 →
assistant 消息落库。本模块及 import 链禁止出现任何下单函数（红线）。"""
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional

from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits
from pydantic_ai.messages import PartDeltaEvent, TextPartDelta

from app_logger import log as applog
from agent import tools as T
from agent.llm import build_model
from agent.chat import store, events
from agent.chat.prompts import build_system_prompt, render_history, CHAT_PROMPT_VERSION

HISTORY_LIMIT = 20          # 发给模型的历史消息条数上限（规格 §4）
FLUSH_INTERVAL = 0.1        # text_delta 聚合窗口（秒）
FLUSH_CHARS = 400
RESULT_TRUNC = 2000         # 工具结果截断（trace 与回传模型同规则）


class TurnCancelled(Exception):
    pass


@dataclass
class ChatDeps:
    turn_id: int
    budget: T.ToolBudget
    credential_id: Optional[int]
    trace: list = field(default_factory=list)

    def emit(self, type_: str, payload: dict) -> None:
        events.append_event(self.turn_id, type_, payload)

    def check_cancel(self) -> None:
        if store.cancel_requested(self.turn_id):
            raise TurnCancelled()


class _DeltaBuffer:
    """text_delta 事件聚合：~100ms 或 400 字符一批，不逐 token 写库。"""

    def __init__(self, deps: ChatDeps):
        self.deps = deps
        self.buf = ""
        self.parts: list[str] = []
        self.last = time.monotonic()

    def add(self, text: str) -> None:
        self.buf += text
        if len(self.buf) >= FLUSH_CHARS or time.monotonic() - self.last >= FLUSH_INTERVAL:
            self.flush()
            self.deps.check_cancel()          # 流式块之间的取消检查点

    def flush(self) -> None:
        if self.buf:
            self.deps.emit("text_delta", {"text": self.buf})
            self.parts.append(self.buf)
            self.buf = ""
        self.last = time.monotonic()

    def full_text(self) -> str:
        return "".join(self.parts) + self.buf


def _tool(ctx: RunContext[ChatDeps], name: str, args: dict, fn) -> dict:
    """统一工具包装：取消检查 → tool_start → 执行 → trace/tool_end。
    事件由我们自己发（不依赖框架事件类名，抗版本漂移）。"""
    d = ctx.deps
    d.check_cancel()
    d.emit("tool_start", {"tool": name, "args": args})
    t0 = time.monotonic()
    try:
        out = fn()
    except TurnCancelled:
        raise
    except Exception as e:                      # 工具失败不终止轮次：错误回传模型
        out = {"error": f"工具执行异常: {e}"}
    ok = not (isinstance(out, dict) and out.get("error"))
    raw = json.dumps(out, ensure_ascii=False)[:RESULT_TRUNC]
    d.trace.append({"tool": name, "args": args, "result": raw})
    d.emit("tool_end", {"tool": name, "ok": ok, "summary": raw[:400],
                        "elapsed_ms": int((time.monotonic() - t0) * 1000)})
    return out


def _build_agent(cfg, model) -> Agent:
    agent = Agent(model, output_type=str, system_prompt=build_system_prompt(),
                  deps_type=ChatDeps)

    @agent.tool
    def get_market_snapshot(ctx: RunContext[ChatDeps], symbols: list[str]) -> dict:
        """实时行情快照：价格/24h涨跌/成交额/资金费率。symbols 用 clean 格式（如 BTCUSDT）。"""
        return _tool(ctx, "market_snapshot", {"symbols": symbols},
                     lambda: T.market_snapshot(symbols))

    @agent.tool
    def get_market_overview(ctx: RunContext[ChatDeps], top_n: int = 10) -> dict:
        """全市场总览：涨跌榜 + 资金费率极值（Binance USDT-M）。"""
        return _tool(ctx, "market_overview", {"top_n": top_n},
                     lambda: T.market_overview(top_n))

    @agent.tool
    def get_klines(ctx: RunContext[ChatDeps], symbol: str, interval: str,
                   limit: int = 100) -> dict:
        """原始K线数组 [[open_time,open,high,low,close,volume],…]，limit≤300。"""
        return _tool(ctx, "get_klines", {"symbol": symbol, "interval": interval,
                                         "limit": limit},
                     lambda: T.get_klines(symbol, interval, limit))

    @agent.tool
    def get_indicators(ctx: RunContext[ChatDeps], symbol: str, interval: str) -> dict:
        """核心指标当前值：MA/EMA/MACD/RSI/BOLL/ATR/量比（基于已收盘K线）。"""
        return _tool(ctx, "get_indicators", {"symbol": symbol, "interval": interval},
                     lambda: T.get_indicators(symbol, interval))

    @agent.tool
    def get_kline_structure(ctx: RunContext[ChatDeps], symbol: str, interval: str) -> dict:
        """K线结构深评：形态/分类/上下方枢轴/ATR/近端统计。有每轮配额，省着用。"""
        return _tool(ctx, "kline_structure", {"symbol": symbol, "interval": interval},
                     lambda: T.kline_summary(symbol, interval, ctx.deps.budget))

    @agent.tool
    def get_signal_history(ctx: RunContext[ChatDeps], symbol: str, indicator: str) -> dict:
        """该 symbol×筛选器label 的历史信号 1h/4h/24h 表现（方向盲原始收益）。"""
        return _tool(ctx, "signal_history", {"symbol": symbol, "indicator": indicator},
                     lambda: T.signal_history(symbol, indicator))

    @agent.tool
    def list_watchlists(ctx: RunContext[ChatDeps]) -> dict:
        """TradingView 关注列表（跑筛选扫描前先用这个拿 watchlist_id）。"""
        return _tool(ctx, "list_watchlists", {}, T.list_watchlists)

    @agent.tool
    def run_screener_scan(ctx: RunContext[ChatDeps], screener_keys: list[str],
                          timeframes: list[str], watchlist_id: int) -> dict:
        """跑 Pine 筛选器扫描（长任务：限流 1 次/2 秒，组合上限 12）。
        screener_keys 用语义档案里的 key（如 oscillator/divergence_bottom）。"""
        d = ctx.deps

        def cb(done, total, note):
            d.check_cancel()
            d.emit("tool_progress", {"tool": "run_screener_scan",
                                     "done": done, "total": total, "note": note})

        return _tool(ctx, "run_screener_scan",
                     {"screener_keys": screener_keys, "timeframes": timeframes,
                      "watchlist_id": watchlist_id},
                     lambda: T.run_screener_scan(screener_keys, timeframes,
                                                 watchlist_id, progress_cb=cb))

    if cfg.credential_id:
        @agent.tool
        def get_position_plan(ctx: RunContext[ChatDeps], symbol: str, interval: str,
                              direction: str) -> dict:
            """只读仓位规划预览（结构止损/RR/可行性）。不会下单。direction: long|short"""
            return _tool(ctx, "position_plan", {"symbol": symbol, "interval": interval,
                                                "direction": direction},
                         lambda: T.position_plan_preview(symbol, interval, direction,
                                                         ctx.deps.credential_id))

        @agent.tool
        def get_account_overview(ctx: RunContext[ChatDeps]) -> dict:
            """当前账户只读总览：余额/持仓/挂单。"""
            return _tool(ctx, "account_overview", {},
                         lambda: T.account_overview(ctx.deps.credential_id))

    return agent


def _build_prompt(session_id: int, user_message_id: int):
    msgs = store.list_messages(session_id)
    current = next(m for m in msgs if m["id"] == user_message_id)
    history = [m for m in msgs if m["id"] < user_message_id][-HISTORY_LIMIT:]
    text = ""
    if history:
        text += "【对话历史（最近 %d 条）】\n%s\n\n【当前消息】\n" % (
            len(history), render_history(history))
    text += current.get("content") or ""
    if current.get("images"):
        # 视觉管道在 Task 14 接入；此前如实告知模型
        text += "\n（用户附带了 %d 张图片，当前部署未启用视觉功能，无法查看图片内容）" \
                % len(current["images"])
    return text, current


async def _drive(agent: Agent, prompt: str, deps: ChatDeps, cfg, buf: _DeltaBuffer):
    async with agent.iter(prompt, deps=deps,
                          usage_limits=UsageLimits(
                              request_limit=cfg.max_tool_calls + 2,
                              tool_calls_limit=cfg.max_tool_calls)) as run:
        async for node in run:
            if Agent.is_model_request_node(node):
                async with node.stream(run.ctx) as stream:
                    async for ev in stream:
                        if isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                            buf.add(ev.delta.content_delta)
        return run


def _usage_tokens(run) -> tuple[int, int]:
    try:
        u = run.usage() if callable(getattr(run, "usage", None)) else getattr(run, "usage", None)
    except Exception:
        return 0, 0
    if u is None:
        return 0, 0
    it = getattr(u, "input_tokens", None) or getattr(u, "request_tokens", 0) or 0
    ot = getattr(u, "output_tokens", None) or getattr(u, "response_tokens", 0) or 0
    return it, ot


def run_turn(turn_row, model_override=None) -> None:
    """worker 线程入口。所有出口都保证：turn 有终态 + 有对应事件。"""
    turn_id, session_id = turn_row["id"], turn_row["session_id"]
    from agent.config import load_config
    cfg = load_config()
    deps = ChatDeps(turn_id=turn_id,
                    budget=T.ToolBudget(deep_dive_limit=cfg.deep_dive_limit),
                    credential_id=cfg.credential_id)
    buf = _DeltaBuffer(deps)
    try:
        if store.cancel_requested(turn_id):
            raise TurnCancelled()
        if not cfg.enabled or (not cfg.api_key and model_override is None):
            raise RuntimeError("Agent 未启用或未配置 API Key（请到系统设置页配置）")
        prompt, current = _build_prompt(session_id, turn_row["user_message_id"])
        model = model_override or build_model(cfg)
        agent = _build_agent(cfg, model)
        run = asyncio.run(_drive(agent, prompt, deps, cfg, buf))
        buf.flush()
        in_tok, out_tok = _usage_tokens(run)
        mid = store.add_message(session_id, "assistant", buf.full_text(),
                                trace={"prompt_version": CHAT_PROMPT_VERSION,
                                       "steps": deps.trace},
                                model="test" if model_override else cfg.model,
                                input_tokens=in_tok, output_tokens=out_tok)
        deps.emit("turn_done", {"message_id": mid,
                                "input_tokens": in_tok, "output_tokens": out_tok})
        store.finish_turn(turn_id, "done")
        _maybe_autotitle(session_id, current)
    except TurnCancelled:
        buf.flush()
        store.add_message(session_id, "assistant", buf.full_text() or "（已停止）",
                          trace={"steps": deps.trace}, error="cancelled")
        deps.emit("cancelled", {})
        store.finish_turn(turn_id, "cancelled")
    except Exception as e:
        applog("chat", "error", f"turn #{turn_id} failed: {e!r}")
        buf.flush()
        store.add_message(session_id, "assistant", buf.full_text(),
                          trace={"steps": deps.trace}, error=str(e)[:2000])
        deps.emit("turn_error", {"error": str(e)[:500]})
        store.finish_turn(turn_id, "failed")


def _maybe_autotitle(session_id: int, current: dict) -> None:
    sess = [s for s in store.list_sessions() if s["id"] == session_id]
    if sess and sess[0]["title"] == "新会话":
        text = (current.get("content") or "图片分析").strip()
        store.rename_session(session_id, text[:30])
```

- [ ] **Step 5: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_runtime.py -v`
Expected: PASS。若 `agent.iter`/`node.stream` 的事件类名在已装的 pydantic-ai 小版本里不同（ImportError/AttributeError），先运行
`python -c "import pydantic_ai; print(pydantic_ai.__version__)"`，再对照该版本文档修正 import——**只允许改 `_drive` 与 import 行，不许改事件写库契约**。

- [ ] **Step 6: Commit**

```bash
git add backend/agent/chat/runtime.py backend/agent/llm.py backend/tests/test_chat_runtime.py
git commit -m "feat(chat): turn runtime — pydantic-ai iter loop, delta batching, tool events, cancel/fail paths"
```

### Task 9: 队列 worker + lifespan 接线

**Files:**
- Create: `backend/agent/chat/worker.py`
- Modify: `backend/main.py:33-37`（换用 chat worker）
- Test: `backend/tests/test_chat_worker.py`

**Interfaces:**
- Consumes: `store.claim_next_turn/recover_interrupted`、`runtime.run_turn`
- Produces: `start_worker(interval=0.5)` / `stop_worker()`（main.py lifespan 调用）；启动时自动 `recover_interrupted()` + `semantics.seed_defaults()`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_worker.py
import time
from unittest.mock import patch
from agent.chat import store, worker, events


def test_worker_drains_queue_and_recovers():
    sid = store.create_session()
    # 制造一个"上次运行被打断"的 running turn
    t_stale = store.create_turn(sid, store.add_message(sid, "user", "old"))
    store.claim_next_turn()
    # 再排一个正常 queued turn
    t_new = store.create_turn(sid, store.add_message(sid, "user", "new"))

    done = []
    with patch.object(worker, "_process", side_effect=lambda row: done.append(row["id"])):
        worker.start_worker(interval=0.05)
        try:
            deadline = time.monotonic() + 3
            while not done and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            worker.stop_worker()
    assert done == [t_new]                       # 只处理 queued 的
    # stale running 被判 failed 且有事件
    assert [e["type"] for e in events.turn_events(t_stale)] == ["turn_error"]


def test_worker_seed_semantics_on_start():
    from agent.chat import semantics
    worker.start_worker(interval=0.05)
    worker.stop_worker()
    assert len(semantics.get_all()) == 8


def test_stop_worker_joins():
    worker.start_worker(interval=0.05)
    worker.stop_worker()
    assert worker._thread is None or not worker._thread.is_alive()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_worker.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: 实现**

```python
# backend/agent/chat/worker.py
"""Chat worker: single daemon thread draining chat_turns. Never runs in the
APScheduler pool; own short-lived DB connections; clean shutdown."""
import sys
import threading
from app_logger import log as applog
from agent.chat import store, events

_stop = threading.Event()
_thread = None


def _process(turn_row):
    from agent.chat.runtime import run_turn
    run_turn(turn_row)


def _loop(interval):
    mod = sys.modules[__name__]
    while not _stop.wait(interval):
        try:
            row = store.claim_next_turn()
            if row is None:
                continue
            try:
                mod._process(row)                 # 模块属性调用，测试可 patch
            except Exception as e:                # run_turn 自兜底；这里是最后防线
                applog("chat", "error", f"turn #{row['id']} crashed: {e!r}")
                events.append_event(row["id"], "turn_error", {"error": str(e)[:500]})
                store.finish_turn(row["id"], "failed")
        except Exception as e:
            applog("chat", "error", f"worker loop: {e!r}")


def start_worker(interval=0.5):
    global _thread
    if _thread and _thread.is_alive():
        return
    # 启动恢复：running→failed（补事件），queued 保留由循环自然续跑
    for tid in store.recover_interrupted():
        events.append_event(tid, "turn_error", {"error": "服务重启中断，可重试"})
    from agent.chat.semantics import seed_defaults
    seed_defaults()
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), daemon=True,
                               name="chat-worker")
    _thread.start()


def stop_worker():
    global _thread
    _stop.set()
    if _thread:
        _thread.join(timeout=10)
        if _thread.is_alive():
            applog("chat", "warn", "chat worker did not stop within 10s")
        _thread = None
```

`backend/main.py` 的 lifespan 中，把

```python
    from agent.worker import start_worker, stop_worker
    start_worker()
    yield
    stop_worker()
```

改为

```python
    from agent.chat.worker import start_worker, stop_worker
    start_worker()
    yield
    stop_worker()
```

（旧批量 worker 从此不再启动——executor 仍会写 queued 的 agent_runs 行但无人消费，属规格允许的过渡状态，Phase 7 连根移除。）

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_worker.py tests/test_chat_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/chat/worker.py backend/main.py backend/tests/test_chat_worker.py
git commit -m "feat(chat): queue-draining worker with restart recovery; lifespan switches to chat worker"
```

### Task 10: Chat API + SSE 端点

**Files:**
- Create: `backend/api/chat.py`
- Modify: `backend/api/__init__.py`（protected 注册 chat_router）
- Modify: `backend/config.py`（新增 `chat_uploads_dir`）
- Modify: `frontend/src/api/client.js`（chat 方法）
- Test: `backend/tests/test_chat_api.py`

**Interfaces:**
- Consumes: Task 2/3 store/events
- Produces（前端 Task 11-13 消费）:
  - `GET /api/chat/sessions` → `[{id,title,updated_at,message_count}]`
  - `POST /api/chat/sessions` `{title?}` → `{id}`
  - `PATCH /api/chat/sessions/{sid}` `{title}` / `DELETE /api/chat/sessions/{sid}`
  - `GET /api/chat/sessions/{sid}/messages` → `{messages, active_turn, active_events, last_event_id}`
  - `POST /api/chat/sessions/{sid}/messages`（multipart：`content` 文本 + `files[]` 图片）→ `{turn_id, message_id}`；已有进行中 turn 时 409
  - `POST /api/chat/turns/{tid}/cancel` → `{ok}`
  - `GET /api/chat/sessions/{sid}/stream?after=N` → SSE（`id:`/`event:`/`data:` 帧 + 15s 心跳注释）
  - `GET /api/chat/images/{kind}/{filename}`（kind ∈ upload|screenshot）
  - `settings.chat_uploads_dir`（env `CHAT_UPLOADS_DIR`，默认 `data/chat_uploads`）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_api.py
import asyncio
import os
import pytest
from agent.chat import store, events

PNG_1PX = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
           b"\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.mark.asyncio
async def test_session_crud(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        assert (await c.get("/api/chat/sessions")).json()[0]["id"] == sid
        r = await c.patch(f"/api/chat/sessions/{sid}", json={"title": "改名"})
        assert r.status_code == 200
        assert (await c.get("/api/chat/sessions")).json()[0]["title"] == "改名"
        assert (await c.delete(f"/api/chat/sessions/{sid}")).status_code == 200
        assert (await c.get("/api/chat/sessions")).json() == []


@pytest.mark.asyncio
async def test_post_message_creates_turn_and_409_when_active(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        r = (await c.post(f"/api/chat/sessions/{sid}/messages",
                          data={"content": "看下 BTC"})).json()
        assert r["turn_id"] and r["message_id"]
        # 上一轮还 queued，再发 409
        r2 = await c.post(f"/api/chat/sessions/{sid}/messages", data={"content": "再问"})
        assert r2.status_code == 409


@pytest.mark.asyncio
async def test_upload_validation_and_image_serving(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        r = await c.post(f"/api/chat/sessions/{sid}/messages",
                         data={"content": "看图"},
                         files={"files": ("k.png", PNG_1PX, "image/png")})
        assert r.status_code == 200
        img = (await c.get(f"/api/chat/sessions/{sid}/messages")).json()[
            "messages"][0]["images"][0]
        assert img["kind"] == "upload"
        got = await c.get(f"/api/chat/images/upload/{img['filename']}")
        assert got.status_code == 200 and got.content == PNG_1PX
        # 类型校验
        bad = await c.post(f"/api/chat/sessions/{sid}/messages",
                           data={"content": "x"},
                           files={"files": ("a.txt", b"hi", "text/plain")})
        assert bad.status_code == 415
        # 路径穿越
        assert (await c.get("/api/chat/images/upload/..%2Fwohub.db")).status_code in (400, 404)


@pytest.mark.asyncio
async def test_messages_returns_active_turn_and_events(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        r = (await c.post(f"/api/chat/sessions/{sid}/messages",
                          data={"content": "hi"})).json()
        events.append_event(r["turn_id"], "text_delta", {"text": "思考中"})
        out = (await c.get(f"/api/chat/sessions/{sid}/messages")).json()
        assert out["active_turn"]["id"] == r["turn_id"]
        assert out["active_events"][0]["payload"]["text"] == "思考中"
        assert out["last_event_id"] >= out["active_events"][-1]["id"]


@pytest.mark.asyncio
async def test_cancel_endpoint(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        tid = (await c.post(f"/api/chat/sessions/{sid}/messages",
                            data={"content": "hi"})).json()["turn_id"]
        assert (await c.post(f"/api/chat/turns/{tid}/cancel")).json()["ok"] is True
        assert store.cancel_requested(tid)


@pytest.mark.asyncio
async def test_sse_stream_replays_backlog(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        tid = (await c.post(f"/api/chat/sessions/{sid}/messages",
                            data={"content": "hi"})).json()["turn_id"]
        e1 = events.append_event(tid, "text_delta", {"text": "a"})
        lines = []
        async with c.stream("GET", f"/api/chat/sessions/{sid}/stream?after=0") as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                lines.append(line)
                if line.startswith("data:"):
                    break
        assert any(l == f"id: {e1}" for l in lines)
        assert any(l.startswith("event: text_delta") for l in lines)
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_api.py -v`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 实现**

`backend/config.py` 的 `Settings.__init__` 中追加一行：

```python
        self.chat_uploads_dir = os.environ.get("CHAT_UPLOADS_DIR", "data/chat_uploads")
```

```python
# backend/api/chat.py
import asyncio
import json
import os
import re
import time
import uuid
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import Optional

from config import settings
from agent.chat import store, events

router = APIRouter(prefix="/chat")

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}
_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class SessionBody(BaseModel):
    title: Optional[str] = None


@router.get("/sessions")
def list_sessions():
    return store.list_sessions()


@router.post("/sessions")
def create_session(body: SessionBody):
    return {"id": store.create_session(body.title)}


@router.patch("/sessions/{sid}")
def rename_session(sid: int, body: SessionBody):
    if not body.title or not body.title.strip():
        raise HTTPException(422, "标题不能为空")
    if not store.rename_session(sid, body.title.strip()):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@router.delete("/sessions/{sid}")
def delete_session(sid: int):
    store.delete_session(sid)
    return {"ok": True}


@router.get("/sessions/{sid}/messages")
def get_messages(sid: int):
    msgs = store.list_messages(sid)
    active = store.active_turn(sid)
    return {"messages": msgs,
            "active_turn": active,
            "active_events": events.turn_events(active["id"]) if active else [],
            "last_event_id": events.last_event_id()}


def _save_upload(f: UploadFile) -> dict:
    ext = ALLOWED_IMAGE_TYPES.get(f.content_type)
    if not ext:
        raise HTTPException(415, f"仅支持 PNG/JPEG 图片，收到 {f.content_type}")
    data = f.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "图片超过 5MB 上限")
    os.makedirs(settings.chat_uploads_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(settings.chat_uploads_dir, filename), "wb") as out:
        out.write(data)
    return {"kind": "upload", "filename": filename}


@router.post("/sessions/{sid}/messages")
def post_message(sid: int, content: str = Form(""),
                 files: list[UploadFile] = File(default=[])):
    if not any(s["id"] == sid for s in store.list_sessions()):
        raise HTTPException(404, "会话不存在")
    if store.active_turn(sid):
        raise HTTPException(409, "上一轮还在进行中（可先停止）")
    if not content.strip() and not files:
        raise HTTPException(422, "消息不能为空")
    images = [_save_upload(f) for f in files]
    mid = store.add_message(sid, "user", content.strip(), images=images or None)
    tid = store.create_turn(sid, mid)
    return {"turn_id": tid, "message_id": mid}


@router.post("/turns/{tid}/cancel")
def cancel_turn(tid: int):
    if not store.request_cancel(tid):
        raise HTTPException(404, "轮次不存在或已结束")
    return {"ok": True}


@router.get("/sessions/{sid}/stream")
async def stream(sid: int, after: int = 0):
    async def gen():
        last = after
        last_beat = time.monotonic()
        while True:
            rows = events.events_after(sid, last)
            for r in rows:
                last = r["id"]
                payload = json.dumps(r["payload"], ensure_ascii=False)
                yield f"id: {r['id']}\nevent: {r['type']}\ndata: {payload}\n\n"
            if rows:
                last_beat = time.monotonic()
                continue
            if time.monotonic() - last_beat > 15:
                yield ": ping\n\n"
                last_beat = time.monotonic()
            await asyncio.sleep(0.15)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/images/{kind}/{filename}")
def get_image(kind: str, filename: str):
    dirs = {"upload": settings.chat_uploads_dir,
            "screenshot": settings.screenshots_dir}
    if kind not in dirs or not _FILENAME_RE.fullmatch(filename):
        raise HTTPException(400, "非法路径")
    path = os.path.join(dirs[kind], filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "文件不存在")
    return FileResponse(path)
```

`backend/api/__init__.py`：加 `from api.chat import router as chat_router`，
在 `protected.include_router(agent_router)` 之后加 `protected.include_router(chat_router)`。

`frontend/src/api/client.js` 的 `export const api = {…}` 末尾（agent 段之后）追加：

```javascript
  // ---- chat ----

  async listChatSessions() {
    return request('/chat/sessions')
  },

  async createChatSession(title = null) {
    return request('/chat/sessions', { method: 'POST', body: JSON.stringify({ title }) })
  },

  async renameChatSession(id, title) {
    return request(`/chat/sessions/${id}`, { method: 'PATCH', body: JSON.stringify({ title }) })
  },

  async deleteChatSession(id) {
    return request(`/chat/sessions/${id}`, { method: 'DELETE' })
  },

  async getChatMessages(id) {
    return request(`/chat/sessions/${id}/messages`)
  },

  async sendChatMessage(id, content, files = []) {
    const form = new FormData()
    form.append('content', content)
    for (const f of files) form.append('files', f)
    const res = await fetch(`${BASE}/chat/sessions/${id}/messages`, {
      method: 'POST',
      body: form,
    })
    if (res.status === 401) { window.location.href = '/login'; throw new Error('Unauthorized') }
    if (!res.ok) {
      let detail = `${res.status}`
      try { detail = (await res.json()).detail || detail } catch {}
      throw new Error(detail)
    }
    return res.json()
  },

  async cancelChatTurn(turnId) {
    return request(`/chat/turns/${turnId}/cancel`, { method: 'POST' })
  },

  chatStreamUrl(sessionId, after = 0) {
    return `${BASE}/chat/sessions/${sessionId}/stream?after=${after}`
  },

  chatImageUrl(kind, filename) {
    return `${BASE}/chat/images/${kind}/${encodeURIComponent(filename)}`
  },
```

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_api.py -v && pytest -m "not network" -q`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api/chat.py backend/api/__init__.py backend/config.py backend/tests/test_chat_api.py frontend/src/api/client.js
git commit -m "feat(chat): sessions/messages/cancel/upload API + SSE stream with resume cursor"
```

---

## Phase 4 — 对话页前端

> 前端无测试框架（house 现状）。前端任务的验证 = `npm run build` 通过 + 手测清单逐项确认。样式一律复用全局 CSS 变量（对照 `frontend/src/views/Tasks.vue` 中的 `var(--…)` 命名；下方样式给了 fallback 值，接入时替换为项目变量名）。

### Task 11: 对话页 Chat.vue（文本全链路：会话/流式/工具卡片/停止/续播/重试）

**Files:**
- Modify: `frontend/package.json`（`npm install marked dompurify`）
- Modify: `frontend/src/router/index.js:5,20`（Agent → Chat）
- Modify: `frontend/src/App.vue:110`（导航 label `Agent 复盘` → `Agent 对话`）
- Create: `frontend/src/views/Chat.vue`
- Modify: `backend/api/chat.py`（补一个重试端点）
- Test: `backend/tests/test_chat_api.py`（补重试用例）

**Interfaces:**
- Consumes: Task 10 的全部 chat API 与 `api.chatStreamUrl/chatImageUrl`
- Produces: `/agent` 路由 = 对话页；`POST /api/chat/messages/{mid}/retry -> {turn_id}`（失败重试：同一条 user 消息重新入队新 turn）

- [ ] **Step 1: 后端补重试端点（TDD）**

测试追加到 `backend/tests/test_chat_api.py`：

```python
@pytest.mark.asyncio
async def test_retry_creates_new_turn_for_same_message(client):
    async with client as c:
        sid = (await c.post("/api/chat/sessions", json={})).json()["id"]
        r = (await c.post(f"/api/chat/sessions/{sid}/messages",
                          data={"content": "hi"})).json()
        # 模拟上一轮失败
        store.finish_turn(r["turn_id"], "failed")
        r2 = (await c.post(f"/api/chat/messages/{r['message_id']}/retry")).json()
        assert r2["turn_id"] != r["turn_id"]
        # 有活动轮次时 409
        r3 = await c.post(f"/api/chat/messages/{r['message_id']}/retry")
        assert r3.status_code == 409
```

`backend/api/chat.py` 追加：

```python
@router.post("/messages/{mid}/retry")
def retry_message(mid: int):
    db_msgs = None
    # 找到该 user 消息所属会话
    for s in store.list_sessions():
        for m in store.list_messages(s["id"]):
            if m["id"] == mid and m["role"] == "user":
                db_msgs = (s["id"], m)
                break
        if db_msgs:
            break
    if not db_msgs:
        raise HTTPException(404, "消息不存在或不是用户消息")
    sid, _ = db_msgs
    if store.active_turn(sid):
        raise HTTPException(409, "上一轮还在进行中")
    return {"turn_id": store.create_turn(sid, mid)}
```

Run: `cd backend && pytest tests/test_chat_api.py -v` → PASS 后提交前端部分一起。

- [ ] **Step 2: 安装前端依赖 + 路由/导航切换**

```bash
cd frontend && npm install marked dompurify
```

`frontend/src/router/index.js`：第 5 行 `import Agent from '../views/Agent.vue'` 改为 `import Chat from '../views/Chat.vue'`；第 20 行 `{ path: '/agent', component: Agent }` 改为 `{ path: '/agent', component: Chat }`。
`frontend/src/App.vue`：navItems 中 `/agent` 项的 `label: 'Agent 复盘'` 改为 `label: 'Agent 对话'`（icon 不动）。

- [ ] **Step 3: 实现 Chat.vue（完整文件）**

```vue
<!-- frontend/src/views/Chat.vue -->
<template>
  <div class="chat-page">
    <aside class="chat-side">
      <button class="btn primary new-btn" @click="newSession">＋ 新会话</button>
      <div class="sess-list">
        <div v-for="s in sessions" :key="s.id" class="sess-item"
             :class="{ active: s.id === activeId }" @click="selectSession(s.id)">
          <span class="sess-title">{{ s.title }}</span>
          <span class="sess-ops">
            <button title="重命名" @click.stop="renameSession(s)">✎</button>
            <button title="删除" @click.stop="removeSession(s)">✕</button>
          </span>
        </div>
      </div>
    </aside>

    <section class="chat-main">
      <div v-if="!activeId" class="chat-empty">选择左侧会话，或新建一个开始对话</div>
      <template v-else>
        <div ref="scrollEl" class="chat-scroll">
          <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
            <div class="bubble">
              <div v-if="m.images && m.images.length" class="msg-images">
                <a v-for="img in m.images" :key="img.filename"
                   :href="api.chatImageUrl(img.kind, img.filename)" target="_blank">
                  <img :src="api.chatImageUrl(img.kind, img.filename)" />
                </a>
              </div>
              <div v-if="m.role === 'assistant'" class="md" v-html="renderMd(m.content)"></div>
              <div v-else class="plain">{{ m.content }}</div>
              <details v-if="traceSteps(m).length" class="trace">
                <summary>工具轨迹（{{ traceSteps(m).length }} 次调用）</summary>
                <div v-for="(st, i) in traceSteps(m)" :key="i" class="trace-step">
                  <code>{{ st.tool }}</code> {{ shortJson(st.args) }}
                  <pre>{{ st.result }}</pre>
                </div>
              </details>
              <div v-if="m.error" class="msg-error">
                {{ m.error === 'cancelled' ? '已停止' : '出错：' + m.error }}
                <button v-if="retryTargetOf(m)" class="btn tiny" @click="retry(m)">重试</button>
              </div>
            </div>
          </div>

          <div v-if="live.active" class="msg assistant">
            <div class="bubble">
              <div v-for="(c, i) in live.cards" :key="i" class="tool-card" :class="c.status">
                <div class="tool-head">
                  <span class="tool-dot" :class="c.status"></span>
                  <code>{{ c.tool }}</code>
                  <span class="tool-note">{{ c.note }}</span>
                  <span v-if="c.elapsed" class="tool-ms">{{ c.elapsed }}ms</span>
                </div>
                <details v-if="c.summary"><summary>结果摘要</summary><pre>{{ c.summary }}</pre></details>
              </div>
              <div v-if="live.images.length" class="msg-images">
                <a v-for="img in live.images" :key="img.filename"
                   :href="api.chatImageUrl(img.kind, img.filename)" target="_blank">
                  <img :src="api.chatImageUrl(img.kind, img.filename)" />
                </a>
              </div>
              <div class="md" v-html="renderMd(live.text)"></div>
              <span class="cursor">▍</span>
            </div>
          </div>
        </div>

        <div class="chat-input">
          <textarea v-model="draft" rows="2" :disabled="live.active"
                    placeholder="问点什么…（Enter 发送，Shift+Enter 换行）"
                    @keydown.enter.exact.prevent="send"></textarea>
          <button v-if="live.active" class="btn danger" @click="stop">■ 停止</button>
          <button v-else class="btn primary" :disabled="!draft.trim()" @click="send">发送</button>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api } from '../api/client.js'

const sessions = ref([])
const activeId = ref(null)
const messages = ref([])
const draft = ref('')
const scrollEl = ref(null)
const live = reactive({ active: false, turnId: null, text: '', cards: [], images: [] })

let es = null
let lastEventId = 0
let reconnectDelay = 1000

function renderMd(text) {
  return DOMPurify.sanitize(marked.parse(text || ''))
}
function shortJson(o) {
  try { const s = JSON.stringify(o); return s.length > 120 ? s.slice(0, 120) + '…' : s }
  catch { return '' }
}
function traceSteps(m) {
  return (m.trace && m.trace.steps ? m.trace.steps : []).filter(s => s.tool)
}
function retryTargetOf(m) {
  // 失败/停止的 assistant 消息 → 找它前面最近的 user 消息
  if (m.role !== 'assistant' || !m.error || live.active) return null
  const idx = messages.value.findIndex(x => x.id === m.id)
  for (let i = idx - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') return messages.value[i]
  }
  return null
}

async function scrollBottom() {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
}

function resetLive() {
  live.active = false
  live.turnId = null
  live.text = ''
  live.cards = []
  live.images = []
}

// ---- SSE ----
function closeStream() {
  if (es) { es.close(); es = null }
}

function openStream() {
  closeStream()
  if (!activeId.value) return
  es = new EventSource(api.chatStreamUrl(activeId.value, lastEventId))
  const on = (type, fn) => es.addEventListener(type, e => {
    lastEventId = Number(e.lastEventId || lastEventId)
    reconnectDelay = 1000
    fn(JSON.parse(e.data))
    scrollBottom()
  })
  on('text_delta', p => { live.active = true; live.text += p.text })
  on('tool_start', p => {
    live.active = true
    live.cards.push({ tool: p.tool, status: 'running', note: shortJson(p.args), summary: '', elapsed: 0 })
  })
  on('tool_progress', p => {
    const c = [...live.cards].reverse().find(c => c.tool === p.tool && c.status === 'running')
    if (c) c.note = `${p.done}/${p.total} · ${p.note}`
  })
  on('tool_end', p => {
    const c = [...live.cards].reverse().find(c => c.tool === p.tool && c.status === 'running')
    if (c) { c.status = p.ok ? 'done' : 'error'; c.summary = p.summary; c.elapsed = p.elapsed_ms }
  })
  on('image', p => live.images.push({ kind: p.kind, filename: p.filename }))
  on('turn_done', async () => { await finalizeTurn() })
  on('turn_error', async () => { await finalizeTurn() })
  on('cancelled', async () => { await finalizeTurn() })
  es.onerror = () => {
    closeStream()
    setTimeout(openStream, reconnectDelay)
    reconnectDelay = Math.min(reconnectDelay * 2, 10000)
  }
}

async function finalizeTurn() {
  const data = await api.getChatMessages(activeId.value)
  messages.value = data.messages
  resetLive()
  await loadSessions()          // 标题可能被自动更新
  scrollBottom()
}

// ---- 会话 ----
async function loadSessions() {
  sessions.value = await api.listChatSessions()
}

async function selectSession(id) {
  activeId.value = id
  resetLive()
  const data = await api.getChatMessages(id)
  messages.value = data.messages
  lastEventId = data.last_event_id
  if (data.active_turn) {
    // 恢复进行中轮次：先回放已有事件，再从 last_event_id 跟播
    live.active = true
    live.turnId = data.active_turn.id
    for (const ev of data.active_events) replayEvent(ev)
  }
  openStream()
  scrollBottom()
}

function replayEvent(ev) {
  const p = ev.payload
  if (ev.type === 'text_delta') live.text += p.text
  else if (ev.type === 'tool_start') live.cards.push({ tool: p.tool, status: 'running', note: shortJson(p.args), summary: '', elapsed: 0 })
  else if (ev.type === 'tool_progress') {
    const c = [...live.cards].reverse().find(c => c.tool === p.tool && c.status === 'running')
    if (c) c.note = `${p.done}/${p.total} · ${p.note}`
  } else if (ev.type === 'tool_end') {
    const c = [...live.cards].reverse().find(c => c.tool === p.tool && c.status === 'running')
    if (c) { c.status = p.ok ? 'done' : 'error'; c.summary = p.summary; c.elapsed = p.elapsed_ms }
  } else if (ev.type === 'image') live.images.push({ kind: p.kind, filename: p.filename })
}

async function newSession() {
  const { id } = await api.createChatSession()
  await loadSessions()
  await selectSession(id)
}

async function renameSession(s) {
  const t = prompt('会话标题', s.title)
  if (t && t.trim()) { await api.renameChatSession(s.id, t.trim()); await loadSessions() }
}

async function removeSession(s) {
  if (!confirm(`删除会话「${s.title}」及全部消息？`)) return
  await api.deleteChatSession(s.id)
  if (activeId.value === s.id) { activeId.value = null; messages.value = []; closeStream() }
  await loadSessions()
}

// ---- 发送/停止/重试 ----
async function send() {
  const text = draft.value.trim()
  if (!text || live.active) return
  draft.value = ''
  try {
    const r = await api.sendChatMessage(activeId.value, text)
    messages.value.push({ id: r.message_id, role: 'user', content: text, images: [] })
    live.active = true
    live.turnId = r.turn_id
    scrollBottom()
  } catch (e) {
    alert('发送失败：' + e.message)
    draft.value = text
  }
}

async function stop() {
  if (live.turnId) { try { await api.cancelChatTurn(live.turnId) } catch {} }
}

async function retry(m) {
  const target = retryTargetOf(m)
  if (!target) return
  try {
    const r = await api.retryChatMessage(target.id)
    live.active = true
    live.turnId = r.turn_id
  } catch (e) { alert('重试失败：' + e.message) }
}

onMounted(async () => {
  await loadSessions()
  if (sessions.value.length) await selectSession(sessions.value[0].id)
})
onBeforeUnmount(closeStream)
</script>

<style scoped>
.chat-page { display: flex; height: calc(100vh - 48px); gap: 12px; }
.chat-side { width: 220px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; }
.new-btn { width: 100%; }
.sess-list { overflow-y: auto; flex: 1; }
.sess-item { display: flex; justify-content: space-between; align-items: center;
  padding: 8px 10px; border-radius: 8px; cursor: pointer; font-size: 13px; }
.sess-item:hover, .sess-item.active { background: var(--hover-bg, rgba(128,128,128,.15)); }
.sess-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sess-ops { visibility: hidden; }
.sess-item:hover .sess-ops { visibility: visible; }
.sess-ops button { background: none; border: none; cursor: pointer; opacity: .6; }
.chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.chat-empty { margin: auto; opacity: .5; }
.chat-scroll { flex: 1; overflow-y: auto; padding: 8px 16px; }
.msg { display: flex; margin-bottom: 12px; }
.msg.user { justify-content: flex-end; }
.bubble { max-width: 78%; padding: 10px 14px; border-radius: 12px;
  background: var(--card-bg, rgba(128,128,128,.08)); overflow-wrap: break-word; }
.msg.user .bubble { background: var(--accent-soft, rgba(200,110,60,.18)); white-space: pre-wrap; }
.md :deep(pre) { overflow-x: auto; padding: 8px; border-radius: 6px;
  background: rgba(0,0,0,.25); }
.md :deep(table) { border-collapse: collapse; }
.md :deep(td), .md :deep(th) { border: 1px solid rgba(128,128,128,.3); padding: 2px 8px; }
.msg-images img { max-width: 260px; max-height: 180px; border-radius: 8px; margin: 4px 6px 4px 0; }
.tool-card { border: 1px solid rgba(128,128,128,.25); border-radius: 8px;
  padding: 6px 10px; margin-bottom: 6px; font-size: 12.5px; }
.tool-card.error { border-color: rgba(220,80,80,.6); }
.tool-head { display: flex; align-items: center; gap: 8px; }
.tool-dot { width: 8px; height: 8px; border-radius: 50%; background: #e6a23c; flex-shrink: 0; }
.tool-dot.running { animation: pulse 1s infinite; }
.tool-dot.done { background: #67c23a; }
.tool-dot.error { background: #f56c6c; }
@keyframes pulse { 50% { opacity: .3; } }
.tool-note { opacity: .75; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-ms { margin-left: auto; opacity: .5; }
.tool-card pre, .trace pre { max-height: 200px; overflow: auto; font-size: 11.5px;
  white-space: pre-wrap; word-break: break-all; }
.trace { margin-top: 8px; font-size: 12px; opacity: .85; }
.trace-step { margin: 6px 0; }
.msg-error { color: #f56c6c; font-size: 12.5px; margin-top: 6px; }
.cursor { animation: pulse .8s infinite; }
.chat-input { display: flex; gap: 8px; padding: 10px 16px; align-items: flex-end; }
.chat-input textarea { flex: 1; resize: none; border-radius: 10px; padding: 10px;
  background: var(--input-bg, rgba(128,128,128,.1)); border: 1px solid rgba(128,128,128,.25);
  color: inherit; font: inherit; }
.btn { padding: 8px 18px; border-radius: 8px; border: none; cursor: pointer; }
.btn.primary { background: var(--accent, #c06a3a); color: #fff; }
.btn.danger { background: #f56c6c; color: #fff; }
.btn.tiny { padding: 2px 8px; font-size: 12px; margin-left: 8px; }
.btn:disabled { opacity: .4; cursor: default; }
</style>
```

同时在 `frontend/src/api/client.js` 的 chat 段补一个方法（Task 10 已建其余）：

```javascript
  async retryChatMessage(messageId) {
    return request(`/chat/messages/${messageId}/retry`, { method: 'POST' })
  },
```

- [ ] **Step 4: 构建验证 + 手测清单**

Run: `cd frontend && npm run build`
Expected: 构建成功无报错。

手测（`cd backend && python main.py` + `cd frontend && npm run dev`，浏览器 :5173，需已配置有效 LLM key）:
1. 新建会话 → 发「BTC 现在什么价」→ 工具卡片弹出（running 黄点→done 绿点）→ 文字逐字出现 → 完成后消息定格、会话标题变为问题前 30 字
2. 长任务期间刷新页面 → 进行中的卡片与已出文字恢复、继续跟播
3. 停止按钮 → 出现「已停止」，可继续发新消息
4. 断网 3 秒恢复 → EventSource 自动重连不丢事件
5. 会话重命名/删除正常；失败轮次显示「重试」按钮且可用

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/router/index.js frontend/src/App.vue frontend/src/views/Chat.vue frontend/src/api/client.js backend/api/chat.py backend/tests/test_chat_api.py
git commit -m "feat(chat-ui): conversation page — sessions, SSE streaming, tool cards, stop/resume/retry"
```

### Task 12: 图片输入与展示（粘贴/选择上传）

**Files:**
- Modify: `frontend/src/views/Chat.vue`

**Interfaces:**
- Consumes: Task 10 `sendChatMessage(id, content, files)`（multipart 已支持）
- Produces: 输入区支持粘贴/选择 PNG/JPEG（≤5MB，前端预检），发送后缩略图入消息流

- [ ] **Step 1: 实现**

`Chat.vue` 变更三处：

① template 输入区（替换原 `<div class="chat-input">…</div>` 整块）：

```vue
        <div class="chat-input-wrap">
          <div v-if="pendingFiles.length" class="pend-imgs">
            <span v-for="(f, i) in pendingFiles" :key="i" class="pend-chip">
              <img :src="f.preview" />
              <button @click="pendingFiles.splice(i, 1)">✕</button>
            </span>
          </div>
          <div class="chat-input">
            <button class="btn ghost" title="添加图片" @click="fileInput.click()">🖼</button>
            <input ref="fileInput" type="file" accept="image/png,image/jpeg" multiple
                   style="display:none" @change="pickFiles" />
            <textarea v-model="draft" rows="2" :disabled="live.active"
                      placeholder="问点什么…（Enter 发送，Shift+Enter 换行，可粘贴图片）"
                      @keydown.enter.exact.prevent="send" @paste="onPaste"></textarea>
            <button v-if="live.active" class="btn danger" @click="stop">■ 停止</button>
            <button v-else class="btn primary"
                    :disabled="!draft.trim() && !pendingFiles.length" @click="send">发送</button>
          </div>
        </div>
```

② script 追加状态与方法：

```javascript
const pendingFiles = ref([])
const fileInput = ref(null)

function addFile(file) {
  if (!['image/png', 'image/jpeg'].includes(file.type)) { alert('仅支持 PNG/JPEG'); return }
  if (file.size > 5 * 1024 * 1024) { alert('图片超过 5MB'); return }
  pendingFiles.value.push({ file, preview: URL.createObjectURL(file) })
}
function pickFiles(e) {
  for (const f of e.target.files) addFile(f)
  e.target.value = ''
}
function onPaste(e) {
  for (const item of e.clipboardData.items) {
    if (item.type.startsWith('image/')) { const f = item.getAsFile(); if (f) addFile(f) }
  }
}
```

③ `send()` 改为携带文件（整函数替换）：

```javascript
async function send() {
  const text = draft.value.trim()
  const files = pendingFiles.value.map(p => p.file)
  if ((!text && !files.length) || live.active) return
  draft.value = ''
  pendingFiles.value = []
  try {
    const r = await api.sendChatMessage(activeId.value, text, files)
    const data = await api.getChatMessages(activeId.value)   // 拿服务端落库的 images 引用
    messages.value = data.messages
    live.active = true
    live.turnId = r.turn_id
    scrollBottom()
  } catch (e) {
    alert('发送失败：' + e.message)
    draft.value = text
  }
}
```

样式追加：

```css
.pend-imgs { display: flex; gap: 8px; padding: 0 16px; }
.pend-chip { position: relative; }
.pend-chip img { width: 56px; height: 56px; object-fit: cover; border-radius: 8px; }
.pend-chip button { position: absolute; top: -6px; right: -6px; border-radius: 50%;
  border: none; width: 18px; height: 18px; font-size: 10px; cursor: pointer; }
.btn.ghost { background: none; border: 1px solid rgba(128,128,128,.3); }
```

- [ ] **Step 2: 构建验证 + 手测**

Run: `cd frontend && npm run build` → 成功。
手测：粘贴截图出现缩略 chip → 发送后消息流显示图片（点击新标签页看原图）→ 视觉未配置时 agent 回复会说明看不到图（Task 13 后变为真识图）。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Chat.vue
git commit -m "feat(chat-ui): image paste/upload with preview chips and inline rendering"
```

---

## Phase 5 — 视觉中继 + 截图工具

### Task 13: 视觉中继 `vision.py` + 用户图片管道 + 配置字段

**Files:**
- Create: `backend/agent/chat/vision.py`
- Modify: `backend/agent/config.py`（FIELDS/AgentConfig 加 `vision_model`）
- Modify: `backend/agent/chat/runtime.py`（`_build_prompt` 接入图片；签名改为 `_build_prompt(session_id, user_message_id, cfg, deps)`）
- Test: `backend/tests/test_chat_vision.py`

**Interfaces:**
- Consumes: Task 1 的 `vision_model` 列、`agent.llm.build_model(cfg, model_name)`
- Produces:
  - `vision.describe_image(cfg, image_bytes: bytes, media_type: str, extra: str = "") -> str`
  - `vision.load_image(kind: str, filename: str) -> tuple[bytes, str]`（返回 bytes+media_type；kind ∈ upload|screenshot）
  - 图片规则（规格 §4）：配置了 `vision_model` → 一律中继成文字描述；未配置 → `BinaryContent` 直传主模型（不支持图像的主模型会报错，由失败路径呈现）

- [ ] **Step 1: 修改 `backend/agent/config.py`**

`FIELDS` 元组加入 `"vision_model"`（`"model"` 之后）；`AgentConfig` dataclass 加字段 `vision_model: str`（`model: str` 之后）；`load_config` 返回构造中加 `vision_model=row["vision_model"]`。

- [ ] **Step 2: 写失败测试**

```python
# backend/tests/test_chat_vision.py
import os
import pytest
from unittest.mock import patch
from pydantic_ai.models.test import TestModel
from agent.config import save_config, load_config
from agent.chat import store, vision, runtime

PNG_1PX = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
           b"\xff?\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82")


def test_config_roundtrips_vision_model():
    save_config({"provider": "openai", "model": "m", "api_key": "k",
                 "vision_model": "gemini-vision", "enabled": True})
    assert load_config().vision_model == "gemini-vision"


def test_describe_image_uses_vision_model_slot():
    save_config({"provider": "openai", "model": "m", "api_key": "k",
                 "vision_model": "v", "enabled": True})
    cfg = load_config()
    with patch("agent.chat.vision.build_model",
               return_value=TestModel(call_tools=[], custom_output_text="上升趋势，MACD 金叉")) as bm:
        out = vision.describe_image(cfg, PNG_1PX, "image/png")
    assert "上升趋势" in out
    assert bm.call_args.kwargs.get("model_name") == "v" or bm.call_args.args[1:] == ("v",)


def test_load_image_reads_upload_dir(tmp_path):
    from config import settings
    with patch.object(settings, "chat_uploads_dir", str(tmp_path)):
        (tmp_path / "a.png").write_bytes(PNG_1PX)
        data, mt = vision.load_image("upload", "a.png")
    assert data == PNG_1PX and mt == "image/png"
    with pytest.raises(FileNotFoundError):
        vision.load_image("upload", "nope.png")


def _turn_with_image(tmp_path):
    from config import settings
    settings.chat_uploads_dir = str(tmp_path)          # 测试内直接指向 tmp
    (tmp_path / "b.png").write_bytes(PNG_1PX)
    sid = store.create_session()
    mid = store.add_message(sid, "user", "看这张图",
                            images=[{"kind": "upload", "filename": "b.png"}])
    store.create_turn(sid, mid)
    return sid, store.claim_next_turn()


def test_runtime_relays_image_when_vision_configured(tmp_path):
    save_config({"provider": "openai", "model": "m", "api_key": "k",
                 "vision_model": "v", "enabled": True})
    sid, row = _turn_with_image(tmp_path)
    with patch("agent.chat.runtime.describe_image",
               return_value="图为BTC 4h，回踩EMA55") as di:
        runtime.run_turn(row, model_override=TestModel(call_tools=[], custom_output_text="分析如下"))
    assert di.called
    # 中继描述进入了 trace（视觉调用作为一步记录）
    steps = store.list_messages(sid)[-1]["trace"]["steps"]
    assert any(s.get("tool") == "vision_relay" for s in steps)


def test_runtime_passes_binary_when_no_vision_model(tmp_path):
    save_config({"provider": "openai", "model": "m", "api_key": "k",
                 "vision_model": "", "enabled": True})
    sid, row = _turn_with_image(tmp_path)
    runtime.run_turn(row, model_override=TestModel(call_tools=[], custom_output_text="ok"))
    # 直传路径：不调用中继，轮次正常完成（TestModel 接受多模态输入）
    assert store.list_messages(sid)[-1]["error"] is None
```

- [ ] **Step 3: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_vision.py -v`
Expected: FAIL

- [ ] **Step 4: 实现**

```python
# backend/agent/chat/vision.py
"""视觉中继：图片 → 视觉模型 → 结构化盘面描述文本。
规则确定（规格 §4）：配置了 vision_model 就一律走中继；没配置就直传主模型。"""
import os
from pydantic_ai import Agent
from config import settings
from agent.llm import build_model

VISION_SYSTEM = """你是K线图表读图员。客观描述图中可见的事实：
品种与周期（若可见）、趋势结构（高低点序列）、关键支撑/压力位、
显著K线形态、可见指标状态（如 MACD/RSI/均线）、成交量特征、异常之处。
只描述可见事实与数值，不给交易建议，不猜测图外信息。用中文，条目式。"""

_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def load_image(kind: str, filename: str) -> tuple[bytes, str]:
    dirs = {"upload": settings.chat_uploads_dir, "screenshot": settings.screenshots_dir}
    if kind not in dirs:
        raise ValueError(f"unknown image kind: {kind}")
    path = os.path.join(dirs[kind], filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(filename)[1].lower()
    with open(path, "rb") as f:
        return f.read(), _MEDIA.get(ext, "image/png")


def describe_image(cfg, image_bytes: bytes, media_type: str, extra: str = "") -> str:
    from pydantic_ai import BinaryContent
    model = build_model(cfg, model_name=cfg.vision_model)
    agent = Agent(model, output_type=str, system_prompt=VISION_SYSTEM)
    prompt = [extra or "请读取并描述这张K线图。",
              BinaryContent(data=image_bytes, media_type=media_type)]
    return agent.run_sync(prompt).output
```

`backend/agent/chat/runtime.py` 修改：

① 顶部 import 增加：`from agent.chat.vision import describe_image, load_image` 与 `from pydantic_ai import BinaryContent`。

② `_build_prompt` 整函数替换为：

```python
def _build_prompt(session_id: int, user_message_id: int, cfg, deps: ChatDeps):
    msgs = store.list_messages(session_id)
    current = next(m for m in msgs if m["id"] == user_message_id)
    history = [m for m in msgs if m["id"] < user_message_id][-HISTORY_LIMIT:]
    text = ""
    if history:
        text += "【对话历史（最近 %d 条）】\n%s\n\n【当前消息】\n" % (
            len(history), render_history(history))
    text += current.get("content") or ""
    images = current.get("images") or []
    if not images:
        return text, current
    if cfg.vision_model:
        # 视觉中继：每张图先由视觉模型转述，作为一步 trace 记录
        for i, img in enumerate(images, 1):
            deps.check_cancel()
            deps.emit("tool_start", {"tool": "vision_relay",
                                     "args": {"filename": img["filename"]}})
            try:
                data, mt = load_image(img["kind"], img["filename"])
                desc = describe_image(cfg, data, mt)
                ok = True
            except Exception as e:
                desc = f"图片读取/分析失败: {e}"
                ok = False
            deps.trace.append({"tool": "vision_relay", "args": img,
                               "result": desc[:RESULT_TRUNC]})
            deps.emit("tool_end", {"tool": "vision_relay", "ok": ok,
                                   "summary": desc[:400], "elapsed_ms": 0})
            text += f"\n\n【图片{i}的视觉分析（由视觉模型转述）】\n{desc}"
        return text, current
    # 未配置视觉模型：BinaryContent 直传主模型（不支持图像的主模型将由失败路径呈现）
    parts: list = [text]
    for img in images:
        data, mt = load_image(img["kind"], img["filename"])
        parts.append(BinaryContent(data=data, media_type=mt))
    return parts, current
```

③ `run_turn` 中调用处改为 `prompt, current = _build_prompt(session_id, turn_row["user_message_id"], cfg, deps)`，并删除旧的「未启用视觉功能」提示逻辑（已被上面取代）。

- [ ] **Step 5: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_vision.py tests/test_chat_runtime.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/agent/chat/vision.py backend/agent/config.py backend/agent/chat/runtime.py backend/tests/test_chat_vision.py
git commit -m "feat(chat): vision relay — user images via vision-model slot or direct multimodal passthrough"
```

### Task 14: `capture_chart` 截图工具

**Files:**
- Modify: `backend/agent/tools.py`（`capture_chart`）
- Modify: `backend/agent/chat/runtime.py`（`_build_agent` 注册，`cfg.vision_model` 门控）
- Test: `backend/tests/test_chat_capture.py`

**Interfaces:**
- Consumes: `screenshots.client.chartshot_client.screenshot(symbol, timeframes)`、`vision.describe_image/load_image`
- Produces: `tools.capture_chart(symbol: str, interval: str) -> dict`（`{"files": […]} | {"error"}`）；agent 工具 `capture_chart`（发 `image` 事件 + 返回视觉分析文本；占 deep_dive 预算；仅 `cfg.vision_model` 非空时注册）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_chat_capture.py
from unittest.mock import patch
from pydantic_ai.models.function import FunctionModel, AgentInfo, DeltaToolCall
from agent import tools as T
from agent.chat import store, events, runtime
from agent.config import save_config


def test_capture_chart_wraps_chartshot():
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": True, "files": ["BTCUSDT_1h_x.png"]}):
        out = T.capture_chart("BTCUSDT", "1h")
    assert out == {"files": ["BTCUSDT_1h_x.png"]}
    with patch("screenshots.client.chartshot_client.screenshot",
               return_value={"ok": False, "error": "cookie 过期"}):
        assert "error" in T.capture_chart("BTCUSDT", "1h")


def _prep(vision="v"):
    save_config({"provider": "openai", "model": "m", "api_key": "k",
                 "vision_model": vision, "enabled": True})
    sid = store.create_session()
    tid = store.create_turn(sid, store.add_message(sid, "user", "截图看下 BTC 1h"))
    return sid, store.claim_next_turn()


def test_capture_tool_emits_image_event_and_relays():
    sid, row = _prep()

    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="capture_chart",
                                    json_args='{"symbol": "BTCUSDT", "interval": "1h"}')}
        else:
            yield "图已分析"

    with patch("agent.tools.capture_chart",
               return_value={"files": ["BTCUSDT_1h_x.png"]}), \
         patch("agent.chat.runtime.load_image", return_value=(b"png", "image/png")), \
         patch("agent.chat.runtime.describe_image", return_value="4h 上升通道"):
        runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))
    types = [e["type"] for e in events.turn_events(row["id"])]
    assert "image" in types and types[-1] == "turn_done"
    imgs = [e for e in events.turn_events(row["id"]) if e["type"] == "image"]
    assert imgs[0]["payload"] == {"kind": "screenshot", "filename": "BTCUSDT_1h_x.png",
                                  "caption": "BTCUSDT 1h"}


def test_capture_tool_absent_without_vision_model():
    sid, row = _prep(vision="")

    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="capture_chart",
                                    json_args='{"symbol": "BTCUSDT", "interval": "1h"}')}
        else:
            yield "done"

    runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))
    # 工具未注册 → 模型调用未知工具 → 该轮以失败结束而非崩溃
    assert [e["type"] for e in events.turn_events(row["id"])][-1] in ("turn_error", "turn_done")
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_chat_capture.py -v`
Expected: FAIL with `AttributeError: capture_chart`

- [ ] **Step 3: 实现**

`backend/agent/tools.py` 追加：

```python
def capture_chart(symbol: str, interval: str) -> dict:
    """ChartShot 截取 TradingView 实时图表（含用户配置的指标模板）。
    返回文件名列表；文件已落在 settings.screenshots_dir。"""
    from screenshots.client import chartshot_client
    res = chartshot_client.screenshot(symbol, [interval])
    if not res.get("ok"):
        return {"error": f"截图失败: {res.get('error', 'unknown')}（可能 ChartShot 未运行或 cookie 过期）"}
    files = res.get("files") or []
    if not files:
        return {"error": "截图服务未返回文件"}
    return {"files": files}
```

`backend/agent/chat/runtime.py` 的 `_build_agent` 中、`if cfg.credential_id:` 块**之前**加：

```python
    if cfg.vision_model:
        @agent.tool
        def capture_chart(ctx: RunContext[ChatDeps], symbol: str, interval: str) -> dict:
            """截取 TradingView 实时图表并做视觉分析（长任务，占深评配额）。
            interval 用 1h/4h/1d 等格式。"""
            d = ctx.deps

            def run():
                if d.budget.used >= d.budget.deep_dive_limit:
                    return {"error": f"深评配额已用完（每轮 {d.budget.deep_dive_limit} 次），"
                                     "请用已有证据作答"}
                d.budget.used += 1
                out = T.capture_chart(symbol, interval)
                if out.get("error"):
                    return out
                analysis = []
                for fn in out["files"]:
                    d.emit("image", {"kind": "screenshot", "filename": fn,
                                     "caption": f"{symbol} {interval}"})
                    try:
                        data, mt = load_image("screenshot", fn)
                        analysis.append(describe_image(cfg, data, mt))
                    except Exception as e:
                        analysis.append(f"视觉分析失败: {e}")
                return {"files": out["files"], "analysis": analysis}

            return _tool(ctx, "capture_chart",
                         {"symbol": symbol, "interval": interval}, run)
```

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_chat_capture.py tests/test_chat_runtime.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/agent/tools.py backend/agent/chat/runtime.py backend/tests/test_chat_capture.py
git commit -m "feat(chat): capture_chart tool — ChartShot screenshot + inline image event + vision analysis"
```

---

## Phase 6 — 配置增强 + 语义编辑

### Task 15: 模型列表 / 连通性测试 / 语义 API

> 规格 §4 写的是 `GET /api/agent/models`；实现改为 **POST**（请求体可携带未保存的表单值——key 不该进 URL/查询串）。语义不变，属规格细化，规格文档无需回改。

**Files:**
- Modify: `backend/api/agent.py`（AgentConfigBody 调整 + 4 个新端点）
- Test: `backend/tests/test_agent_probe_api.py`

**Interfaces:**
- Consumes: `agent.llm.build_model`、`agent.chat.semantics`、`requests`（已有依赖）
- Produces（Task 16 前端消费）:
  - `POST /api/agent/models` `{provider?, base_url?, api_key?}`（缺省回落已存配置）→ `{models: [str]}`
  - `POST /api/agent/test` `{provider?, base_url?, api_key?, model?, vision_model?}` → `{main: {ok, error?}, vision: {ok, error?, supports_image?} | null}`
  - `GET /api/agent/semantics` → `[{key, label, meaning, bias, usage, caveats, combos}]`（空表自动 seed）
  - `PUT /api/agent/semantics/{folder}/{name}` body 为五字段子集 → `{ok}`
  - `AgentConfigBody`：新增 `vision_model: str = ""`；删除 `cooldown_minutes`（旧客户端多传会被 Pydantic 忽略）

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_probe_api.py
import pytest
from unittest.mock import patch
from agent.config import save_config


@pytest.mark.asyncio
async def test_models_proxy_openai_compatible(client):
    save_config({"provider": "openai", "base_url": "https://openrouter.ai/api/v1",
                 "model": "m", "api_key": "sk-x", "enabled": False})

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "deepseek/deepseek-v4-pro"},
                                          {"id": "google/gemini-3-pro"}]}

    with patch("api.agent.requests.get", return_value=FakeResp()) as g:
        async with client as c:
            r = (await c.post("/api/agent/models", json={})).json()
    assert r["models"] == ["deepseek/deepseek-v4-pro", "google/gemini-3-pro"]
    assert g.call_args.args[0] == "https://openrouter.ai/api/v1/models"


@pytest.mark.asyncio
async def test_models_requires_key(client):
    async with client as c:
        r = await c.post("/api/agent/models", json={})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_llm_test_endpoint_merges_overrides(client):
    save_config({"provider": "openai", "model": "saved-m", "api_key": "k",
                 "vision_model": "", "enabled": False})
    seen = {}

    def fake_probe_text(cfg):
        seen["model"] = cfg.model
        return {"ok": True}

    with patch("api.agent._probe_text", side_effect=fake_probe_text), \
         patch("api.agent._probe_vision", return_value={"ok": True, "supports_image": True}) as pv:
        async with client as c:
            r = (await c.post("/api/agent/test",
                              json={"model": "override-m", "vision_model": "vm"})).json()
    assert r["main"]["ok"] is True and seen["model"] == "override-m"
    assert r["vision"]["supports_image"] is True
    # 不传 vision_model 且配置为空 → vision 为 null
    with patch("api.agent._probe_text", return_value={"ok": True}):
        async with client as c:
            r2 = (await c.post("/api/agent/test", json={})).json()
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

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && pytest tests/test_agent_probe_api.py -v`
Expected: FAIL（405/404）

- [ ] **Step 3: 实现（`backend/api/agent.py`）**

顶部 import 增加：`import base64`、`import dataclasses`、`import requests`、
`from agent.llm import build_model`。

`AgentConfigBody` 调整：删除 `cooldown_minutes` 字段；在 `model: str` 之后加
`vision_model: str = ""`。

文件末尾追加：

```python
# ---- 连通性探测与模型列表（Phase 6）----

# 1x1 红色 PNG，用于视觉模型图像能力探测
_PROBE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/"
    "q842iQAAAABJRU5ErkJggg==")


class ProbeBody(BaseModel):
    provider: Optional[Literal["openai", "anthropic"]] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None       # None = 用已存密钥
    model: Optional[str] = None
    vision_model: Optional[str] = None


def _merged_cfg(body: ProbeBody):
    cfg = load_config()
    return dataclasses.replace(
        cfg,
        provider=body.provider or cfg.provider,
        base_url=cfg.base_url if body.base_url is None else body.base_url,
        api_key=body.api_key or cfg.api_key,
        model=body.model or cfg.model,
        vision_model=cfg.vision_model if body.vision_model is None else body.vision_model)


@router.post("/models")
def list_models(body: ProbeBody):
    cfg = _merged_cfg(body)
    if not cfg.api_key:
        raise HTTPException(400, "未配置 API Key")
    try:
        if cfg.provider == "anthropic":
            r = requests.get("https://api.anthropic.com/v1/models",
                             headers={"x-api-key": cfg.api_key,
                                      "anthropic-version": "2023-06-01"}, timeout=15)
        else:
            base = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
            r = requests.get(f"{base}/models",
                             headers={"Authorization": f"Bearer {cfg.api_key}"}, timeout=15)
        r.raise_for_status()
        ids = sorted(m["id"] for m in r.json().get("data", []) if m.get("id"))
        return {"models": ids}
    except requests.RequestException as e:
        raise HTTPException(502, f"模型列表获取失败: {e}")


def _probe_text(cfg) -> dict:
    """最小文本调用验证 key/model 可用。真网调用，仅由 /test 端点触发。"""
    try:
        from pydantic_ai import Agent
        agent = Agent(build_model(cfg), output_type=str)
        agent.run_sync("回复一个字：好", model_settings={"max_tokens": 16})
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def _probe_vision(cfg) -> dict:
    try:
        from pydantic_ai import Agent, BinaryContent
        agent = Agent(build_model(cfg, model_name=cfg.vision_model), output_type=str)
        agent.run_sync(["图中是什么颜色？一词回答。",
                        BinaryContent(data=_PROBE_PNG, media_type="image/png")],
                       model_settings={"max_tokens": 16})
        return {"ok": True, "supports_image": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@router.post("/test")
def test_llm(body: ProbeBody):
    cfg = _merged_cfg(body)
    if not cfg.api_key:
        raise HTTPException(400, "未配置 API Key")
    out = {"main": _probe_text(cfg), "vision": None}
    if cfg.vision_model:
        out["vision"] = _probe_vision(cfg)
    return out


# ---- 筛选器语义档案 ----

class SemanticsBody(BaseModel):
    meaning: Optional[str] = None
    bias: Optional[str] = None
    usage: Optional[str] = None
    caveats: Optional[str] = None
    combos: Optional[str] = None


@router.get("/semantics")
def get_semantics():
    from agent.chat.semantics import seed_defaults, get_all
    seed_defaults()
    return get_all()


@router.put("/semantics/{folder}/{name}")
def put_semantics(folder: str, name: str, body: SemanticsBody):
    from agent.chat.semantics import upsert
    if not upsert(f"{folder}/{name}", body.model_dump()):
        raise HTTPException(404, "未知筛选器 key")
    return {"ok": True}
```

- [ ] **Step 4: 运行测试通过**

Run: `cd backend && pytest tests/test_agent_probe_api.py tests/test_agent_config.py -v`
Expected: PASS（test_agent_config.py 若断言了 cooldown_minutes 往返，改为断言 vision_model 往返）

- [ ] **Step 5: Commit**

```bash
git add backend/api/agent.py backend/tests/test_agent_probe_api.py backend/tests/test_agent_config.py
git commit -m "feat(agent): model-list proxy, main/vision connectivity test, semantics CRUD API"
```

### Task 16: Settings 改造（模型下拉/测试按钮/视觉槽位/语义编辑）

**Files:**
- Modify: `frontend/src/views/Settings.vue`
- Modify: `frontend/src/api/client.js`（4 个新方法）

**Interfaces:**
- Consumes: Task 15 的 4 个端点
- Produces: Settings 页 Agent 配置区 v2 + 「筛选器语义」编辑区

- [ ] **Step 1: client.js 追加（chat 段之后）**

```javascript
  async fetchAgentModels(overrides = {}) {
    return request('/agent/models', { method: 'POST', body: JSON.stringify(overrides) })
  },

  async testAgentLlm(overrides = {}) {
    return request('/agent/test', { method: 'POST', body: JSON.stringify(overrides) })
  },

  async getScreenerSemantics() {
    return request('/agent/semantics')
  },

  async saveScreenerSemantics(key, fields) {
    return request(`/agent/semantics/${key}`, { method: 'PUT', body: JSON.stringify(fields) })
  },
```

- [ ] **Step 2: Settings.vue — Agent 配置区改造**

模板变更（在现有 Agent 配置 section 内）：

① 找到「模型」输入的 form-group（`<input v-model="agentForm.model" placeholder="例：gpt-4o / claude-opus-4-5" />` 所在块），替换为带 datalist 的版本，并在其后新增视觉模型块：

```vue
        <div class="form-group">
          <label>模型 <button type="button" class="btn-inline" @click="loadModels">刷新列表</button></label>
          <input v-model="agentForm.model" list="agent-model-list"
                 placeholder="例：deepseek/deepseek-v4-pro（可手输或从列表选）" />
          <datalist id="agent-model-list">
            <option v-for="m in modelList" :key="m" :value="m" />
          </datalist>
        </div>
        <div class="form-group">
          <label>视觉模型（可选，识图/截图分析用）</label>
          <input v-model="agentForm.vision_model" list="agent-model-list"
                 placeholder="留空 = 图片直传主模型（主模型须多模态）" />
        </div>
```

② 找到 `冷却时间 cooldown_minutes` 的 form-group（`<input v-model.number="agentForm.cooldown_minutes" …>` 所在整块），**删除**。

③ 「保存」按钮旁加测试按钮与结果显示（保存按钮所在块内）：

```vue
        <button type="button" class="btn secondary" :disabled="testing" @click="testLlm">
          {{ testing ? '测试中…' : '测试连接' }}
        </button>
        <span v-if="testResult" class="test-result">
          主模型 {{ testResult.main.ok ? '✅' : '❌ ' + testResult.main.error }}
          <template v-if="testResult.vision">
            ｜视觉 {{ testResult.vision.ok ? '✅ 支持图像' : '❌ ' + testResult.vision.error }}
          </template>
        </span>
```

script 变更：`agentForm` 初始对象删除 `cooldown_minutes` 字段、加 `vision_model: ''`；加载函数里同步（`agentForm.value = { …, vision_model: r.vision_model || '' }`，删 cooldown 行）；新增：

```javascript
const modelList = ref([])
const testing = ref(false)
const testResult = ref(null)

function agentOverrides() {
  const o = { provider: agentForm.value.provider, base_url: agentForm.value.base_url,
              model: agentForm.value.model, vision_model: agentForm.value.vision_model }
  if (agentApiKeyInput.value.trim()) o.api_key = agentApiKeyInput.value.trim()
  return o
}

async function loadModels() {
  try { modelList.value = (await api.fetchAgentModels(agentOverrides())).models }
  catch (e) { alert('模型列表获取失败：' + e.message) }
}

async function testLlm() {
  testing.value = true
  testResult.value = null
  try { testResult.value = await api.testAgentLlm(agentOverrides()) }
  catch (e) { testResult.value = { main: { ok: false, error: e.message }, vision: null } }
  finally { testing.value = false }
}
```

样式补充（scoped）：

```css
.btn-inline { font-size: 12px; padding: 1px 8px; margin-left: 8px; cursor: pointer; }
.test-result { font-size: 12.5px; margin-left: 10px; }
```

- [ ] **Step 3: Settings.vue — 「筛选器语义」编辑区（新 section，Agent 配置区之后）**

```vue
    <section class="card">
      <div class="section-head">
        <h3 class="section-title">筛选器语义档案</h3>
        <span class="hint">注入 agent system prompt，让它理解每个筛选器的含义（初稿可直接修改）</span>
      </div>
      <div v-for="s in semantics" :key="s.key" class="sem-card">
        <div class="sem-head" @click="s._open = !s._open">
          <strong>{{ s.label }}</strong> <code>{{ s.key }}</code>
          <span class="sem-bias">{{ s.bias }}</span>
        </div>
        <div v-if="s._open" class="sem-body">
          <label>含义<textarea v-model="s.meaning" rows="2" /></label>
          <label>方向倾向<input v-model="s.bias" /></label>
          <label>用法<textarea v-model="s.usage" rows="2" /></label>
          <label>局限<textarea v-model="s.caveats" rows="2" /></label>
          <label>建议叠加<textarea v-model="s.combos" rows="2" /></label>
          <button class="btn primary" @click="saveSemantics(s)">保存</button>
          <span v-if="s._msg" class="action-msg msg-ok">{{ s._msg }}</span>
        </div>
      </div>
    </section>
```

script 追加：

```javascript
const semantics = ref([])

async function loadSemantics() {
  semantics.value = (await api.getScreenerSemantics()).map(s => ({ ...s, _open: false, _msg: '' }))
}

async function saveSemantics(s) {
  await api.saveScreenerSemantics(s.key, {
    meaning: s.meaning, bias: s.bias, usage: s.usage, caveats: s.caveats, combos: s.combos,
  })
  s._msg = '已保存'
  setTimeout(() => { s._msg = '' }, 2000)
}
```

在页面既有的 `onMounted` 里追加 `loadSemantics()` 调用。样式：

```css
.sem-card { border: 1px solid rgba(128,128,128,.2); border-radius: 8px; margin-bottom: 8px; }
.sem-head { display: flex; gap: 10px; align-items: center; padding: 8px 12px; cursor: pointer; }
.sem-bias { margin-left: auto; font-size: 12px; opacity: .7; }
.sem-body { padding: 0 12px 12px; display: flex; flex-direction: column; gap: 6px; }
.sem-body label { display: flex; flex-direction: column; font-size: 12.5px; gap: 2px; }
```

- [ ] **Step 4: 构建验证 + 手测**

Run: `cd frontend && npm run build` → 成功。
手测：填 OpenRouter key →「刷新列表」出模型下拉 →「测试连接」主/视觉分别显示 ✅/❌ → 保存后重进页面 vision_model 回显 → 语义卡片展开、改一段、保存、刷新仍在 → 对话页问「底背离是什么信号」，回答能复述你改过的语义。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Settings.vue frontend/src/api/client.js
git commit -m "feat(agent-ui): model list + connectivity test + vision slot + screener semantics editor"
```

---

## Phase 7 — 旧批量层移除

### Task 17: 后端旧层拆除

**Files:**
- Delete: `backend/agent/worker.py`、`backend/agent/queue.py`、`backend/agent/agent_decider.py`、`backend/agent/prompts.py`、`backend/agent/store.py`
- Modify: `backend/tasks/executor.py`（去掉基线落库与入队）
- Modify: `backend/api/agent.py`（去掉 runs/rerun/rate/stats 端点）
- Delete tests: `test_agent_worker.py`、`test_agent_queue.py`、`test_agent_decider.py`、`test_agent_enqueue.py`、`test_agent_api.py`、`test_agent_stats.py`、`test_agent_store.py`、`test_agent_schema.py`、`test_agent_llm.py` 中仅测批量路径的用例（llm 工厂本身仍在用，保留工厂用例）

**Interfaces:**
- Consumes: —
- Produces: `backend/agent/` 仅剩 `decider.py`（任务管线阈值逻辑）、`config.py`、`llm.py`、`tools.py`、`validator.py`、`chat/`；表 agent_runs/agent_decisions 休眠保留。

- [ ] **Step 1: 删文件**

```bash
git rm backend/agent/worker.py backend/agent/queue.py backend/agent/agent_decider.py backend/agent/prompts.py backend/agent/store.py
git rm backend/tests/test_agent_worker.py backend/tests/test_agent_queue.py backend/tests/test_agent_decider.py backend/tests/test_agent_enqueue.py backend/tests/test_agent_api.py backend/tests/test_agent_stats.py backend/tests/test_agent_store.py backend/tests/test_agent_schema.py
```

（若某文件名不存在，先 `ls backend/tests/ | grep agent` 对照实际名单调整。）

- [ ] **Step 2: executor.py 拆除三处**

① 删除两处基线落库块（watchlist 与 market_scan 各一处，形如）：

```python
    try:
        from agent.store import record_rule_run
        record_rule_run(task_id, rule_out.decisions, signal_id_map)
    except Exception as e:
        applog("agent", "warn", f"baseline record failed: {e}")
```

② 删除两处 `_enqueue_agent_run(task_id, batch, rule_out.decisions, signal_id_map, actions)` 调用行。
③ 删除整个 `_enqueue_agent_run` 函数定义（executor.py:298-334 一带）。
`from agent.decider import SignalBatch, RuleDecider, bias_map_for`（第 7 行）**保留**——RuleDecider 是任务管线的阈值逻辑本体。

- [ ] **Step 3: api/agent.py 拆除**

删除端点函数：`list_runs`、`run_detail`、`rerun`、`rate_decision`、`stats` 及 `RateBody`、`MIN_RELIABLE_N`、`_safe_json`（config/models/test/semantics 端点保留）。顶部 `from database import get_db`、`import json` 若无引用一并删。

- [ ] **Step 4: 全量回归**

Run: `cd backend && pytest -m "not network" -q`
Expected: 全 PASS。另跑红线自查：

```bash
cd backend && grep -rn "agent.queue\|agent.worker\|agent_decider\|record_rule_run\|enqueue_run" --include="*.py" . | grep -v tests/ | grep -v chat/
```

Expected: 无输出。

- [ ] **Step 5: Commit**

```bash
git add -A backend
git commit -m "refactor(agent): remove batch verdict layer — worker/queue/decider/review API; RuleDecider seam and outcome tracking stay"
```

### Task 18: 前端旧层拆除 + 文档收尾

**Files:**
- Delete: `frontend/src/views/Agent.vue`
- Modify: `frontend/src/api/client.js`（删 5 个方法）、`frontend/src/views/Tasks.vue`（删 agent_decide 动作）、`frontend/src/views/Trade.vue`（保留 symbol/direction 预填——对话 agent 引导链接还要用）
- Modify: `CLAUDE.md`

**Interfaces:** —

- [ ] **Step 1: 前端拆除**

```bash
git rm frontend/src/views/Agent.vue
```

`client.js` 删除方法：`listAgentRuns`、`getAgentRun`、`rerunAgentRun`、`rateAgentDecision`、`getAgentStats`（`getAgentConfig`/`updateAgentConfig` 保留）。
`Tasks.vue`：`grep -n "agent_decide" frontend/src/views/Tasks.vue` 找到动作复选框及其 label（f6db7fa 引入的 5 行左右），删除该动作项。

- [ ] **Step 2: 构建验证**

Run: `cd frontend && npm run build`
Expected: 成功（若有残留 import Agent.vue 的报错，按报错清除）。

- [ ] **Step 3: CLAUDE.md 更新**

「Agent decision layer」小节整段替换为：

```markdown
### Chat agent

Conversational agent at `/agent` (Manus-style): multi-session chat persisted to
SQLite, background worker drains `chat_turns`, events append to `chat_events`,
SSE stream (`GET /api/chat/sessions/{id}/stream?after=N`) is a resumable
observation window. Tools are read-only (screener scan with progress events,
klines/indicators/structure, market snapshot/overview, signal history,
ChartShot capture + vision relay, position-plan preview, account overview) and
throttled; per-turn `max_tool_calls` + `deep_dive_limit` budgets. Screener
semantics profiles live in `screener_semantics` (Settings-editable, injected
into the system prompt). Vision uses a separate `vision_model` slot (same
provider/key). Red lines: `backend/agent/` never imports order-placing
functions; execution always goes through the human-confirmed Trade page
(`/trade?symbol=…&direction=…` prefill). Design docs:
`docs/superpowers/specs/2026-07-04-chat-agent-design.md`.
```

同文件「Key directories」中 `backend/agent/` 一行改为：

```markdown
- `backend/agent/` — chat agent: `chat/` (store/events/runtime/worker/vision/semantics/prompts), `tools.py` (read-only, throttled), `decider.py` (RuleDecider — task-pipeline threshold logic), `config.py` (Fernet key + vision_model), `llm.py`, `validator.py` (stub)
```

「Task execution flow」第 3/7 步删去批量 agent 相关描述（保留 RuleDecider 阈值语义）；Database 一节 Key tables 补 `chat_sessions, chat_messages, chat_turns, chat_events, screener_semantics`。

- [ ] **Step 4: 终验**

```bash
cd backend && pytest -m "not network" -q && cd ../frontend && npm run build
```

Expected: 全绿 + 构建成功。

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(agent-ui): remove batch review page and agent_decide action; document chat agent in CLAUDE.md"
```

---

## 完成定义（DoD）

1. `cd backend && pytest -m "not network"` 全绿；`cd frontend && npm run build` 成功。
2. 手测主流程：配 key → 测试连接 → 对话跑一次筛选扫描（看到进度卡片）→ 刷新页面续播 → 停止按钮 → 语义编辑生效。
3. 红线自查（Task 6/17 的 grep）无命中。
4. `git log --oneline` 每任务一提交，信息符合仓库惯例。

