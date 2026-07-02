# Agent 决策层 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 `docs/superpowers/specs/2026-07-02-agent-decision-layer-design.md` 实现 LLM 决策层：决策缝 + 数据地基加固（Phase 0）、agent 基础设施（Phase 1）、异步 AgentDecider worker（Phase 2）、复盘页（Phase 3）、闭环统计 + 验证器接口（Phase 4）。

**Architecture:** executor 的隐式阈值决策抽成 `Decider` 接口（RuleDecider 零行为变化并落库基线）；LLM 决策经 DB 队列（`agent_runs`）由独立 daemon 线程消费（PydanticAI，双协议），只读工具带节流，绝不下单；outcomes 追踪从内存 Timer 改为持久化 `outcome_checks` + 轮询线程；前端新增 /agent 复盘页，闭环统计方向感知胜率并与规则基线对比。

**Tech Stack:** FastAPI + SQLite（既有）、`pydantic-ai-slim[openai,anthropic]`（唯一新增依赖）、Vue 3（既有）。

**House rules（贯穿所有任务）:**
- 工作目录 `backend/`，测试命令 `cd backend && python -m pytest tests/test_X.py -v`（网络测试标 `pytest.mark.network`，CI 用 `-m "not network"` 跳过）。
- 提交直接落 main（仓库惯例），每任务一提交，消息用 `feat(agent):` / `fix(tracker):` 等前缀。
- DB 访问一律 `get_db(settings.db_path)` 短事务（开→执行→commit→close），worker/poller 线程各用自己的连接。
- 新表只能**追加**进 `database.py` 的 SCHEMA 常量（CREATE TABLE IF NOT EXISTS；改已有表体是静默 no-op，禁止）。
- 所有中文 UI 文案、CSS 变量主题、`wohub-*` localStorage 约定照旧。

---

## Phase 0 — 决策缝 + 数据地基加固

### Task 1: 新表 schema（outcome_checks + agent_runs + agent_decisions）

**Files:**
- Modify: `backend/database.py`（SCHEMA 常量末尾追加）
- Test: `backend/tests/test_agent_schema.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_schema.py
import os
import sqlite3
import pytest


def _cols(table):
    db = sqlite3.connect(os.environ["DB_PATH"])
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    db.close()
    return {r[1] for r in rows}


def test_outcome_checks_table():
    assert {"id", "signal_id", "horizon", "due_at", "done", "error", "created_at"} <= _cols("outcome_checks")


def test_agent_runs_table():
    assert {"id", "task_id", "decider", "status", "context_json", "trace_json",
            "model", "prompt_version", "input_tokens", "output_tokens",
            "error", "created_at", "started_at", "finished_at"} <= _cols("agent_runs")


def test_agent_decisions_table():
    assert {"id", "run_id", "signal_id", "signal_ids_json", "symbol", "timeframe",
            "direction", "confidence", "reasons", "factors_json",
            "human_rating", "created_at"} <= _cols("agent_decisions")


def test_indexes_exist():
    db = sqlite3.connect(os.environ["DB_PATH"])
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    db.close()
    assert "idx_outcome_checks_due" in names
    assert "idx_agent_runs_status" in names
    assert "idx_agent_decisions_symbol" in names
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_agent_schema.py -v`
Expected: FAIL（表不存在）

- [ ] **Step 3: 在 SCHEMA 末尾（trading_orders 之后、结束三引号之前）追加**

```sql
CREATE TABLE IF NOT EXISTS outcome_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER REFERENCES signals(id),
    horizon TEXT NOT NULL CHECK (horizon IN ('1h', '4h', '24h')),
    due_at TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_outcome_checks_due ON outcome_checks(done, due_at);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id),
    decider TEXT NOT NULL CHECK (decider IN ('agent', 'rule')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed')),
    context_json TEXT,
    trace_json TEXT,
    model TEXT,
    prompt_version TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_task ON agent_runs(task_id, created_at);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER REFERENCES agent_runs(id),
    signal_id INTEGER REFERENCES signals(id),
    signal_ids_json TEXT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short', 'skip')),
    confidence REAL,
    reasons TEXT,
    factors_json TEXT,
    human_rating INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_symbol ON agent_decisions(symbol, timeframe, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_run ON agent_decisions(run_id);
```

- [ ] **Step 4: 运行确认通过**（含既有 `tests/test_database.py` —— 它断言表数量，若有断言需 +3）

Run: `cd backend && python -m pytest tests/test_agent_schema.py tests/test_database.py -v`
Expected: PASS

- [ ] **Step 5: Commit** `feat(agent): schema for outcome_checks, agent_runs, agent_decisions`

### Task 2: tracker 持久化——写 outcome_checks 行，检查函数返回错误

**Files:**
- Modify: `backend/tasks/tracker.py`
- Test: `backend/tests/test_tracker_persistent.py`

背景：现在 `schedule_outcome_tracking`（tracker.py:49-64）起 3 个内存 `threading.Timer`，重启即丢；`_check_outcome`（tracker.py:67-116）在 price=0 / snapshot 缺失时静默 return。改为：调度=插 3 行 `outcome_checks`；检查=可独立调用的 `run_outcome_check`，返回 `None`（成功）或错误字符串。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_tracker_persistent.py
import os
from unittest.mock import patch
from database import get_db


def _db():
    return get_db(os.environ["DB_PATH"])


def _mk_signal(db, symbol="BTCUSDT"):
    cur = db.execute(
        "INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) VALUES (NULL, ?, 'Binance', '底背离', '1h')",
        (symbol,))
    db.commit()
    return cur.lastrowid


def test_schedule_inserts_three_checks():
    from tasks.tracker import schedule_outcome_tracking
    db = _db()
    sid = _mk_signal(db)
    schedule_outcome_tracking(sid, "BTCUSDT", "Binance")
    rows = db.execute("SELECT horizon, done FROM outcome_checks WHERE signal_id = ? ORDER BY horizon", (sid,)).fetchall()
    db.close()
    assert [(r["horizon"], r["done"]) for r in rows] == [("1h", 0), ("24h", 0), ("4h", 0)]


TICKERS = ([{"symbol": "BTCUSDT", "exchange": "Binance", "lastPrice": 110.0,
             "priceChangePercent": 1.0, "volume24h": 5e6, "high24h": 0, "low24h": 0}], None)


def test_run_outcome_check_writes_outcome():
    from tasks import tracker
    db = _db()
    sid = _mk_signal(db)
    db.execute("INSERT INTO snapshots (signal_id, price, volume_24h, change_24h, funding_rate) VALUES (?, 100.0, 1e6, 0, 0)", (sid,))
    db.commit()
    with patch.object(tracker, "fetch_all_tickers", return_value=TICKERS):
        err = tracker.run_outcome_check(sid, "BTCUSDT", "Binance", "1h")
    assert err is None
    row = db.execute("SELECT change_1h FROM outcomes WHERE signal_id = ?", (sid,)).fetchone()
    db.close()
    assert abs(row["change_1h"] - 10.0) < 1e-6


def test_run_outcome_check_reports_price_miss():
    from tasks import tracker
    db = _db()
    sid = _mk_signal(db, symbol="NOPE")
    db.execute("INSERT INTO snapshots (signal_id, price) VALUES (?, 100.0)", (sid,))
    db.commit()
    db.close()
    with patch.object(tracker, "fetch_all_tickers", return_value=TICKERS):
        err = tracker.run_outcome_check(sid, "NOPE", "Binance", "1h")
    assert err is not None and "price" in err
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_tracker_persistent.py -v`
Expected: FAIL（`run_outcome_check` 不存在；schedule 仍起 Timer）

- [ ] **Step 3: 重写 tracker.py 的调度与检查**

`schedule_outcome_tracking` 整体替换（删除 threading.Timer 路径，`import threading`/`time` 若不再使用一并清理）：

```python
HORIZONS = [("1h", "+1 hour"), ("4h", "+4 hours"), ("24h", "+1 day")]


def schedule_outcome_tracking(signal_id, symbol, exchange):
    """Persist due-checks; the outcome poller thread executes them when due.
    Survives process restarts (unlike the former in-memory Timers)."""
    db = get_db(settings.db_path)
    for horizon, offset in HORIZONS:
        db.execute(
            "INSERT INTO outcome_checks (signal_id, horizon, due_at) VALUES (?, ?, datetime('now', ?))",
            (signal_id, horizon, offset),
        )
    db.commit()
    db.close()
```

`_check_outcome` 改名为 `run_outcome_check`，签名 `(signal_id, symbol, exchange, period) -> str | None`：逻辑保持，但所有静默 return 改为 `return "..."` 错误串（`f"price miss: no ticker for {symbol}@{exchange}"`、`"snapshot missing or price=0"`），成功路径末尾 `return None`；外层 `except Exception as e: return str(e)`。注意 symbol/exchange 仍需参与 ticker 匹配（poller 从 signals 表 join 出来传入）。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_tracker_persistent.py tests/test_tracker.py -v`
Expected: PASS
⚠️ 既有 `tests/test_tracker.py` 在模块顶部 `from tasks.tracker import ... _check_outcome`——改名后会在**收集阶段**就 ImportError（不是断言失败）；把该 import 与相关用例同步改为 `run_outcome_check`。

- [ ] **Step 5: Commit** `feat(tracker): persistent outcome_checks replace in-memory timers`

### Task 3: outcome 轮询线程 + lifespan 接线

**Files:**
- Create: `backend/tasks/outcome_poller.py`
- Modify: `backend/main.py:29-33`（lifespan）
- Test: `backend/tests/test_outcome_poller.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_outcome_poller.py
import os
from unittest.mock import patch
from database import get_db


def test_process_due_checks_marks_done_and_error():
    from tasks import tracker
    from tasks.outcome_poller import process_due_checks
    db = get_db(os.environ["DB_PATH"])
    cur = db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) VALUES (NULL, 'BTCUSDT', 'Binance', 'x', '1h')")
    sid = cur.lastrowid
    db.execute("INSERT INTO snapshots (signal_id, price) VALUES (?, 100.0)", (sid,))
    # one due, one not due
    db.execute("INSERT INTO outcome_checks (signal_id, horizon, due_at) VALUES (?, '1h', datetime('now', '-1 minute'))", (sid,))
    db.execute("INSERT INTO outcome_checks (signal_id, horizon, due_at) VALUES (?, '4h', datetime('now', '+4 hours'))", (sid,))
    db.commit()
    tickers = ([{"symbol": "BTCUSDT", "exchange": "Binance", "lastPrice": 105.0,
                 "priceChangePercent": 0, "volume24h": 0, "high24h": 0, "low24h": 0}], None)
    with patch.object(tracker, "fetch_all_tickers", return_value=tickers):
        n = process_due_checks(limit=50)
    assert n == 1
    rows = db.execute("SELECT horizon, done, error FROM outcome_checks WHERE signal_id = ? ORDER BY horizon", (sid,)).fetchall()
    db.close()
    assert [(r["horizon"], r["done"]) for r in rows] == [("1h", 1), ("4h", 0)]
    assert rows[0]["error"] is None


def test_poller_thread_start_stop():
    from tasks.outcome_poller import start_poller, stop_poller
    start_poller(interval=0.05)
    stop_poller()  # 应在 join 超时内干净退出
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_outcome_poller.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 outcome_poller.py**

```python
# backend/tasks/outcome_poller.py
"""Executes due outcome_checks rows. Restart-safe replacement for the
former threading.Timer scheme: on boot the poller simply picks up any
overdue rows (including those written before a restart)."""
import threading
from database import get_db
from config import settings
from app_logger import log as applog

_stop = threading.Event()
_thread = None


