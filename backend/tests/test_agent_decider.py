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
    """FunctionModel 构造精确输出：symbol/timeframe 命中候选 → 落库并关联 signal_ids；批外符号丢弃。"""
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from agent.agent_decider import run_agent_on_context
    from agent.queue import enqueue_run, claim_next

    def make_output(messages, info):
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
