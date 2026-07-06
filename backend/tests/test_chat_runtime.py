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


def test_cancel_inside_tool_blocks_next_tool():
    """第一个工具执行完成后请求取消 → 第二个工具调用的 pre-exec 检查点必须拦截。

    适配说明：规格草稿里让模型在同一次响应中通过两个 DeltaToolCall 一次性
    发出两个工具调用，意图是"顺序执行、第一个的副作用能在第二个开始前生效"。
    但 pydantic-ai 1.107.0 中同响应内的多个工具调用默认以
    parallel_execution_mode='parallel' 执行（backend 用 run_in_executor 把同步
    工具函数丢进线程池），两个调用可能在不同线程里几乎同时开始，先后顺序不受
    保证——会让这个断言变成竞态。改为让模型分两轮 model_request 顺序发出两次
    工具调用（agent.iter 的 tool 节点天然顺序执行，不存在并行歧义），断言契约
    （恰好 1 个 tool_start、终态 cancelled、trace 带 prompt_version）不变。
    """
    sid, mid, row = _prep("对比两个币")
    calls = {"n": 0}

    async def stream_fn(messages, info: AgentInfo):
        calls["n"] += 1
        if calls["n"] == 1:
            yield {0: DeltaToolCall(name="get_market_snapshot",
                                    json_args='{"symbols": ["BTCUSDT"]}')}
        elif calls["n"] == 2:
            yield {0: DeltaToolCall(name="get_market_snapshot",
                                    json_args='{"symbols": ["ETHUSDT"]}')}
        else:
            yield "不应到达这里"

    from unittest.mock import patch

    def snap_and_cancel(symbols):
        store.request_cancel(row["id"])
        return {symbols[0]: {"lastPrice": 1}}

    with patch("agent.tools.market_snapshot", side_effect=snap_and_cancel):
        runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))
    types = _types(row["id"])
    assert types[-1] == "cancelled"
    assert types.count("tool_start") == 1          # 第二个工具在 emit 前就被拦截
    msgs = store.list_messages(sid)
    assert msgs[-1]["error"] == "cancelled"
    assert msgs[-1]["trace"]["prompt_version"]      # Finding 4 的回归断言


def test_cancel_between_stream_chunks():
    """流式块之间的检查点：第二块 flush 后必须触发取消，且已出文本保留。

    第一个真实块（"开场白"）经 PartStartEvent 携带的 TextPart 进入 buf——
    `_drive()` 现在同时订阅 PartStartEvent(TextPart) 与
    PartDeltaEvent(TextPartDelta)，因此不再需要占位块把 TextPart "开起来"。
    """
    sid, mid, row = _prep("讲个长故事")

    async def stream_fn(messages, info: AgentInfo):
        yield "开场白"                               # PartStartEvent，首块文本，必须进入 buf
        yield "甲" * 500                            # PartDeltaEvent，超过 FLUSH_CHARS，flush + 检查（未取消）
        store.request_cancel(row["id"])
        yield "乙" * 500                            # flush 后检查点触发 TurnCancelled

    runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))
    types = _types(row["id"])
    assert types[-1] == "cancelled"
    last = store.list_messages(sid)[-1]
    assert "开场白" in last["content"]               # 首块文本已持久化
    assert "甲" in last["content"]                  # 部分文本已持久化


def test_first_stream_chunk_not_lost():
    """PartStartEvent 携带的首块文本必须进入缓冲——真实 provider 的每轮回复开头。"""
    sid, mid, row = _prep("你好")

    async def stream_fn(messages, info: AgentInfo):
        yield "首块"
        yield "，后续"

    runtime.run_turn(row, model_override=FunctionModel(stream_function=stream_fn))
    last = store.list_messages(sid)[-1]
    assert last["content"] == "首块，后续"
    assert last["error"] is None