def process_due_checks(limit=50) -> int:
    """Run all due checks once. Returns number processed. Own connection,
    short transactions; safe to call from any thread (incl. tests)."""
    from tasks.tracker import run_outcome_check
    db = get_db(settings.db_path)
    rows = db.execute(
        """SELECT c.id, c.signal_id, c.horizon, s.symbol, s.exchange
           FROM outcome_checks c JOIN signals s ON s.id = c.signal_id
           WHERE c.done = 0 AND c.due_at <= datetime('now')
           ORDER BY c.due_at LIMIT ?""",
        (limit,),
    ).fetchall()
    db.close()
    for r in rows:
        err = run_outcome_check(r["signal_id"], r["symbol"], r["exchange"], r["horizon"])
        db = get_db(settings.db_path)
        db.execute("UPDATE outcome_checks SET done = 1, error = ? WHERE id = ?", (err, r["id"]))
        db.commit()
        db.close()
        if err:
            applog("tracker", "warn", f"outcome check #{r['id']} ({r['symbol']} {r['horizon']}): {err}")
    return len(rows)


def _loop(interval):
    while not _stop.wait(interval):
        try:
            process_due_checks()
        except Exception as e:
            applog("tracker", "error", f"outcome poller: {e}")


def start_poller(interval=60.0):
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), daemon=True, name="outcome-poller")
    _thread.start()


def stop_poller():
    _stop.set()
    if _thread:
        _thread.join(timeout=5)
```

lifespan（main.py）在 `start_all_enabled()` 后加 `from tasks.outcome_poller import start_poller, stop_poller; start_poller()`，`yield` 后 `stop_poller()`（在 `stop_scheduler()` 之前）。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && python -m pytest tests/test_outcome_poller.py -v`
Expected: PASS

- [ ] **Step 5: Commit** `feat(tracker): outcome poller thread executes due checks (restart-safe)`

### Task 4: 修 _record_signals（叉乘 bug + indicator 纯 label）

**Files:**
- Modify: `backend/tasks/executor.py:111-116, 150, 196, 292-325`
- Test: `backend/tests/test_record_signals.py`

背景：`_record_signals(task_id, overlaps, resolutions, all_results)` 把 `label(res)` 双编码串存进 indicator，且对每个 label 再叉乘全部 resolutions（executor.py:308-314）——手动 `/test` 多周期任务会写入错误 timeframe 的重复行。改为传入**精确三元组**。

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_record_signals.py
import os
from unittest.mock import patch
from database import get_db


def test_record_signals_exact_rows_no_cross_product():
    from tasks import executor
    entries = [  # (raw_symbol, label, resolution)
        ("BINANCE:BTCUSDT.P", "底背离", "1h"),
        ("BINANCE:BTCUSDT.P", "超卖", "1h"),
        ("BINANCE:ETHUSDT.P", "底背离", "4h"),
    ]
    with patch.object(executor, "record_snapshot"), patch.object(executor, "schedule_outcome_tracking"):
        executor._record_signals(None, entries)
    db = get_db(os.environ["DB_PATH"])
    rows = db.execute("SELECT symbol, indicator, timeframe FROM signals ORDER BY id").fetchall()
    db.close()
    assert [(r["symbol"], r["indicator"], r["timeframe"]) for r in rows] == [
        ("BTCUSDT", "底背离", "1h"), ("BTCUSDT", "超卖", "1h"), ("ETHUSDT", "底背离", "4h")]


def test_record_signals_returns_id_map():
    from tasks import executor
    with patch.object(executor, "record_snapshot"), patch.object(executor, "schedule_outcome_tracking"):
        id_map = executor._record_signals(None, [("BINANCE:BTCUSDT.P", "底背离", "1h")])
    assert list(id_map.keys()) == [("BTCUSDT", "1h")]
    assert len(id_map[("BTCUSDT", "1h")]) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_record_signals.py -v`
Expected: FAIL（签名不符）

- [ ] **Step 3: 重写 _record_signals 与两处调用点**

```python
# 模块顶部（import 区）——从函数内移出，使测试能 patch executor.record_snapshot：
from tasks.tracker import record_snapshot, schedule_outcome_tracking


def _record_signals(task_id, entries):
    """entries: list of (raw_symbol, label, resolution) — exact rows to insert.
    Returns {(clean_symbol, resolution): [signal_id, ...]} for decision linkage."""
    id_map = {}
    try:
        db = get_db(settings.db_path)
        pending = []
        for sym, label, res in entries:
            clean = sym.replace("BINANCE:", "").replace(".P", "")
            exchange = "Binance"
            if "OKX:" in sym:
                exchange = "OKX"
            elif "BYBIT:" in sym:
                exchange = "Bybit"
            cursor = db.execute(
                "INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) VALUES (?, ?, ?, ?, ?)",
                (task_id, clean, exchange, label, res),
            )
            pending.append((cursor.lastrowid, clean, exchange))
            id_map.setdefault((clean, res), []).append(cursor.lastrowid)
        db.commit()
        db.close()

        for signal_id, clean, exchange in pending:
            record_snapshot(signal_id, clean, exchange)
            schedule_outcome_tracking(signal_id, clean, exchange)
    except Exception as e:
        print(f"[executor] Failed to record signals: {e}")
    return id_map
```

调用点改法：
- `_exec_watchlist_signal`（executor.py:150）：从 `signals_by_res` 生成精确三元组：
  ```python
  entries = [(sym, label, res)
             for res, sigs in signals_by_res.items()
             for sym, labels in sigs.items() for label in labels]
  signal_ids = _record_signals(task_id, entries)
  ```
- `_exec_market_scan`（executor.py:196）：从 `all_results` 反查命中的精确 (sym,label,res)：
  ```python
  entries = [(sym, r["label"], r["resolution"])
             for r in all_results for sym in r["symbols"] if sym in overlaps]
  signal_ids = _record_signals(task_id, entries)
  ```

- [ ] **Step 4: 运行全量测试确认通过**

Run: `cd backend && python -m pytest -m "not network" -q`
Expected: PASS（若有测试依赖旧 `label(res)` indicator 编码，修正该测试）

- [ ] **Step 5: Commit** `fix(executor): exact signal rows — no res cross-product, indicator stores pure label`

### Task 5: Decider 接缝 + RuleDecider（零行为变化）

**Files:**
- Create: `backend/agent/__init__.py`（空）、`backend/agent/decider.py`
- Modify: `backend/tasks/executor.py:88-116, 176-177`
- Test: `backend/tests/test_rule_decider.py`

- [ ] **Step 1: 写失败测试（golden：与旧内联逻辑逐项等价）**

```python
# backend/tests/test_rule_decider.py
from agent.decider import SignalBatch, RuleDecider

RESULTS = [
    {"label": "底背离", "resolution": "1h", "symbols": ["BINANCE:AUSDT.P", "BINANCE:BUSDT.P"], "count": 2},
    {"label": "超卖",   "resolution": "1h", "symbols": ["BINANCE:AUSDT.P"], "count": 1},
    {"label": "底背离", "resolution": "4h", "symbols": ["BINANCE:CUSDT.P"], "count": 1},
]


def _batch(task_type="watchlist_signal", screeners=2, threshold=2):
    return SignalBatch(
        task_id=1, task_type=task_type,
        config={"resolutions": ["1h", "4h"], "overlap_threshold": threshold,
                "screeners": [{}] * screeners},
        results=RESULTS, bias_map={"底背离": "long", "超卖": "long"})


def test_watchlist_multi_screener_overlap():
    out = RuleDecider().decide(_batch())
    assert out.signals_by_res == {"1h": {"BINANCE:AUSDT.P": ["底背离", "超卖"]}, "4h": {}}


def test_watchlist_single_screener_passes_all():
    out = RuleDecider().decide(_batch(screeners=1))
    assert out.signals_by_res["1h"] == {"BINANCE:AUSDT.P": ["底背离", "超卖"], "BINANCE:BUSDT.P": ["底背离"]}


def test_market_scan_overlaps():
    out = RuleDecider().decide(_batch(task_type="market_scan"))
    assert out.overlaps == {"BINANCE:AUSDT.P": ["底背离", "超卖"]}


def test_decisions_direction_from_unanimous_bias():
    out = RuleDecider().decide(_batch())
    d = out.decisions[0]
    assert (d.symbol, d.timeframe, d.direction) == ("BINANCE:AUSDT.P", "1h", "long")
    assert d.confidence is None


def test_decisions_skip_on_conflicting_bias():
    b = _batch()
    b.bias_map = {"底背离": "long", "超卖": "short"}
    assert RuleDecider().decide(b).decisions[0].direction == "skip"
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && python -m pytest tests/test_rule_decider.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 decider.py**

```python
# backend/agent/decider.py
"""Decision seam. RuleDecider reproduces the executor's former inline
threshold logic verbatim (golden-tested); AgentDecider (Phase 2) plugs in
behind the same SignalBatch input, asynchronously."""
from dataclasses import dataclass, field

# Screener key -> direction bias. Only unambiguous screeners are mapped;
# labels absent here contribute no direction to rule-baseline decisions.
SCREENER_BIAS = {
    "oscillator/divergence_top": "short",
    "oscillator/divergence_bottom": "long",
    "oscillator/overbought_zone": "short",
    "oscillator/oversold_zone": "long",
}


def bias_map_for(screeners: list[dict]) -> dict[str, str]:
    """label -> 'long'|'short' from task config screeners list."""
    out = {}
    for sc in screeners:
        key = f"{sc.get('folder_type', '')}/{sc.get('screener_name', '')}"
        if key in SCREENER_BIAS:
            out[sc.get("label", sc.get("screener_name", ""))] = SCREENER_BIAS[key]
    return out


@dataclass
class SignalBatch:
    task_id: int
    task_type: str                    # watchlist_signal | market_scan
    config: dict                      # task config (resolutions, overlap_threshold, screeners...)
    results: list                     # [{label, resolution, symbols, count}]
    bias_map: dict = field(default_factory=dict)
    cross: dict = field(default_factory=dict)   # build_cross_analysis output (market_scan)


@dataclass
class Decision:
    symbol: str          # raw form, e.g. 'BINANCE:BTCUSDT.P'
    timeframe: str
    direction: str       # long | short | skip
    confidence: float | None
    reasons: str
    labels: list


@dataclass
class DeciderOutput:
    signals_by_res: dict = field(default_factory=dict)  # watchlist: {res: {sym: [labels]}}
    overlaps: dict = field(default_factory=dict)        # market_scan: {sym: [labels]}
    decisions: list = field(default_factory=list)


def _rule_decision(sym, res, labels, bias_map, reason):
    biases = {bias_map[l] for l in labels if l in bias_map}
    direction = biases.pop() if len(biases) == 1 else "skip"
    return Decision(symbol=sym, timeframe=res, direction=direction,
                    confidence=None, reasons=reason, labels=list(labels))


class RuleDecider:
    def decide(self, batch: SignalBatch) -> DeciderOutput:
        if batch.task_type == "market_scan":
            return self._market_scan(batch)
        return self._watchlist(batch)

    def _watchlist(self, batch):
        resolutions = batch.config.get("resolutions", ["1h"])
        threshold = batch.config.get("overlap_threshold", 2)
        is_single = len(batch.config.get("screeners", [])) <= 1
        signals_by_res = {}
        for res in resolutions:
            res_results = [r for r in batch.results if r["resolution"] == res]
            sym_labels = {}
            for r in res_results:
                for sym in r["symbols"]:
                    sym_labels.setdefault(sym, []).append(r["label"])
            if is_single:
                signals_by_res[res] = sym_labels
            else:
                signals_by_res[res] = {s: l for s, l in sym_labels.items() if len(l) >= threshold}
        decisions = [
            _rule_decision(sym, res, labels, batch.bias_map,
                           f"规则：{len(labels)} 个筛选器命中（阈值 {threshold}）")
            for res, sigs in signals_by_res.items() for sym, labels in sigs.items()
        ]
        return DeciderOutput(signals_by_res=signals_by_res, decisions=decisions)

    def _market_scan(self, batch):
        from sources.pine_screener import build_cross_analysis
        threshold = batch.config.get("overlap_threshold", 2)
        analysis = batch.cross or build_cross_analysis(batch.results)
        overlaps = {s: l for s, l in analysis.get("screener_overlap", {}).items() if len(l) >= threshold}
        # timeframe per decision: the resolutions this symbol actually hit
        decisions = []
        for sym, labels in overlaps.items():
            hit_res = sorted({r["resolution"] for r in batch.results if sym in r["symbols"]})
            for res in hit_res:
                decisions.append(_rule_decision(sym, res, labels, batch.bias_map,
                                                f"规则：跨筛选器叠加 {len(labels)}（阈值 {threshold}）"))
        return DeciderOutput(overlaps=overlaps, decisions=decisions)
```

