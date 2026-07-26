"""runner 测试：金标实跑走 FunctionModel（零网络、零 LLM 费用），
离线打分走真实 store。验证的是评测管道本身。"""
import pytest
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from agent.chat import store
from evals import runner
from evals.cases import EvalCase


def _fm(tool_name: str, json_args: str, answer: str):
    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name=tool_name, json_args=json_args)}
        else:
            yield answer
    return FunctionModel(stream_function=stream_fn)


def test_run_case_scores_scripted_flow():
    case = EvalCase(id="t-overview", question="今天市场整体什么情况？",
                    must_call=["market_overview"],
                    must_not_call=["run_screener_scan"],
                    answer_rules=["nonempty_conclusion", "cn_language",
                                  "numeric_evidence", "no_execution_claim"])
    model = _fm("get_market_overview", '{"top_n": 5}',
                "结论：市场整体偏强，涨幅榜第一 EULUSDT +63.22%，跌幅榜 DEXEUSDT -44.40%。")
    r = runner.run_case(case, model)
    assert r.l1["score"] == 1.0
    assert r.l2["score"] == 1.0 and r.l2["n_calls"] == 1
    assert r.l3["score"] == 1.0
    assert r.total == 1.0
    assert r.l1["tools_called"] == ["market_overview"]   # trace 用内部名


def test_run_case_catches_forbidden_tool():
    case = EvalCase(id="t-forbidden", question="看下 BTCUSDT",
                    must_not_call=["run_screener_scan"])
    model = _fm("run_screener_scan",
                '{"screener_keys": ["oscillator/divergence_bottom"], '
                '"timeframes": ["1h"], "watchlist_id": 78201040}',
                "扫完了。")
    r = runner.run_case(case, model)
    assert r.l1["score"] == 0.0
    assert any("禁用工具" in v for v in r.l1["violations"])


def test_run_case_uses_fixtures_not_network():
    """fixtures 生效的证据：market_snapshot 返回固定价格 64375.1。"""
    captured = {}

    async def stream_fn(messages, info: AgentInfo):
        if len(messages) == 1:
            yield {0: DeltaToolCall(name="get_market_snapshot",
                                    json_args='{"symbols": ["BTCUSDT"]}')}
        else:
            captured["prompt_len"] = len(str(messages))
            yield "BTCUSDT 现价 64375.1，24h +0.95%。"

    case = EvalCase(id="t-fixture", question="BTC 现价？",
                    must_call=["market_snapshot"])
    r = runner.run_case(case, FunctionModel(stream_function=stream_fn))
    assert r.l1["score"] == 1.0
    assert "64375.1" in r.answer


def test_run_case_does_not_touch_chat_tables():
    before = len(store.list_sessions())
    case = EvalCase(id="t-isolation", question="市场如何？")
    runner.run_case(case, _fm("get_market_overview", "{}", "一切正常。"))
    assert len(store.list_sessions()) == before


def test_run_golden_isolates_case_crash():
    class Boom:
        pass  # 非法 model 对象 → run_case 内部崩溃

    results = runner.run_golden(Boom(), case_ids=["market-overview"])
    assert len(results) == 1
    assert results[0].total == 0.0
    assert "运行异常" in str(results[0].l1.get("violations"))


# ---- 离线打分 ----

def test_score_stored_buckets_by_prompt_version():
    sid = store.create_session()
    store.add_message(sid, "user", "看下 BTC")
    store.add_message(
        sid, "assistant",
        "结论：BTCUSDT 短线偏多。RSI 57.3 中性偏强，MACD 金叉后柱体扩大，"
        "下方枢轴 63590 未破，结构完好。",
        trace={"prompt_version": "chat-v1",
               "steps": [{"tool": "get_indicators",
                          "args": {"symbol": "BTCUSDT"},
                          "result": '{"ok": true}'}]},
        model="gpt-x", input_tokens=100, output_tokens=50)
    store.add_message(
        sid, "assistant", "已帮你下单做多。",       # 违反红线的坏样本
        trace={"prompt_version": "chat-v2", "steps": []},
        model="gpt-x")

    rows = runner.score_stored()
    assert len(rows) == 2
    by_ver = {r["prompt_version"]: r for r in rows}
    assert by_ver["chat-v1"]["l3"] == 1.0
    assert by_ver["chat-v2"]["rules"]["no_execution_claim"] is False

    from evals import report
    summary = report.summarize_stored(rows)
    assert ("chat-v1", "gpt-x") in summary
    assert summary[("chat-v2", "gpt-x")]["rule_failures"]["no_execution_claim"] == 1
    md = report.render_stored_markdown(summary, len(rows))
    assert "chat-v1" in md and "no_execution_claim" in md


def test_score_stored_empty_db():
    assert runner.score_stored() == []
