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