executor 改造：
- `_exec_watchlist_signal`：删除 88-109 行内联逻辑，替换为：
  ```python
  from agent.decider import SignalBatch, RuleDecider, bias_map_for
  batch = SignalBatch(task_id=task_id, task_type="watchlist_signal", config=config,
                      results=all_results, bias_map=bias_map_for(screeners))
  rule_out = RuleDecider().decide(batch)
  signals_by_res = rule_out.signals_by_res
  ```
  （`all_signals` 合并、消息构建、推送逻辑不动。）
- `_exec_market_scan`：176-177 替换为同样的 batch + `overlaps = rule_out.overlaps`（`build_cross_analysis` 调用移入 RuleDecider；`analysis` 若消息构建处用到只有 overlaps，无需保留）。

- [ ] **Step 4: 运行全量测试**

Run: `cd backend && python -m pytest -m "not network" -q`
Expected: PASS

- [ ] **Step 5: Commit** `feat(agent): Decider seam — RuleDecider wraps threshold logic, zero behavior change`

### Task 6: RuleDecider 基线落库

**Files:**
- Create: `backend/agent/store.py`
- Modify: `backend/tasks/executor.py`（两个 handler 在 `_record_signals` 后调用）
- Test: `backend/tests/test_agent_store.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_store.py
import os, json
from database import get_db
from agent.decider import Decision
from agent.store import record_rule_run


def _seed_signals(n=2):
    """FK 约束开启（get_db 设 PRAGMA foreign_keys=ON），signal_id 必须真实存在。"""
    db = get_db(os.environ["DB_PATH"])
    ids = []
    for _ in range(n):
        cur = db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
                         "VALUES (NULL, 'AUSDT', 'Binance', '底背离', '1h')")
        ids.append(cur.lastrowid)
    db.commit()
    db.close()
    return ids


def test_record_rule_run_writes_run_and_decisions():
    sid1, sid2 = _seed_signals(2)
    decisions = [Decision(symbol="BINANCE:AUSDT.P", timeframe="1h", direction="long",
                          confidence=None, reasons="规则：2 个筛选器命中（阈值 2）", labels=["底背离", "超卖"])]
    id_map = {("AUSDT", "1h"): [sid1, sid2]}
    run_id = record_rule_run(task_id=None, decisions=decisions, signal_id_map=id_map)
    db = get_db(os.environ["DB_PATH"])
    run = db.execute("SELECT decider, status FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    d = db.execute("SELECT * FROM agent_decisions WHERE run_id = ?", (run_id,)).fetchone()
    db.close()
    assert (run["decider"], run["status"]) == ("rule", "done")
    assert (d["symbol"], d["timeframe"], d["direction"], d["signal_id"]) == ("AUSDT", "1h", "long", sid1)
    assert json.loads(d["signal_ids_json"]) == [sid1, sid2]


def test_record_rule_run_empty_is_noop():
    assert record_rule_run(task_id=None, decisions=[], signal_id_map={}) is None
```

- [ ] **Step 2: 运行确认失败** — `cd backend && python -m pytest tests/test_agent_store.py -v` → FAIL

- [ ] **Step 3: 实现 store.py 并接线**

```python
# backend/agent/store.py
"""Persistence for agent runs & decisions."""
import json
from database import get_db
from config import settings


def _clean(sym: str) -> str:
    return sym.replace("BINANCE:", "").replace(".P", "")


def record_rule_run(task_id, decisions, signal_id_map) -> int | None:
    """Baseline: one 'rule' run per signal-producing execution. Written for
    every watchlist/market_scan run regardless of agent_decide action."""
    if not decisions:
        return None
    db = get_db(settings.db_path)
    cur = db.execute(
        "INSERT INTO agent_runs (task_id, decider, status, finished_at) VALUES (?, 'rule', 'done', datetime('now'))",
        (task_id,))
    run_id = cur.lastrowid
    for d in decisions:
        ids = signal_id_map.get((_clean(d.symbol), d.timeframe), [])
        db.execute(
            """INSERT INTO agent_decisions
               (run_id, signal_id, signal_ids_json, symbol, timeframe, direction, confidence, reasons, factors_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (run_id, ids[0] if ids else None, json.dumps(ids), _clean(d.symbol),
             d.timeframe, d.direction, d.confidence, d.reasons))
    db.commit()
    db.close()
    return run_id
```

executor 两个 handler 在 `signal_ids = _record_signals(...)` 之后追加（try/except 包裹、失败仅 applog，不影响主流程）：

```python
try:
    from agent.store import record_rule_run
    record_rule_run(task_id, rule_out.decisions, signal_ids)
except Exception as e:
    applog("agent", "warn", f"baseline record failed: {e}")
```

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest -m "not network" -q` → PASS

- [ ] **Step 5: Commit** `feat(agent): persist RuleDecider baseline runs/decisions`

---

## Phase 1 — Agent 基础设施

### Task 7: 依赖 pydantic-ai-slim

**Files:**
- Modify: `backend/pyproject.toml`（dependencies 列表）

- [ ] **Step 1: 追加依赖** `"pydantic-ai-slim[openai,anthropic]>=1.0,<2"` 到 `[project] dependencies`

- [ ] **Step 2: 安装并验证 import**

Run: `cd backend && pip install -e . && python -c "from pydantic_ai import Agent; from pydantic_ai.models.test import TestModel; print('ok')"`
Expected: `ok`
⚠️ 若 import 路径报错（PydanticAI API 随版本演进），运行 `pip show pydantic-ai-slim` 查版本并对照 https://ai.pydantic.dev 修正后续任务中的 import——**本计划代码按 v1.x API 编写**（`Agent`, `RunContext`, `output_type=`, `run_sync`, `UsageLimits`, `TestModel`/`FunctionModel`）。

- [ ] **Step 3: 全量测试不回归** — `cd backend && python -m pytest -m "not network" -q` → PASS

- [ ] **Step 4: Commit** `chore(agent): add pydantic-ai-slim[openai,anthropic] dependency`

### Task 8: agent_config 表 + 配置模块 + API

**Files:**
- Modify: `backend/database.py`（SCHEMA 追加 agent_config）
- Create: `backend/agent/config.py`、`backend/api/agent.py`
- Modify: `backend/api/__init__.py`（protected 注册）
- Test: `backend/tests/test_agent_config.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_config.py
import pytest


@pytest.mark.asyncio
async def test_get_config_defaults(client):
    async with client as c:
        r = await c.get("/api/agent/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["provider"] in ("openai", "anthropic")
    assert "api_key" not in body           # 密钥绝不回读
    assert body["has_api_key"] is False


@pytest.mark.asyncio
async def test_update_config_roundtrip_key_masked(client):
    async with client as c:
        r = await c.put("/api/agent/config", json={
            "provider": "openai", "base_url": "https://api.example.com/v1",
            "model": "gpt-5", "api_key": "sk-secret", "enabled": True,
            "max_tool_calls": 10, "deep_dive_limit": 3, "cooldown_minutes": 120,
            "push_verdict": False, "credential_id": None, "max_tokens": 4096})
        assert r.status_code == 200
        r2 = await c.get("/api/agent/config")
    body = r2.json()
    assert body["has_api_key"] is True and "api_key" not in body
    assert body["model"] == "gpt-5" and body["enabled"] is True


def test_load_config_decrypts_key():
    from agent.config import save_config, load_config
    save_config({"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "k123",
                 "base_url": "", "enabled": True, "max_tool_calls": 15, "deep_dive_limit": 5,
                 "cooldown_minutes": 240, "push_verdict": False, "credential_id": None,
                 "max_tokens": 4096})
    cfg = load_config()
    assert cfg.api_key == "k123" and cfg.provider == "anthropic" and cfg.enabled
```

- [ ] **Step 2: 运行确认失败** — `cd backend && python -m pytest tests/test_agent_config.py -v` → FAIL

- [ ] **Step 3: 实现**

SCHEMA 追加：

```sql
CREATE TABLE IF NOT EXISTS agent_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL DEFAULT 'openai' CHECK (provider IN ('openai', 'anthropic')),
    base_url TEXT NOT NULL DEFAULT '',
    api_key_enc TEXT,
    model TEXT NOT NULL DEFAULT '',
    max_tokens INTEGER NOT NULL DEFAULT 4096,
    max_tool_calls INTEGER NOT NULL DEFAULT 15,
    deep_dive_limit INTEGER NOT NULL DEFAULT 5,
    cooldown_minutes INTEGER NOT NULL DEFAULT 240,
    credential_id INTEGER,
    push_verdict INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

```python
# backend/agent/config.py
"""Single-row agent config. API key is Fernet-encrypted with the same
SECRET_KEY-derived key as trading credentials — rotating SECRET_KEY
invalidates the stored key (documented operator behavior)."""
from dataclasses import dataclass
from database import get_db
from config import settings
from trading.credentials import encrypt_secret, decrypt_secret

FIELDS = ("provider", "base_url", "model", "max_tokens", "max_tool_calls",
          "deep_dive_limit", "cooldown_minutes", "credential_id", "push_verdict", "enabled")


@dataclass
class AgentConfig:
    provider: str
    base_url: str
    api_key: str | None
    model: str
    max_tokens: int
    max_tool_calls: int
    deep_dive_limit: int
    cooldown_minutes: int
    credential_id: int | None
    push_verdict: bool
    enabled: bool


def _ensure_row(db):
    db.execute("INSERT OR IGNORE INTO agent_config (id) VALUES (1)")


def load_config() -> AgentConfig:
    db = get_db(settings.db_path)
    _ensure_row(db)
    db.commit()
    row = db.execute("SELECT * FROM agent_config WHERE id = 1").fetchone()
    db.close()
    key = decrypt_secret(row["api_key_enc"]) if row["api_key_enc"] else None
    return AgentConfig(provider=row["provider"], base_url=row["base_url"], api_key=key,
                       model=row["model"], max_tokens=row["max_tokens"],
                       max_tool_calls=row["max_tool_calls"], deep_dive_limit=row["deep_dive_limit"],
                       cooldown_minutes=row["cooldown_minutes"], credential_id=row["credential_id"],
                       push_verdict=bool(row["push_verdict"]), enabled=bool(row["enabled"]))


def save_config(data: dict) -> None:
    """data: FIELDS 子集 + 可选 api_key（None/缺省 = 不改动已存密钥）。
    credential_id 允许显式置 None（清除）。"""
    db = get_db(settings.db_path)
    _ensure_row(db)
    sets, params = [], []
    for f in FIELDS:
        if f not in data:
            continue
        if data[f] is None and f != "credential_id":
            continue
        sets.append(f"{f} = ?")
        v = data[f]
        params.append(int(v) if isinstance(v, bool) else v)
    if data.get("api_key"):
        sets.append("api_key_enc = ?")
        params.append(encrypt_secret(data["api_key"]))
    if sets:
        sets.append("updated_at = datetime('now')")
        db.execute(f"UPDATE agent_config SET {', '.join(sets)} WHERE id = 1", params)
    db.commit()
    db.close()
```

```python
# backend/api/agent.py
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional, Literal
from agent.config import load_config, save_config
from config import settings

router = APIRouter(prefix="/agent")


class AgentConfigBody(BaseModel):
    provider: Literal["openai", "anthropic"]
    base_url: str = ""
    api_key: Optional[str] = None          # None = 不改
    model: str
    max_tokens: int = Field(4096, ge=256, le=64000)
    max_tool_calls: int = Field(15, ge=1, le=50)
    deep_dive_limit: int = Field(5, ge=0, le=20)
    cooldown_minutes: int = Field(240, ge=0)
    credential_id: Optional[int] = None
    push_verdict: bool = False
    enabled: bool = False


def _public(cfg) -> dict:
    d = cfg.__dict__.copy()
    d["has_api_key"] = bool(d.pop("api_key"))
    d["insecure_defaults"] = settings.insecure_defaults()   # 前端据此显示警告
    return d


@router.get("/config")
def get_config():
    return _public(load_config())


@router.put("/config")
def put_config(body: AgentConfigBody):
    save_config(body.model_dump())
    return _public(load_config())
```

`api/__init__.py`：`from api.agent import router as agent_router` + `protected.include_router(agent_router)`——
**必须插在 `api_router.include_router(protected)`（第 29 行）之前**：FastAPI 在 include 时拷贝路由，追加在其后会静默不生效。

⚠️ 本任务使 SCHEMA 表数从 12 变 13：`tests/test_database.py` 的表数断言需 +1（Task 1 已 +3）。

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest tests/test_agent_config.py tests/test_agent_schema.py tests/test_database.py -v` → PASS

- [ ] **Step 5: Commit** `feat(agent): agent_config storage (Fernet key) + /api/agent/config`

### Task 9: 只读工具层（节流 + 预算）

**Files:**
- Create: `backend/agent/tools.py`
- Test: `backend/tests/test_agent_tools.py`

工具函数是纯 Python（PydanticAI 注册在 Task 12）。全部只读；`kline_summary` 是唯一逐 symbol 网络放大器，受全局节流（≥0.25s 间隔）+ 每 run 预算（`ToolBudget`）双重约束。**本模块禁止 import trading.service 的任何下单函数。**

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_tools.py
import time
from unittest.mock import patch
from agent.tools import ToolBudget, kline_summary, market_snapshot, signal_history


def _candles(n=60, base=100.0):
    from klines.models import Candle
    out = []
    for i in range(n):
        p = base + i * 0.1
        out.append(Candle(open_time=i * 3600_000, close_time=(i + 1) * 3600_000 - 1,
                          open=p, high=p + 1, low=p - 1, close=p + 0.5,
                          volume=1000.0, closed=True))
    return out


def test_kline_summary_shape_and_budget():
    budget = ToolBudget(deep_dive_limit=1)
    with patch("agent.tools.fetch_klines", return_value=_candles()):
        s = kline_summary("BTCUSDT", "1h", budget)
        s2 = kline_summary("ETHUSDT", "1h", budget)   # 预算耗尽（留在 patch 块内防真实网络调用）
    assert s["symbol"] == "BTCUSDT" and "atr" in s and "recent_patterns" in s
    assert "candles" not in s                     # 绝不返回原始蜡烛数组
    assert "error" in s2 and "budget" in s2["error"]


def test_kline_summary_throttles():
    import agent.tools as t
    budget = ToolBudget(deep_dive_limit=10)
    with patch("agent.tools.fetch_klines", return_value=_candles()):
        t0 = time.monotonic()
        kline_summary("BTCUSDT", "1h", budget)
        kline_summary("ETHUSDT", "1h", budget)
        assert time.monotonic() - t0 >= t._MIN_INTERVAL


def test_market_snapshot_filters_symbols():
    tickers = ([{"symbol": "BTCUSDT", "exchange": "Binance", "lastPrice": 1.0,
                 "priceChangePercent": 2.0, "volume24h": 9.9, "high24h": 0, "low24h": 0}], None)
    funding = ([{"symbol": "BTCUSDT", "exchange": "Binance", "fundingRate": 0.0001}], None)
    with patch("agent.tools.fetch_all_tickers", return_value=tickers), \
         patch("agent.tools.fetch_all_funding_rates", return_value=funding):
        snap = market_snapshot(["BTCUSDT", "MISSING"])
    assert snap["BTCUSDT"]["lastPrice"] == 1.0 and snap["BTCUSDT"]["fundingRate"] == 0.0001
    assert snap["MISSING"] == {"error": "no ticker data"}


def test_signal_history_win_rates(reset_db):
    import os
    from database import get_db
    db = get_db(os.environ["DB_PATH"])
    for chg in (5.0, -2.0, 3.0):
        cur = db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
                         "VALUES (NULL, 'BTCUSDT', 'Binance', '底背离', '1h')")
        db.execute("INSERT INTO outcomes (signal_id, change_4h) VALUES (?, ?)", (cur.lastrowid, chg))
    db.commit()
    db.close()
    h = signal_history("BTCUSDT", "底背离")
    assert h["count"] == 3 and abs(h["up_rate_4h"] - 2 / 3) < 1e-4
```

- [ ] **Step 2: 运行确认失败** — `cd backend && python -m pytest tests/test_agent_tools.py -v` → FAIL

- [ ] **Step 3: 实现 tools.py**

```python
# backend/agent/tools.py
"""Read-only tools for the agent loop. Every function returns a
JSON-serializable dict; errors are returned as {'error': ...} strings the
LLM can read (never raised). NO order-placing imports allowed here."""
import time
import threading
from dataclasses import dataclass
from statistics import mean, pstdev
from database import get_db
from config import settings
from klines.fetcher import fetch_klines
from klines.patterns import detect_patterns
from klines.classification import classify
from klines.structure import find_pivot, atr
from sources.exchanges import fetch_all_tickers, fetch_all_funding_rates

_MIN_INTERVAL = 0.25          # Binance fapi 无既有限流；工具层自守
_lock = threading.Lock()
_last_call = 0.0


def _throttle():
    global _last_call
    with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


@dataclass
class ToolBudget:
    deep_dive_limit: int = 5
    used: int = 0


def market_snapshot(symbols: list[str]) -> dict:
    """15s TTL 缓存的 ticker/funding 快照，键为 clean symbol。"""
    tickers, _ = fetch_all_tickers()
    funding, _ = fetch_all_funding_rates()
    tmap = {(t["symbol"], t["exchange"]): t for t in tickers}
    fmap = {(f["symbol"], f["exchange"]): f for f in funding}
    out = {}
    for sym in symbols:
        t = tmap.get((sym, "Binance"))
        if not t:
            out[sym] = {"error": "no ticker data"}
            continue
        f = fmap.get((sym, "Binance"))
        out[sym] = {"lastPrice": t["lastPrice"], "priceChangePercent": t["priceChangePercent"],
                    "volume24h": t["volume24h"],
                    "fundingRate": f["fundingRate"] if f else None}
    return out


def kline_summary(symbol: str, interval: str, budget: ToolBudget, n: int = 120) -> dict:
    """K线压缩摘要：形态 + 分类 + 双向 pivot + ATR + 近端统计。受预算与节流约束。"""
    if budget.used >= budget.deep_dive_limit:
        return {"error": f"deep-dive budget exhausted ({budget.deep_dive_limit} per run); "
                         "decide with the evidence you already have"}
    budget.used += 1
    _throttle()
    try:
        candles = fetch_klines(symbol, interval, limit=max(n, 60))
    except Exception as e:
        return {"error": f"kline fetch failed: {e}"}
    closed = [c for c in candles if c.closed]
    if len(closed) < 20:
        return {"error": f"insufficient closed candles: {len(closed)}"}
    last = closed[-1]
    a = atr(candles, period=14)
    piv_long = find_pivot(candles, "long", last.close)
    piv_short = find_pivot(candles, "short", last.close)
    patterns = [{"name_zh": p.name_zh, "direction": p.direction, "category": p.category}
                for p in detect_patterns(candles)[-5:]]
    vols = [c.volume for c in closed[-30:]]
    vol_sigma = pstdev(vols) if len(vols) > 1 else 0.0
    recent = closed[-n:] if len(closed) >= n else closed
    return {
        "symbol": symbol, "interval": interval,
        "last_close": last.close,
        "atr": a, "atr_pct": round(a / last.close * 100, 3) if a else None,
        "pivot_below": piv_long.to_dict() if piv_long else None,
        "pivot_above": piv_short.to_dict() if piv_short else None,
        "last_closed_classification": classify(last).to_dict(),
        "recent_patterns": patterns,   # 方向标签为启发式（趋势消歧），非既定事实
        "recent_stats": {
            "bars": len(recent),
            "change_pct": round((last.close - recent[0].open) / recent[0].open * 100, 3),
            "high": max(c.high for c in recent), "low": min(c.low for c in recent),
            "volume_z_last": round((last.volume - mean(vols)) / vol_sigma, 2) if vol_sigma else 0.0,
        },
    }


def signal_history(symbol: str, indicator: str, limit: int = 30) -> dict:
    """同 symbol×indicator 历史信号的 1h/4h/24h 上涨占比（方向盲的原始收益，
    LLM 须结合信号语义解读）。indicator 为纯 label（Phase 0 统一编码）；
    LIKE 子句兼容迁移前的 'label(res)' 双编码旧数据（spec Phase 0.4）。"""
    db = get_db(settings.db_path)
    rows = db.execute(
        """SELECT o.change_1h, o.change_4h, o.change_24h
           FROM signals s LEFT JOIN outcomes o ON o.signal_id = s.id
           WHERE s.symbol = ? AND (s.indicator = ? OR s.indicator LIKE ? || '(%')
           ORDER BY s.triggered_at DESC LIMIT ?""",
        (symbol, indicator, indicator, limit)).fetchall()
    db.close()
    out = {"symbol": symbol, "indicator": indicator, "count": len(rows)}
    for h in ("1h", "4h", "24h"):
        vals = [r[f"change_{h}"] for r in rows if r[f"change_{h}"] is not None]
        out[f"tracked_{h}"] = len(vals)
        out[f"up_rate_{h}"] = round(sum(1 for v in vals if v > 0) / len(vals), 4) if vals else None
        out[f"avg_change_{h}"] = round(mean(vals), 4) if vals else None
    return out


def position_plan_preview(symbol: str, interval: str, direction: str, credential_id: int) -> dict:
    """只读仓位规划预览（需要专用凭据拉 equity/exchangeInfo，Places NO order）。
    仅当 agent_config.credential_id 已配置时注册为 agent 工具。"""
    _throttle()
    try:
        from trading.service import build_position_plan   # 只读函数；下单函数禁止 import
        return build_position_plan(credential_id=credential_id, symbol=symbol,
                                   interval=interval, direction=direction, order_type="MARKET")
    except Exception as e:
        return {"error": f"plan failed: {e}"}
```

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest tests/test_agent_tools.py -v` → PASS

- [ ] **Step 5: Commit** `feat(agent): read-only tool layer with throttle and per-run budget`

### Task 10: LLM 模型工厂 + prompt 常量

**Files:**
- Create: `backend/agent/llm.py`、`backend/agent/prompts.py`
- Test: `backend/tests/test_agent_llm.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_llm.py
import pytest
from agent.config import AgentConfig


def _cfg(**kw):
    base = dict(provider="openai", base_url="https://gw.example.com/v1", api_key="k",
                model="gpt-5", max_tokens=4096, max_tool_calls=15, deep_dive_limit=5,
                cooldown_minutes=240, credential_id=None, push_verdict=False, enabled=True)
    base.update(kw)
    return AgentConfig(**base)


def test_openai_model_uses_base_url():
    from agent.llm import build_model
    m = build_model(_cfg())
    assert m.model_name == "gpt-5"


def test_anthropic_model():
    from agent.llm import build_model
    m = build_model(_cfg(provider="anthropic", base_url="", model="claude-sonnet-4-6"))
    assert "claude" in m.model_name


def test_missing_key_raises():
    from agent.llm import build_model
    with pytest.raises(ValueError):
        build_model(_cfg(api_key=None))


def test_prompt_version_exists():
    from agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
    assert PROMPT_VERSION and "纯技术分析" in SYSTEM_PROMPT
```

- [ ] **Step 2: 运行确认失败** — `cd backend && python -m pytest tests/test_agent_llm.py -v` → FAIL

- [ ] **Step 3: 实现**

```python
# backend/agent/llm.py
"""Provider factory. v1.x PydanticAI API — 若 import 失败见 Task 7 的版本说明。"""
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider


def build_model(cfg):
    if not cfg.api_key:
        raise ValueError("agent LLM api_key 未配置")
    if cfg.provider == "anthropic":
        return AnthropicModel(cfg.model, provider=AnthropicProvider(api_key=cfg.api_key))
    kwargs = {"api_key": cfg.api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return OpenAIChatModel(cfg.model, provider=OpenAIProvider(**kwargs))
```

```python
# backend/agent/prompts.py
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """你是加密永续合约的技术分析决策员，对一次筛选任务产出的候选信号逐个裁决。

硬约束：
- 纯技术分析：只依据价格、成交量、衍生指标与市场结构；不引入任何消息面/情绪/链上判断。
- 你只做研究裁决（direction/confidence/理由），不下单、不算仓位——执行与风险由确定性系统负责。
- 工具预算有限（深评有配额）；先用批内证据（重叠计数、跨周期共振、快照）粗排，只对最值得的候选调 kline_summary 深评。
- 筛选结果为空可能是"无信号"也可能是"数据源失败"，不要过度解读空集。
- K线形态的方向标签是启发式，不是既定事实。

对每个候选输出：direction（long/short/skip）、confidence（0-1，校准而非乐观）、reasons（中文，简洁、可复核）、factors（可选的关键数值证据）。没有把握就 skip——skip 是合法且常见的正确答案。"""


def render_batch(context: dict) -> str:
    """把 context_json（SignalBatch 快照）紧凑渲染为用户消息。"""
    lines = [f"任务 #{context['task_id']}（{context['task_type']}）本轮筛选批："]
    for r in context["results"]:
        note = "（空集：无信号或数据源失败）" if not r["symbols"] else ""
        lines.append(f"- {r['label']} @{r['resolution']}: {len(r['symbols'])} 命中{note}")
    lines.append("\n候选信号（已通过规则阈值并落库，请逐个裁决；其余符号仅作背景）：")
    for c in context["candidates"]:
        snap = c.get("snapshot") or {}
        lines.append(f"- {c['symbol']} @{c['timeframe']} ← {'、'.join(c['labels'])}"
                     f"｜24h涨跌 {snap.get('priceChangePercent', '?')}%"
                     f"｜资金费率 {snap.get('fundingRate', '?')}")
    cross = context.get("cross") or {}
    if cross.get("resolution_overlap"):
        lines.append(f"\n跨周期共振: {cross['resolution_overlap']}")
    if cross.get("full_overlap"):
        lines.append(f"全交集: {cross['full_overlap']}")
    if context.get("bias_map"):
        lines.append(f"筛选器方向语义: {context['bias_map']}")
    return "\n".join(lines)
```

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest tests/test_agent_llm.py -v` → PASS

- [ ] **Step 5: Commit** `feat(agent): LLM provider factory + decision prompts v1`

---

## Phase 2 — AgentDecider worker

### Task 11: DB 队列（入队/领取/完成）

**Files:**
- Create: `backend/agent/queue.py`
- Test: `backend/tests/test_agent_queue.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_queue.py
import json
from agent.queue import enqueue_run, claim_next, finish_run, fail_run, get_run


def test_enqueue_claim_finish_cycle():
    rid = enqueue_run(task_id=None, context={"task_id": 1, "candidates": []})
    row = claim_next()
    assert row["id"] == rid and row["status"] == "running"
    assert json.loads(row["context_json"])["task_id"] == 1
    assert claim_next() is None                      # 队列已空
    finish_run(rid, model="m", prompt_version="v1", trace={"steps": []},
               input_tokens=10, output_tokens=5)
    assert get_run(rid)["status"] == "done"


def test_fail_run_records_error():
    rid = enqueue_run(task_id=None, context={})
    claim_next()
    fail_run(rid, "boom")
    row = get_run(rid)
    assert row["status"] == "failed" and row["error"] == "boom"
```

- [ ] **Step 2: 运行确认失败** — `cd backend && python -m pytest tests/test_agent_queue.py -v` → FAIL

- [ ] **Step 3: 实现 queue.py**

```python
# backend/agent/queue.py
"""agent_runs doubles as the work queue (single worker, restart-safe)."""
import json
from database import get_db
from config import settings


def enqueue_run(task_id, context: dict) -> int:
    db = get_db(settings.db_path)
    cur = db.execute(
        "INSERT INTO agent_runs (task_id, decider, status, context_json) VALUES (?, 'agent', 'queued', ?)",
        (task_id, json.dumps(context, ensure_ascii=False)))
    db.commit()
    db.close()
    return cur.lastrowid


def claim_next():
    """原子领取最早的 queued run；无则 None。（sqlite3 ≥3.35 支持 RETURNING）"""
    db = get_db(settings.db_path)
    row = db.execute(
        """UPDATE agent_runs SET status = 'running', started_at = datetime('now')
           WHERE id = (SELECT id FROM agent_runs WHERE status = 'queued' ORDER BY id LIMIT 1)
           RETURNING *""").fetchone()
    db.commit()
    db.close()
    return row


def finish_run(run_id, *, model, prompt_version, trace, input_tokens, output_tokens):
    db = get_db(settings.db_path)
    db.execute(
        """UPDATE agent_runs SET status='done', finished_at=datetime('now'), model=?,
           prompt_version=?, trace_json=?, input_tokens=?, output_tokens=? WHERE id=?""",
        (model, prompt_version, json.dumps(trace, ensure_ascii=False)[:200000],
         input_tokens, output_tokens, run_id))
    db.commit()
    db.close()


def fail_run(run_id, error: str):
    db = get_db(settings.db_path)
    db.execute("UPDATE agent_runs SET status='failed', finished_at=datetime('now'), error=? WHERE id=?",
               (str(error)[:2000], run_id))
    db.commit()
    db.close()


def get_run(run_id):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    db.close()
    return row
```

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest tests/test_agent_queue.py -v` → PASS

- [ ] **Step 5: Commit** `feat(agent): DB-backed run queue`

### Task 12: AgentDecider（PydanticAI agent + 冷却复用 + trace）

**Files:**
- Create: `backend/agent/agent_decider.py`
- Test: `backend/tests/test_agent_decider.py`

- [ ] **Step 1: 写失败测试（TestModel 无网络）**

```python
# backend/tests/test_agent_decider.py
import os, json
from database import get_db
from agent.config import AgentConfig


def _cfg(**kw):
    base = dict(provider="openai", base_url="", api_key="k", model="test",
                max_tokens=4096, max_tool_calls=15, deep_dive_limit=5,
                cooldown_minutes=240, credential_id=None, push_verdict=False, enabled=True)
    base.update(kw)
    return AgentConfig(**base)


def _seed_signal():
    """FK 开启：candidates 里的 signal_ids 必须指向真实 signals 行。"""
    db = get_db(os.environ["DB_PATH"])
    cur = db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
                     "VALUES (NULL, 'BTCUSDT', 'Binance', '底背离', '1h')")
    db.commit()
    sid = cur.lastrowid
    db.close()
    return sid


def _context(sid):
    return {
        "task_id": None, "task_type": "watchlist_signal",
        "results": [{"label": "底背离", "resolution": "1h", "symbols": ["BINANCE:BTCUSDT.P"]}],
        "candidates": [{"symbol": "BTCUSDT", "timeframe": "1h", "labels": ["底背离"],
                        "signal_ids": [sid], "snapshot": {"priceChangePercent": 1.0}}],
        "cross": {}, "bias_map": {"底背离": "long"},
    }


def test_run_agent_decision_persists(reset_db):
    # call_tools=[] 避免 TestModel 默认调用全部工具触发真实网络请求
    from pydantic_ai.models.test import TestModel
    from agent.agent_decider import run_agent_on_context
    from agent.queue import enqueue_run, claim_next
    ctx = _context(_seed_signal())
    rid = enqueue_run(None, ctx)
    row = claim_next()
    result = run_agent_on_context(rid, json.loads(row["context_json"]), _cfg(),
                                  model_override=TestModel(call_tools=[]))
    assert result["decisions"] >= 0      # TestModel 生成合法但任意的结构化输出
    db = get_db(os.environ["DB_PATH"])
    run = db.execute("SELECT status, prompt_version FROM agent_runs WHERE id=?", (rid,)).fetchone()
    db.close()
    assert run["status"] == "done" and run["prompt_version"] == "v1"


def test_valid_verdict_links_signal_ids(reset_db):
    """FunctionModel 构造精确输出：symbol/timeframe 命中候选 → 落库并关联 signal_ids。"""
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from agent.agent_decider import run_agent_on_context
    from agent.queue import enqueue_run, claim_next

    def make_output(messages, info):
        # 直接调用 final_result 工具产出结构化输出（v1.x FunctionModel 惯例）
        return ModelResponse(parts=[ToolCallPart(
            tool_name="final_result",
            args={"verdicts": [
                {"symbol": "BTCUSDT", "timeframe": "1h", "direction": "long",
                 "confidence": 0.8, "reasons": "测试", "factors": None},
                {"symbol": "EVILUSDT", "timeframe": "1h", "direction": "short",
                 "confidence": 0.9, "reasons": "批外符号应被丢弃", "factors": None},
            ]})])

    sid = _seed_signal()
    ctx = _context(sid)
    rid = enqueue_run(None, ctx)
    claim_next()
    out = run_agent_on_context(rid, ctx, _cfg(), model_override=FunctionModel(make_output))
    assert out["decisions"] == 1
    db = get_db(os.environ["DB_PATH"])
    ds = db.execute("SELECT symbol, direction, signal_ids_json FROM agent_decisions WHERE run_id=?",
                    (rid,)).fetchall()
    db.close()
    assert len(ds) == 1 and ds[0]["symbol"] == "BTCUSDT"
    assert json.loads(ds[0]["signal_ids_json"]) == [sid]


def test_cooldown_reuses_decision(reset_db):
    from pydantic_ai.models.test import TestModel
    from agent.agent_decider import run_agent_on_context
    from agent.queue import enqueue_run, claim_next
    # 先手工插入一条冷却窗口内的 agent 裁决
    db = get_db(os.environ["DB_PATH"])
    cur = db.execute("INSERT INTO agent_runs (decider, status) VALUES ('agent', 'done')")
    db.execute("INSERT INTO agent_decisions (run_id, symbol, timeframe, direction, confidence, reasons) "
               "VALUES (?, 'BTCUSDT', '1h', 'long', 0.7, 'prior')", (cur.lastrowid,))
    db.commit()
    db.close()
    ctx = _context(_seed_signal())
    rid = enqueue_run(None, ctx)
    claim_next()
    out = run_agent_on_context(rid, ctx, _cfg(), model_override=TestModel(call_tools=[]))
    assert out["reused"] == 1 and out["decisions"] == 0     # 全部命中冷却，不再调 LLM
    db = get_db(os.environ["DB_PATH"])
    trace = json.loads(db.execute("SELECT trace_json FROM agent_runs WHERE id=?", (rid,)).fetchone()["trace_json"])
    db.close()
    assert trace["reused"]                                   # 记录了复用的 decision id
```

- [ ] **Step 2: 运行确认失败** — `cd backend && python -m pytest tests/test_agent_decider.py -v` → FAIL
（FunctionModel/ToolCallPart 的确切 import 与构造按已安装版本文档微调，断言不变。）

- [ ] **Step 3: 实现 agent_decider.py**

```python
# backend/agent/agent_decider.py
"""AgentDecider: consumes a queued run's context, drives the PydanticAI
tool loop, persists per-signal verdicts. Called by the worker thread only.
本模块及其 import 链禁止出现任何下单函数（红线）。"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from database import get_db
from config import settings
from agent import tools as T
from agent.llm import build_model
from agent.prompts import SYSTEM_PROMPT, PROMPT_VERSION, render_batch
from agent.queue import finish_run, fail_run


class VerdictOut(BaseModel):
    symbol: str
    timeframe: str
    direction: Literal["long", "short", "skip"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: str
    factors: Optional[dict] = None


class DecisionSetOut(BaseModel):
    verdicts: list[VerdictOut]


@dataclass
class Deps:
    budget: T.ToolBudget
    credential_id: Optional[int]
    trace: list = field(default_factory=list)


def _build_agent(cfg, model):
    agent = Agent(model, output_type=DecisionSetOut, system_prompt=SYSTEM_PROMPT, deps_type=Deps)

    @agent.tool
    def get_market_snapshot(ctx: RunContext[Deps], symbols: list[str]) -> dict:
        """获取给定 symbol 列表（clean 格式，如 BTCUSDT）的实时行情快照：价格/24h 涨跌/成交额/资金费率。"""
        out = T.market_snapshot(symbols)
        ctx.deps.trace.append({"tool": "market_snapshot", "args": symbols})
        return out

    @agent.tool
    def get_kline_summary(ctx: RunContext[Deps], symbol: str, interval: str) -> dict:
        """深评一个候选：K线结构摘要（形态/分类/上下方枢轴/ATR/近端统计）。每轮有配额，省着用。"""
        out = T.kline_summary(symbol, interval, ctx.deps.budget)
        ctx.deps.trace.append({"tool": "kline_summary", "args": [symbol, interval],
                               "result": json.dumps(out, ensure_ascii=False)[:2000]})
        return out

    @agent.tool
    def get_signal_history(ctx: RunContext[Deps], symbol: str, indicator: str) -> dict:
        """查询该 symbol×指标 的历史信号 1h/4h/24h 上涨占比与均值（方向盲原始收益）。"""
        out = T.signal_history(symbol, indicator)
        ctx.deps.trace.append({"tool": "signal_history", "args": [symbol, indicator],
                               "result": json.dumps(out, ensure_ascii=False)[:2000]})
        return out

    if cfg.credential_id:
        @agent.tool
        def get_position_plan(ctx: RunContext[Deps], symbol: str, interval: str,
                              direction: Literal["long", "short"]) -> dict:
            """只读仓位规划预览（结构止损/RR/可行性）。不会下单。"""
            out = T.position_plan_preview(symbol, interval, direction, ctx.deps.credential_id)
            ctx.deps.trace.append({"tool": "position_plan", "args": [symbol, interval, direction],
                                   "result": json.dumps(out, ensure_ascii=False)[:2000]})
            return out

    return agent


def _recent_decisions(symbols_tf, cooldown_minutes):
    """{(symbol, timeframe): decision_id} —— 冷却窗口内已有的 agent 裁决。"""
    if not symbols_tf or not cooldown_minutes:
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    db = get_db(settings.db_path)
    rows = db.execute(
        """SELECT d.id, d.symbol, d.timeframe FROM agent_decisions d
           JOIN agent_runs r ON r.id = d.run_id
           WHERE r.decider = 'agent' AND d.created_at >= ?""", (cutoff,)).fetchall()
    db.close()
    have = {(r["symbol"], r["timeframe"]): r["id"] for r in rows}
    return {k: have[k] for k in symbols_tf if k in have}


def run_agent_on_context(run_id, context, cfg, model_override=None) -> dict:
    try:
        candidates = context.get("candidates", [])
        reused = _recent_decisions({(c["symbol"], c["timeframe"]) for c in candidates},
                                   cfg.cooldown_minutes)
        fresh = [c for c in candidates if (c["symbol"], c["timeframe"]) not in reused]
        trace = {"reused": list(reused.values()), "steps": []}
        n_written = in_tok = out_tok = 0

        if fresh:
            model = model_override or build_model(cfg)
            agent = _build_agent(cfg, model)
            deps = Deps(budget=T.ToolBudget(deep_dive_limit=cfg.deep_dive_limit),
                        credential_id=cfg.credential_id, trace=trace["steps"])
            prompt = render_batch({**context, "candidates": fresh})
            result = agent.run_sync(prompt, deps=deps,
                                    usage_limits=UsageLimits(request_limit=cfg.max_tool_calls))
            usage = result.usage()
            in_tok = getattr(usage, "input_tokens", None) or getattr(usage, "request_tokens", 0) or 0
            out_tok = getattr(usage, "output_tokens", None) or getattr(usage, "response_tokens", 0) or 0
            known = {(c["symbol"], c["timeframe"]): c for c in fresh}
            db = get_db(settings.db_path)
            for v in result.output.verdicts:
                c = known.get((v.symbol, v.timeframe))
                if not c:
                    trace["steps"].append({"dropped": f"unknown candidate {v.symbol}@{v.timeframe}"})
                    continue
                ids = c.get("signal_ids", [])
                db.execute(
                    """INSERT INTO agent_decisions (run_id, signal_id, signal_ids_json, symbol,
                       timeframe, direction, confidence, reasons, factors_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, ids[0] if ids else None, json.dumps(ids), v.symbol, v.timeframe,
                     v.direction, v.confidence, v.reasons,
                     json.dumps(v.factors, ensure_ascii=False) if v.factors else None))
                n_written += 1
            db.commit()
            db.close()

        finish_run(run_id, model="test" if model_override else cfg.model,
                   prompt_version=PROMPT_VERSION, trace=trace,
                   input_tokens=in_tok, output_tokens=out_tok)
        return {"decisions": n_written, "reused": len(reused)}
    except Exception as e:
        fail_run(run_id, repr(e))
        raise
```

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest tests/test_agent_decider.py -v` → PASS

- [ ] **Step 5: Commit** `feat(agent): AgentDecider — tool loop, cooldown reuse, full trace`

### Task 13: worker 线程 + lifespan

**Files:**
- Create: `backend/agent/worker.py`
- Modify: `backend/main.py`（lifespan）
- Test: `backend/tests/test_agent_worker.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_worker.py
import time
from unittest.mock import patch


def test_worker_processes_queued_run(reset_db):
    from agent.queue import enqueue_run, get_run
    from agent import worker
    rid = enqueue_run(None, {"candidates": []})
    calls = []

    def fake_process(run_row):
        calls.append(run_row["id"])
        from agent.queue import finish_run
        finish_run(run_row["id"], model="x", prompt_version="v1", trace={},
                   input_tokens=0, output_tokens=0)

    with patch.object(worker, "_process_run", side_effect=fake_process):
        worker.start_worker(interval=0.05)
        for _ in range(100):
            if get_run(rid)["status"] == "done":
                break
            time.sleep(0.05)
        worker.stop_worker()
    assert calls == [rid]


def test_worker_marks_failed_on_crash(reset_db):
    from agent.queue import enqueue_run, get_run
    from agent import worker
    rid = enqueue_run(None, {"candidates": []})
    with patch.object(worker, "_process_run", side_effect=RuntimeError("boom")):
        worker.start_worker(interval=0.05)
        for _ in range(100):
            if get_run(rid)["status"] == "failed":
                break
            time.sleep(0.05)
        worker.stop_worker()
    assert get_run(rid)["status"] == "failed"
```

- [ ] **Step 2: 运行确认失败** — FAIL（模块不存在）

- [ ] **Step 3: 实现 worker.py 并接线 lifespan**

```python
# backend/agent/worker.py
"""Agent worker: single daemon thread draining agent_runs. Never runs in
the APScheduler pool; own short-lived DB connections; clean shutdown."""
import json
import sys
import threading
from app_logger import log as applog
from agent.queue import claim_next, fail_run

_stop = threading.Event()
_thread = None


def _process_run(run_row):
    from agent.config import load_config
    from agent.agent_decider import run_agent_on_context
    cfg = load_config()
    if not cfg.enabled or not cfg.api_key:
        fail_run(run_row["id"], "agent disabled or api_key missing")
        return
    context = json.loads(run_row["context_json"] or "{}")
    out = run_agent_on_context(run_row["id"], context, cfg)
    applog("agent", "info",
           f"run #{run_row['id']}: {out['decisions']} decisions, {out['reused']} reused")
    _maybe_push_verdict(run_row, cfg)


def _maybe_push_verdict(run_row, cfg):
    """可选跟随消息：非 skip 裁决摘要。经 sender 抽象（channel 无关）+ HTML 转义。失败仅记日志。"""
    if not cfg.push_verdict or not run_row["task_id"]:
        return
    try:
        import html
        from database import get_db
        from config import settings
        from channels.sender import send_text
        db = get_db(settings.db_path)
        task = db.execute(
            """SELECT c.type, c.config_json FROM tasks t JOIN channels c ON c.id = t.channel_id
               WHERE t.id = ?""", (run_row["task_id"],)).fetchone()
        ds = db.execute(
            "SELECT symbol, timeframe, direction, confidence FROM agent_decisions "
            "WHERE run_id = ? AND direction != 'skip'", (run_row["id"],)).fetchall()
        db.close()
        if not task or not ds:
            return
        arrow = {"long": "📈 做多", "short": "📉 做空"}
        lines = [f"🤖 Agent 裁决（run #{run_row['id']}）:"]
        for d in ds:
            lines.append(html.escape(
                f"  {d['symbol']} @{d['timeframe']} → {arrow[d['direction']]}"
                f"（置信 {d['confidence']:.2f}）"))
        send_text(task["type"], json.loads(task["config_json"]), "\n".join(lines))
    except Exception as e:
        applog("agent", "warn", f"verdict push failed: {e}")


def _loop(interval):
    mod = sys.modules[__name__]
    while not _stop.wait(interval):
        try:
            row = claim_next()
            if row is None:
                continue
            try:
                mod._process_run(row)     # 模块属性调用，测试可 patch
            except Exception as e:
                applog("agent", "error", f"run #{row['id']} crashed: {e}")
                fail_run(row["id"], repr(e))
        except Exception as e:
            applog("agent", "error", f"worker loop: {e}")


def start_worker(interval=2.0):
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), daemon=True, name="agent-worker")
    _thread.start()


def stop_worker():
    _stop.set()
    if _thread:
        _thread.join(timeout=10)
```

lifespan（main.py）：`start_poller()` 后加 `from agent.worker import start_worker, stop_worker; start_worker()`；`yield` 后顺序为 `stop_worker()` → `stop_poller()` → `stop_scheduler()`。

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest tests/test_agent_worker.py -v` → PASS

- [ ] **Step 5: Commit** `feat(agent): worker thread drains run queue, optional verdict push`

### Task 14: executor 入队钩子（agent_decide action）

**Files:**
- Modify: `backend/tasks/executor.py`（新增 `_enqueue_agent_run`；两个 handler 在 `record_rule_run` 后调用）
- Test: `backend/tests/test_agent_enqueue.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_enqueue.py
import os, json
from unittest.mock import patch
from database import get_db
from agent.decider import SignalBatch, Decision


def _fixture():
    # task_id 用 None：FK 开启时非空 task_id 必须存在于 tasks 表，
    # 而 _enqueue_agent_run 的 try/except 会吞掉 IntegrityError，导致断言神秘失败
    batch = SignalBatch(task_id=None, task_type="watchlist_signal",
                        config={"resolutions": ["1h"]},
                        results=[{"label": "底背离", "resolution": "1h",
                                  "symbols": ["BINANCE:BTCUSDT.P"], "count": 1}],
                        bias_map={"底背离": "long"})
    decisions = [Decision(symbol="BINANCE:BTCUSDT.P", timeframe="1h", direction="long",
                          confidence=None, reasons="", labels=["底背离"])]
    return batch, decisions, {("BTCUSDT", "1h"): [42]}


def _count():
    db = get_db(os.environ["DB_PATH"])
    n = db.execute("SELECT COUNT(*) FROM agent_runs WHERE decider='agent'").fetchone()[0]
    db.close()
    return n


SNAP = {"BTCUSDT": {"lastPrice": 1.0, "priceChangePercent": 2.0, "volume24h": 1.0, "fundingRate": None}}


def test_enqueue_respects_enabled_and_action(reset_db):
    from tasks.executor import _enqueue_agent_run
    from agent.config import save_config
    batch, decisions, id_map = _fixture()

    with patch("agent.tools.market_snapshot", return_value=SNAP):
        # agent 未启用：不入队
        _enqueue_agent_run(None, batch, decisions, id_map, actions=["agent_decide"])
        assert _count() == 0

        save_config({"provider": "openai", "model": "m", "api_key": "k", "enabled": True,
                     "base_url": "", "max_tokens": 4096, "max_tool_calls": 15,
                     "deep_dive_limit": 5, "cooldown_minutes": 240, "push_verdict": False,
                     "credential_id": None})
        # action 不含 agent_decide：不入队
        _enqueue_agent_run(None, batch, decisions, id_map, actions=["text_summary"])
        assert _count() == 0
        # 启用 + action 命中：入队且 context 完整
        _enqueue_agent_run(None, batch, decisions, id_map, actions=["agent_decide"])
        assert _count() == 1

    db = get_db(os.environ["DB_PATH"])
    row = db.execute("SELECT * FROM agent_runs WHERE decider='agent'").fetchone()
    db.close()
    ctx = json.loads(row["context_json"])
    assert ctx["candidates"][0]["symbol"] == "BTCUSDT"
    assert ctx["candidates"][0]["signal_ids"] == [42]
    assert ctx["candidates"][0]["snapshot"]["priceChangePercent"] == 2.0
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 实现 `_enqueue_agent_run` 并在两个 handler 调用**

```python
def _enqueue_agent_run(task_id, batch, decisions, signal_id_map, actions):
    """actions 含 agent_decide 且 agent 启用时，把批上下文入队给 worker。
    快照在入队时采集（15s TTL 缓存，代价极低）。失败不影响主流程。"""
    if "agent_decide" not in actions:
        return
    try:
        from agent.config import load_config
        from agent.queue import enqueue_run
        from agent import tools as agent_tools
        cfg = load_config()
        if not cfg.enabled:
            return
        candidates = []
        for d in decisions:
            clean = d.symbol.replace("BINANCE:", "").replace(".P", "")
            candidates.append({"symbol": clean, "timeframe": d.timeframe, "labels": d.labels,
                               "signal_ids": signal_id_map.get((clean, d.timeframe), [])})
        if not candidates:
            return
        snaps = agent_tools.market_snapshot(sorted({c["symbol"] for c in candidates}))
        for c in candidates:
            c["snapshot"] = snaps.get(c["symbol"])
        context = {"task_id": task_id, "task_type": batch.task_type,
                   "results": [{"label": r["label"], "resolution": r["resolution"],
                                "symbols": r["symbols"][:50]} for r in batch.results],
                   "cross": batch.cross, "bias_map": batch.bias_map,
                   "candidates": candidates}
        enqueue_run(task_id, context)
    except Exception as e:
        applog("agent", "warn", f"agent enqueue failed: {e}")
```

调用点：两个 handler 在 `record_rule_run(...)` 之后追加
`_enqueue_agent_run(task_id, batch, rule_out.decisions, signal_ids, actions)`。
market_scan 路径需先把 `build_cross_analysis` 结果写回 `batch.cross`（RuleDecider._market_scan 内计算后 `batch.cross = analysis`，或 executor 侧回填——取一种并加注释）。
注意测试 patch 的是 `agent.tools.market_snapshot`，因此 executor 内 import 方式必须是 `from agent import tools as agent_tools` 再 `agent_tools.market_snapshot(...)`（如上）。

前端 Tasks.vue 的 actions 复选项追加 `{value: 'agent_decide', label: 'Agent 裁决'}`（随 Task 17 前端提交亦可）。

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest -m "not network" -q` → PASS

- [ ] **Step 5: Commit** `feat(agent): executor enqueues agent runs for agent_decide tasks`

### Task 15: runs/decisions API

**Files:**
- Modify: `backend/api/agent.py`
- Test: `backend/tests/test_agent_api.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_api.py
import os, pytest
from database import get_db


def _seed():
    db = get_db(os.environ["DB_PATH"])
    cur = db.execute("INSERT INTO agent_runs (task_id, decider, status, context_json, trace_json) "
                     "VALUES (NULL, 'agent', 'done', '{}', '{\"steps\":[]}')")
    rid = cur.lastrowid
    db.execute("INSERT INTO agent_decisions (run_id, symbol, timeframe, direction, confidence, reasons) "
               "VALUES (?, 'BTCUSDT', '1h', 'long', 0.7, 'r')", (rid,))
    db.commit()
    did = db.execute("SELECT id FROM agent_decisions WHERE run_id=?", (rid,)).fetchone()["id"]
    db.close()
    return rid, did


@pytest.mark.asyncio
async def test_list_and_detail(client):
    rid, _ = _seed()
    async with client as c:
        lst = (await c.get("/api/agent/runs")).json()
        det = (await c.get(f"/api/agent/runs/{rid}")).json()
    assert lst[0]["id"] == rid and lst[0]["decision_count"] == 1
    assert det["decisions"][0]["symbol"] == "BTCUSDT"
    assert det["trace"]["steps"] == []


@pytest.mark.asyncio
async def test_rate_decision(client):
    _, did = _seed()
    async with client as c:
        r = await c.post(f"/api/agent/decisions/{did}/rate", json={"rating": 1})
    assert r.status_code == 200
    db = get_db(os.environ["DB_PATH"])
    assert db.execute("SELECT human_rating FROM agent_decisions WHERE id=?", (did,)).fetchone()[0] == 1
    db.close()


@pytest.mark.asyncio
async def test_rerun_requeues(client):
    rid, _ = _seed()
    async with client as c:
        r = await c.post(f"/api/agent/runs/{rid}/rerun")
    new_id = r.json()["id"]
    db = get_db(os.environ["DB_PATH"])
    assert db.execute("SELECT status FROM agent_runs WHERE id=?", (new_id,)).fetchone()[0] == "queued"
    db.close()
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 在 api/agent.py 追加端点**

```python
import json
from fastapi import HTTPException
from database import get_db


@router.get("/runs")
def list_runs(limit: int = 50):
    db = get_db(settings.db_path)
    rows = db.execute(
        """SELECT r.id, r.task_id, r.decider, r.status, r.model, r.prompt_version,
                  r.input_tokens, r.output_tokens, r.error, r.created_at, r.finished_at,
                  t.name AS task_name,
                  (SELECT COUNT(*) FROM agent_decisions d WHERE d.run_id = r.id) AS decision_count
           FROM agent_runs r LEFT JOIN tasks t ON t.id = r.task_id
           WHERE r.decider = 'agent'
           ORDER BY r.id DESC LIMIT ?""", (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


@router.get("/runs/{run_id}")
def run_detail(run_id: int):
    db = get_db(settings.db_path)
    run = db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    if not run:
        db.close()
        raise HTTPException(404, "run not found")
    ds = db.execute(
        """SELECT d.*, o.change_1h, o.change_4h, o.change_24h
           FROM agent_decisions d LEFT JOIN outcomes o ON o.signal_id = d.signal_id
           WHERE d.run_id = ? ORDER BY d.id""", (run_id,)).fetchall()
    db.close()
    out = dict(run)
    out["trace"] = json.loads(run["trace_json"]) if run["trace_json"] else None
    out["context"] = json.loads(run["context_json"]) if run["context_json"] else None
    out.pop("trace_json")
    out.pop("context_json")
    out["decisions"] = [dict(d) for d in ds]
    return out


@router.post("/runs/{run_id}/rerun")
def rerun(run_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT task_id, context_json FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "run not found")
    from agent.queue import enqueue_run
    new_id = enqueue_run(row["task_id"], json.loads(row["context_json"] or "{}"))
    return {"id": new_id}


class RateBody(BaseModel):
    rating: Literal[-1, 0, 1]


@router.post("/decisions/{decision_id}/rate")
def rate_decision(decision_id: int, body: RateBody):
    db = get_db(settings.db_path)
    cur = db.execute("UPDATE agent_decisions SET human_rating = ? WHERE id = ?",
                     (body.rating, decision_id))
    db.commit()
    db.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "decision not found")
    return {"ok": True}
```

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest tests/test_agent_api.py -v` → PASS

- [ ] **Step 5: Commit** `feat(agent): runs/decisions/rerun/rate API`

---

## Phase 3 — 复盘页

前端无测试框架（package.json 仅 vue/vue-router/lightweight-charts），本阶段验证方式为 `cd frontend && npm run build` 通过 + 手动走查。样式必须用既有 CSS 变量（--accent/--success/--danger/--bg-*），文案中文，localStorage 键 `wohub-*`。

### Task 16: api client 方法 + 路由 + 导航

**Files:**
- Modify: `frontend/src/api/client.js`（api 对象末尾追加方法）
- Modify: `frontend/src/router/index.js`（routes 数组）
- Modify: `frontend/src/App.vue`（navItems 数组）
- Create: `frontend/src/views/Agent.vue`（本任务先放最小占位，Task 17 填充）

- [ ] **Step 1: client.js 追加方法（跟随既有一行一方法风格）**

```javascript
  // --- agent ---
  getAgentConfig: () => request('/agent/config'),
  updateAgentConfig: (data) => request('/agent/config', { method: 'PUT', body: JSON.stringify(data) }),
  listAgentRuns: (limit = 50) => request(`/agent/runs?limit=${limit}`),
  getAgentRun: (id) => request(`/agent/runs/${id}`),
  rerunAgentRun: (id) => request(`/agent/runs/${id}/rerun`, { method: 'POST' }),
  rateAgentDecision: (id, rating) => request(`/agent/decisions/${id}/rate`, { method: 'POST', body: JSON.stringify({ rating }) }),
  getAgentStats: () => request('/agent/stats'),
```

（`request` wrapper 的实际调用形态以 client.js 既有方法为准——若既有方法用 `request(path, 'PUT', body)` 之类签名，跟随之。）

- [ ] **Step 2: 路由 + 导航**

router/index.js：`import Agent from '../views/Agent.vue'`，routes 中 `/channels` 之前插入 `{ path: '/agent', component: Agent }`。
App.vue navItems：对应位置插入 `{ path: '/agent', label: 'Agent 复盘', icon: ... }`（icon 用内联 SVG，仿相邻项，如机器人/脑形图标）。
Agent.vue 占位：page-header + "加载中"。

- [ ] **Step 3: 构建验证**

Run: `cd frontend && npm run build`
Expected: 构建成功，无 ESLint/编译错误

- [ ] **Step 4: Commit** `feat(agent-ui): route, nav, api client methods`

### Task 17: Agent.vue 复盘页

**Files:**
- Modify: `frontend/src/views/Agent.vue`
- Modify: `frontend/src/views/Tasks.vue`（任务表单 actions 复选追加 `agent_decide`／"Agent 裁决"）

页面结构（参考 Channels.vue 的卡片 + history-table 模式、Trade.vue 的轮询模式）：

1. **runs 列表卡片**：表格列 = 时间 / 任务名 / 状态徽章（queued·running·done·failed）/ 模型 / tokens（in+out）/ 裁决数 / 操作（查看、重跑）。默认 10s countdown 轮询（复用 1s setInterval 递减模式，onUnmounted 清理）。
2. **run 明细抽屉/区块**（点击行展开）：
   - 每条裁决一张卡片：`symbol @timeframe` + 方向徽章（📈 long=clr-positive / 📉 short=clr-negative / skip=muted）+ confidence + reasons 全文 + factors（若有，键值对小表）。
   - **outcome 列**：1h/4h/24h 变化，方向感知染色——`(direction==='long') === (change>0)` 为绿否则红；skip 或无数据显示 `-`。
   - **人工评分**：三个小按钮 👍(1) 👎(0) ❓(-1)，点击调 `rateAgentDecision`，当前值高亮。
   - **采纳按钮**（direction 非 skip 时显示）：`router.push({ path: '/trade', query: { symbol: d.symbol, direction: d.direction } })`。
   - **工具轨迹折叠面板**：`<details>` 内按序渲染 trace.steps（tool 名 + args + result 截断文本），另显示 trace.reused（"复用裁决 #id"）。
   - failed run 显示 error 与"重跑"按钮（调 rerunAgentRun 后刷新列表）。
3. **空态**：无 runs 时提示"在任务的 actions 中勾选『Agent 裁决』并在设置页启用 Agent"。

- [ ] **Step 1: 实现页面**（完整 SFC；script setup + ref/onMounted/onUnmounted，样式 scoped 用 CSS 变量）
- [ ] **Step 2: Tasks.vue 表单 actions 数组追加** `{ value: 'agent_decide', label: 'Agent 裁决' }`（找到现有 text_summary/chart_shot 复选组照抄结构）
- [ ] **Step 3: 构建验证** — `cd frontend && npm run build` → 成功
- [ ] **Step 4: 手动走查**（可选但推荐）：`docker-compose up` 或后端 `python main.py` + 前端 `npm run dev`，插入一条假 run（sqlite3 手动 INSERT）确认列表/明细/评分/轨迹渲染正常
- [ ] **Step 5: Commit** `feat(agent-ui): review page — runs, verdicts, outcomes, rating, trace`

### Task 18: Trade 页预填（采纳跳转的落点）

**Files:**
- Modify: `frontend/src/views/Trade.vue`（onMounted 读取 route.query）
- Modify: `frontend/src/components/TradeForm.vue`（defineExpose 增加 setDirection）

- [ ] **Step 1: TradeForm.vue** 在 defineExpose 处（applyPlan 旁）暴露：

```javascript
function setDirection(dir) {
  // TradeForm 内部状态字段是 side（'BUY'/'SELL'）；long/short 只出现在 emit 的 payload 里
  if (dir === 'long') form.side = 'BUY'
  else if (dir === 'short') form.side = 'SELL'
}
// defineExpose({ applyPlan, setDirection })  —— 按现有 expose 对象合并
```

（`form.side` 的实际字段名/结构以 TradeForm.vue 现状为准。）

- [ ] **Step 2: Trade.vue** onMounted 末尾追加：

```javascript
import { useRoute } from 'vue-router'   // 若未引入
const route = useRoute()
// onMounted 内：
if (route.query.symbol) {
  symbol.value = String(route.query.symbol)   // 实际 symbol ref 名以 Trade.vue 现状为准
  await loadAll()
}
if (route.query.direction && tradeFormRef.value) {
  tradeFormRef.value.setDirection(String(route.query.direction))
}
```

- [ ] **Step 3: 构建验证** — `cd frontend && npm run build` → 成功；手动访问 `/trade?symbol=BTCUSDT&direction=short` 确认预填
- [ ] **Step 4: Commit** `feat(agent-ui): /trade accepts symbol+direction prefill from review page`

### Task 19: Settings 页 Agent 配置区

**Files:**
- Modify: `frontend/src/views/Settings.vue`

- [ ] **Step 1: 新增"Agent 配置"卡片**（仿交易凭据卡片的结构与样式）：
  - 字段：启用开关、provider 下拉（openai/anthropic）、base_url（provider=openai 时显示）、model、api_key（type=password，placeholder 依 `has_api_key` 显示"已保存（留空不修改）"）、max_tokens、max_tool_calls、deep_dive_limit、cooldown_minutes、credential_id（下拉，选项来自 `api.listTradingCredentials()` + "不使用"）、push_verdict 开关。
  - 保存调 `api.updateAgentConfig`（api_key 为空串时发送 null）。
  - 若返回的 `insecure_defaults` 非空，显示警告条："SECRET_KEY/APP_PASSWORD 为默认值——密钥加密形同虚设，且轮换 SECRET_KEY 会作废已存密钥"。
- [ ] **Step 2: 构建验证** — `cd frontend && npm run build` → 成功
- [ ] **Step 3: Commit** `feat(agent-ui): agent config section in Settings`

---

## Phase 4 — 闭环量化 + 验证器接口

### Task 20: /agent/stats 方向感知统计

**Files:**
- Modify: `backend/api/agent.py`
- Test: `backend/tests/test_agent_stats.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_stats.py
import os, pytest
from database import get_db


def _seed_decision(decider, direction, confidence, change_4h):
    db = get_db(os.environ["DB_PATH"])
    cur = db.execute("INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
                     "VALUES (NULL, 'BTCUSDT', 'Binance', 'x', '1h')")
    sid = cur.lastrowid
    db.execute("INSERT INTO outcomes (signal_id, change_4h) VALUES (?, ?)", (sid, change_4h))
    cur = db.execute("INSERT INTO agent_runs (decider, status) VALUES (?, 'done')", (decider,))
    db.execute("INSERT INTO agent_decisions (run_id, signal_id, symbol, timeframe, direction, confidence, reasons) "
               "VALUES (?, ?, 'BTCUSDT', '1h', ?, ?, '')",
               (cur.lastrowid, sid, direction, confidence))
    db.commit()
    db.close()


@pytest.mark.asyncio
async def test_stats_direction_aware(client):
    _seed_decision("agent", "long", 0.8, +3.0)    # long×涨 = win
    _seed_decision("agent", "short", 0.8, +3.0)   # short×涨 = loss
    _seed_decision("agent", "skip", 0.2, +3.0)    # skip 不计
    _seed_decision("rule", "long", None, -1.0)    # rule 基线，loss
    async with client as c:
        body = (await c.get("/api/agent/stats")).json()
    agent_hi = next(g for g in body["groups"]
                    if g["decider"] == "agent" and g["bucket"] == ">=0.7" and g["horizon"] == "4h")
    assert agent_hi["n"] == 2 and agent_hi["wins"] == 1 and abs(agent_hi["win_rate"] - 0.5) < 1e-6
    assert agent_hi["reliable"] is False          # n < 20
    rule = next(g for g in body["groups"] if g["decider"] == "rule" and g["horizon"] == "4h")
    assert rule["n"] == 1 and rule["wins"] == 0
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 实现 stats 端点**

```python
MIN_RELIABLE_N = 20   # 沿用量化层 P1 纪律：样本不足的格子显式标注


@router.get("/stats")
def stats():
    """方向感知胜率：long 赢=涨，short 赢=跌；skip 不计。
    按 decider × confidence 桶 × horizon 聚合；rule 基线 confidence 为 NULL，单独成桶。"""
    db = get_db(settings.db_path)
    rows = db.execute(
        """SELECT r.decider, d.direction, d.confidence,
                  o.change_1h, o.change_4h, o.change_24h
           FROM agent_decisions d
           JOIN agent_runs r ON r.id = d.run_id
           LEFT JOIN outcomes o ON o.signal_id = d.signal_id
           WHERE d.direction != 'skip' AND d.signal_id IS NOT NULL""").fetchall()
    db.close()

    def bucket(conf):
        if conf is None:
            return "rule"
        if conf >= 0.7:
            return ">=0.7"
        if conf >= 0.5:
            return "0.5-0.7"
        return "<0.5"

    acc = {}   # (decider, bucket, horizon) -> [n, wins, sum_signed]
    for r in rows:
        b = bucket(r["confidence"])
        sign = 1 if r["direction"] == "long" else -1
        for h in ("1h", "4h", "24h"):
            chg = r[f"change_{h}"]
            if chg is None:
                continue
            key = (r["decider"], b, h)
            n, w, s = acc.get(key, (0, 0, 0.0))
            acc[key] = (n + 1, w + (1 if sign * chg > 0 else 0), s + sign * chg)

    groups = [{"decider": k[0], "bucket": k[1], "horizon": k[2],
               "n": v[0], "wins": v[1],
               "win_rate": round(v[1] / v[0], 4) if v[0] else None,
               "avg_signed_change": round(v[2] / v[0], 4) if v[0] else None,
               "reliable": v[0] >= MIN_RELIABLE_N}
              for k, v in sorted(acc.items())]
    return {"groups": groups, "min_reliable_n": MIN_RELIABLE_N}
```

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest tests/test_agent_stats.py -v` → PASS

- [ ] **Step 5: Commit** `feat(agent): direction-aware stats API with rule baseline`

### Task 21: 复盘页统计卡片

**Files:**
- Modify: `frontend/src/views/Agent.vue`

- [ ] **Step 1: 页面顶部加"决策质量"卡片**：表格 decider×bucket 行、1h/4h/24h 胜率列（`win_rate` 百分比显示；`reliable === false` 的格子加灰色角标"样本不足"）；`avg_signed_change` 副行小字。onMounted 调 `getAgentStats`。agent 行与 rule 行相邻排列以便肉眼对比。
- [ ] **Step 2: 构建验证** — `cd frontend && npm run build` → 成功
- [ ] **Step 3: Commit** `feat(agent-ui): decision-quality stats card (agent vs rule baseline)`

### Task 22: StrategyValidator 接口 stub

**Files:**
- Create: `backend/agent/validator.py`
- Test: `backend/tests/test_agent_validator.py`

- [ ] **Step 1: 写失败测试**

```python
# backend/tests/test_agent_validator.py
def test_validator_protocol_shape():
    from agent.validator import StrategyValidator, ValidationReport, NullValidator
    v: StrategyValidator = NullValidator()
    report = v.validate({"name": "x", "rules": []})
    assert isinstance(report, ValidationReport)
    assert report.verdict == "not_validated"
```

- [ ] **Step 2: 运行确认失败** — FAIL

- [ ] **Step 3: 实现 validator.py**

```python
# backend/agent/validator.py
"""StrategyValidator 接口（本轮只留缝，不实现）。

数据契约：任何要"上升为策略"的逻辑（固化的 prompt 版本、阈值规则、
factor 组合）在启用前必须过一个 StrategyValidator 实现——未来由量化层 P3
（backend/quant/backtest.py 的 walk-forward）或外部项目提供。多重检验校正
（试了多少次必须计入）是验证器实现方的责任，不是调用方的。

strategy_spec 契约（dict）:
    name: str                     策略名
    prompt_version: str | None    若为 prompt 固化
    rules: list                   结构化规则/因子描述（由实现方定义粒度）
    sample_window: str | None     声明的样本窗口
verdict ∈ {'pass', 'fail', 'not_validated'}
"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ValidationReport:
    verdict: str                  # pass | fail | not_validated
    detail: str = ""
    metrics: dict = field(default_factory=dict)


class StrategyValidator(Protocol):
    def validate(self, strategy_spec: dict) -> ValidationReport: ...


class NullValidator:
    """占位实现：显式拒绝背书。存在的意义是让调用方今天就能写依赖注入代码。"""

    def validate(self, strategy_spec: dict) -> ValidationReport:
        return ValidationReport(
            verdict="not_validated",
            detail="尚无验证器实现——策略逻辑未经 walk-forward 验证，不得视为已确认",
        )
```

- [ ] **Step 4: 运行确认通过** — `cd backend && python -m pytest tests/test_agent_validator.py -v` → PASS

- [ ] **Step 5: Commit** `feat(agent): StrategyValidator interface stub (validation deferred to quant P3)`

### Task 23: 文档 + 全量验证收尾

**Files:**
- Modify: `CLAUDE.md`（Key directories 加 `backend/agent/`；Database 表清单加 4 张新表；Task execution flow 提 agent_decide/队列/worker；Conventions 提工具节流与只读红线）

- [ ] **Step 1: 更新 CLAUDE.md**（照上述要点，简洁）
- [ ] **Step 2: 后端全量测试** — `cd backend && python -m pytest -m "not network" -q` → 全部 PASS
- [ ] **Step 3: 前端构建** — `cd frontend && npm run build` → 成功
- [ ] **Step 4: 冒烟验证**（本地 `python main.py` 启动）：
  - 启动日志无异常；`GET /api/agent/config` 返回默认配置
  - sqlite3 检查 `outcome_checks`、`agent_config`、`agent_runs`、`agent_decisions` 表存在
  - 配置一个假 LLM（base_url 指向不可达地址）→ 建任务勾选 agent_decide → `/test` 触发 → agent_runs 出现 queued→failed（错误可读），复盘页可见失败与重跑按钮
- [ ] **Step 5: Commit** `docs: document agent decision layer in CLAUDE.md`

---

## 执行注意事项（给实施者）

1. **顺序严格按 Task 1→23**：后续任务依赖前面的表/模块/签名。
2. **PydanticAI 版本漂移**是最大的外部不确定性——Task 7 的版本检查步骤是硬性的；`FunctionModel`/`usage()` 字段名/`ToolCallPart` 构造以安装版本文档为准，计划中的**断言语义**（安全阀丢弃批外符号、冷却复用不调 LLM、trace 落库）不变。
3. **红线自查**（每个 agent 相关任务提交前）：`grep -rn "place_order_bracket\|close_all\|place_order" backend/agent/` 只允许出现在注释和 `position_plan_preview` 的 `build_position_plan` import 行；`backend/agent/` 不得 import `binance_client`。
4. **不修的已知技债**（勿顺手改动，另立任务）：push_logs channel_id 恒 NULL、假 success；anomaly_watch 不落库；`_last_results` 无锁。
5. 手动测试端点 `/api/tasks/{id}/test` 在 Phase 0 后行为变化：多周期任务不再产生叉乘重复信号——若既有测试依赖旧行为，修测试而非实现。
